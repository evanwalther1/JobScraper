"""Discovery radar: find Boise-area employers we don't track yet, and measure coverage.

Run:  python -m app.radar                # pull Adzuna postings + detect ATS for new companies
      python -m app.radar --no-detect    # just pull postings
      python -m app.radar --detect-only  # just detect (no Adzuna requests)

How it works:
  1. Ask Adzuna (an aggregator with an official API) for recent tech postings near Boise.
  2. Group them by company. Each company name is normalized to a key ("Micron Technology,
     Inc." -> "micron") and matched against the companies we track.
  3. For untracked companies with relevant local postings: look up their domain, then run
     the ATS detector (app/detect_ats.py) so they can be added with one click.
  4. Coverage = share of relevant local postings whose company we track.

Adzuna postings are never shown as jobs. The real posting always comes from the company's own
ATS, which keeps the jobs table free of aggregator duplicates.
Adzuna's free tier is limited (roughly 25 requests/minute, 250/day), so a run is capped.
"""

import argparse
import difflib
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.connectors import CONNECTORS
from app.db import SessionLocal
from app.detect_ats import detect
from app.location import in_area
from app.models import Company, RadarCompany, RadarPosting, Relevance
from app.relevance import classify

load_dotenv()
log = logging.getLogger("radar")

ADZUNA_URL = "https://api.adzuna.com/v1/api/jobs/us/search/{page}"
WHERE = "Boise, Idaho"
DISTANCE_KM = 50  # ~30 miles: Meridian, Nampa, Caldwell, Eagle, Star, Kuna
MAX_DAYS_OLD = 30
PAGE_SIZE = 50
MAX_PAGES_PER_QUERY = 20  # 2 queries x 20 pages = at most 40 requests per run
REQUEST_GAP_S = 2.6  # stay under ~25 requests/minute
QUERIES = [
    {"category": "it-jobs"},
    # Software roles Adzuna files under Engineering or elsewhere.
    {"what_or": "software developer programmer firmware cybersecurity devops"},
]
SUPPORTED_ATS = {s.value for s in CONNECTORS}
DETECT_WORKERS = 10

# Words that don't help identify a company ("Inc", "LLC", "Technologies", ...).
_SUFFIXES = {
    "inc", "incorporated", "llc", "llp", "lp", "ltd", "limited", "corp", "corporation", "co",
    "company", "companies", "plc", "group", "holdings", "the", "technology", "technologies",
    "systems", "solutions", "services", "usa", "us", "na", "international",
}


_REMOTE = re.compile(r"\b(remote|telework|teleworking|work from home|wfh|work from anywhere|virtual)\b", re.IGNORECASE)


def looks_remote(title: str, description: str | None) -> bool:
    return bool(_REMOTE.search(title) or _REMOTE.search(description or ""))


def normalize_name(name: str) -> str:
    """'Micron Technology, Inc.' -> 'micron'. Shared key for radar and tracked companies."""
    words = re.sub(r"[^a-z0-9 ]+", " ", name.lower().replace("&", " and ")).split()
    while len(words) > 1 and words[-1] in _SUFFIXES:
        words.pop()
    while len(words) > 1 and words[0] == "the":
        words.pop(0)
    return " ".join(words)


def tracked_keys(session: Session) -> dict[str, int]:
    """Normalized name -> company_id for every tracked company.
    "Kount (Equifax)" registers both "kount" and "equifax"."""
    keys: dict[str, int] = {}
    for company_id, name in session.execute(select(Company.id, Company.name)):
        main, _, alt = name.partition("(")
        for part in (main, alt.rstrip(")")):
            if part.strip():
                keys.setdefault(normalize_name(part), company_id)
    return keys


def _location(result: dict) -> str:
    """Adzuna area ["US", "Idaho", "Ada County", "Meridian"] -> "Meridian, Idaho"."""
    area = (result.get("location") or {}).get("area") or []
    if len(area) >= 3:
        return f"{area[-1]}, {area[1]}"
    return (result.get("location") or {}).get("display_name") or ""


def fetch_adzuna(client: httpx.Client) -> list[dict]:
    app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        raise RuntimeError("Set ADZUNA_APP_ID and ADZUNA_APP_KEY in .env")
    seen: dict[str, dict] = {}
    for query in QUERIES:
        for page in range(1, MAX_PAGES_PER_QUERY + 1):
            params = {
                "app_id": app_id, "app_key": app_key, "results_per_page": PAGE_SIZE,
                "where": WHERE, "distance": DISTANCE_KM, "max_days_old": MAX_DAYS_OLD,
                "sort_by": "date", "content-type": "application/json", **query,
            }
            resp = client.get(ADZUNA_URL.format(page=page), params=params)
            resp.raise_for_status()
            results = resp.json().get("results", [])
            for r in results:
                seen[str(r["id"])] = r
            time.sleep(REQUEST_GAP_S)
            if len(results) < PAGE_SIZE:
                break
    return list(seen.values())


def store_postings(session: Session, results: list[dict], run_at: datetime) -> None:
    companies: dict[str, str] = {}
    rows = []
    for r in results:
        name = ((r.get("company") or {}).get("display_name") or "").strip()
        if not name:
            continue
        key = normalize_name(name)
        companies.setdefault(key, name)
        location = _location(r)
        relevance, _ = classify(r.get("title", ""), r.get("description"))
        created = r.get("created")
        rows.append({
            "id": str(r["id"]), "company_key": key, "title": r.get("title", "").strip(),
            "location": location, "in_area": in_area(location), "relevance": relevance,
            "remote": looks_remote(r.get("title", ""), r.get("description")),
            "redirect_url": r.get("redirect_url"),
            "posted_at": datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None,
            "first_seen_at": run_at, "last_seen_at": run_at,
        })
    if companies:
        stmt = insert(RadarCompany).values(
            [{"key": k, "display_name": n, "first_seen_at": run_at, "last_seen_at": run_at} for k, n in companies.items()]
        )
        session.execute(stmt.on_conflict_do_update(
            index_elements=["key"], set_={"display_name": stmt.excluded.display_name, "last_seen_at": run_at}))
    for i in range(0, len(rows), 500):
        stmt = insert(RadarPosting).values(rows[i : i + 500])
        session.execute(stmt.on_conflict_do_update(
            index_elements=["id"],
            set_={c: stmt.excluded[c] for c in ("title", "location", "in_area", "remote", "relevance", "last_seen_at")},
        ))


def link_tracked(session: Session) -> int:
    """Link radar companies to tracked companies by normalized name. Returns links made."""
    keys = tracked_keys(session)
    linked = 0
    for rc in session.scalars(select(RadarCompany).where(RadarCompany.company_id.is_(None))):
        if rc.key in keys:
            rc.company_id = keys[rc.key]
            linked += 1
    return linked


def lookup_domain(client: httpx.Client, name: str) -> str | None:
    """Company name -> website domain, via Clearbit's free autocomplete endpoint.
    Picks the suggestion whose name is closest to ours (it can rank a namesake first)."""
    try:
        resp = client.get("https://autocomplete.clearbit.com/v1/companies/suggest", params={"query": name}, timeout=10)
        suggestions = resp.json() if resp.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return None
    if not suggestions:
        return None
    target = normalize_name(name)
    best = max(suggestions, key=lambda s: difflib.SequenceMatcher(None, target, normalize_name(s.get("name", ""))).ratio())
    return best.get("domain")


def detect_company(rc: RadarCompany, client: httpx.Client) -> None:
    """Fill in domain (if unknown) and ATS detection for one radar company."""
    if not rc.domain:
        rc.domain = lookup_domain(client, rc.display_name)
    if rc.domain:
        result = detect(rc.display_name, rc.domain, client)
        rc.detected_ats, rc.detected_slug, rc.detect_evidence = result.ats, result.slug, result.evidence
    rc.detected_at = datetime.now(timezone.utc)


def detect_new(client: httpx.Client) -> int:
    """Detect ATS for untracked, not-ignored companies with relevant local postings.
    Each company is saved as soon as it's done, so an interrupted run keeps its progress."""
    relevant_local = (
        select(RadarPosting.company_key)
        .where(RadarPosting.in_area, RadarPosting.remote.is_(False), RadarPosting.relevance == Relevance.MATCH)
        .distinct()
    )
    with SessionLocal() as session:
        keys = session.scalars(
            select(RadarCompany.key).where(
                RadarCompany.company_id.is_(None), RadarCompany.ignored.is_(False),
                RadarCompany.detected_at.is_(None), RadarCompany.key.in_(relevant_local),
            )
        ).all()
    log.info("detecting ATS for %d companies", len(keys))

    def one(key: str) -> None:
        with SessionLocal.begin() as session:
            rc = session.get(RadarCompany, key)
            detect_company(rc, client)
            log.info("  %-40s %s %s", rc.display_name, rc.detected_ats or "-", rc.detected_slug or "")

    with ThreadPoolExecutor(DETECT_WORKERS) as pool:
        list(pool.map(one, keys))
    return len(keys)


def latest_run(session: Session) -> datetime | None:
    return session.scalar(select(func.max(RadarPosting.last_seen_at)))


def coverage(session: Session) -> dict:
    """Coverage of the latest radar run: of the relevant, non-remote postings in the Treasure
    Valley, what share comes from companies we track? Ignored companies are left out."""
    run = latest_run(session)
    if run is None:
        return {"run_at": None}
    base = (
        select(RadarPosting, RadarCompany)
        .join(RadarCompany)
        .where(RadarPosting.last_seen_at == run, RadarPosting.in_area, RadarPosting.remote.is_(False))
    )
    rows = session.execute(base).all()
    relevant = [(p, c) for p, c in rows if p.relevance == Relevance.MATCH and not c.ignored]
    covered = [1 for _, c in relevant if c.company_id is not None]
    return {
        "run_at": run,
        "postings": len(rows),
        "relevant": len(relevant),
        "covered": len(covered),
        "pct": round(100 * len(covered) / len(relevant)) if relevant else None,
        "ignored": sum(1 for p, c in rows if p.relevance == Relevance.MATCH and c.ignored),
        "remote_skipped": session.scalar(
            select(func.count()).select_from(RadarPosting).where(
                RadarPosting.last_seen_at == run, RadarPosting.in_area, RadarPosting.remote)
        ),
    }


def run_radar(detect_ats: bool = True) -> dict:
    run_at = datetime.now(timezone.utc)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JobScraper-radar"}
    # Short timeouts: detection loads many company websites, and some stall.
    with httpx.Client(headers=headers, timeout=8, follow_redirects=True) as client:
        results = fetch_adzuna(client)
        with SessionLocal.begin() as session:
            store_postings(session, results, run_at)
            linked = link_tracked(session)
        detected = detect_new(client) if detect_ats else 0
    with SessionLocal() as session:
        cov = coverage(session)
    summary = {"postings": len(results), "linked": linked, "detected": detected, **cov}
    log.info("radar: %s", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover Boise-area employers via Adzuna.")
    parser.add_argument("--no-detect", action="store_true", help="skip ATS detection")
    parser.add_argument("--detect-only", action="store_true",
                        help="only detect ATS for companies already found (no Adzuna requests)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.detect_only:
        with httpx.Client(headers={"User-Agent": "Mozilla/5.0 JobScraper-radar"}, timeout=8, follow_redirects=True) as client:
            detect_new(client)
    else:
        run_radar(detect_ats=not args.no_detect)


if __name__ == "__main__":
    main()
