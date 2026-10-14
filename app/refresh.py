"""Poll every active company's job board and sync the results into the jobs table.

Run:  python -m app.refresh                  # all active companies
      python -m app.refresh --company micron # only companies whose name matches

Per company:
  1. connector.fetch()          -> open postings (Workday asks for area locations only)
  2. location filter            -> drop anything outside the Treasure Valley (app/location.py)
  3. fetch_details()            -> only for in-area postings we've never stored a description for
  4. upsert                     -> new rows get first_seen_at; all seen rows get last_seen_at;
                                   every row gets company_name and a relevance label
  5. close                      -> rows not seen this run get closed_at
Each company is its own transaction, so one failing board doesn't affect the others.
"""

import argparse
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.connectors import CONNECTORS, Connector, JobPosting
from app.db import SessionLocal
from app.location import in_area, is_unresolved
from app.models import Company, ExcludedPosting, Job
from app.relevance import classify

log = logging.getLogger("refresh")

CHUNK = 500
COMPANY_WORKERS = 4  # companies refreshed in parallel


def refresh_company(session: Session, connector: Connector, company: Company) -> dict:
    run_at = datetime.now(timezone.utc)
    fetched = connector.fetch(company)

    # external_id -> has a stored description?
    known = dict(
        session.execute(
            select(Job.external_id, Job.description_html.is_not(None)).where(
                Job.company_id == company.id
            )
        ).all()
    )

    # Postings already checked on an earlier run and found to be outside the area.
    excluded = set(
        session.scalars(select(ExcludedPosting.external_id).where(ExcludedPosting.company_id == company.id))
    )

    # Location filter, BEFORE any detail requests or writes. Keep postings in the area,
    # plus ones whose location is hidden ("3 Locations") that we haven't already ruled out:
    # those are checked after details.
    postings = [
        p for p in fetched
        if in_area(p.location, company.location_aliases)
        or (is_unresolved(p.location) and p.external_id not in excluded)
    ]

    needs_details = [p for p in postings if p.description_html is None and not known.get(p.external_id)]
    failed = 0
    if needs_details:
        log.info("  %s: fetching details for %d postings", company.name, len(needs_details))

        def detail(p: JobPosting) -> JobPosting:
            nonlocal failed
            try:
                return connector.fetch_details(company, p)
            except httpx.HTTPError as e:
                failed += 1
                log.warning("  detail failed for %s: %s", p.external_id, e)
                return p

        with ThreadPoolExecutor(connector.detail_workers) as pool:
            list(pool.map(detail, needs_details))

    # Second pass: details revealed the real locations of the hidden ones. A stored job
    # whose details we skipped still shows "3 Locations" here; it passed when first stored.
    postings = [
        p for p in postings
        if in_area(p.location, company.location_aliases) or (p.external_id in known and p.description_html is None)
    ]

    # Remember postings whose details showed they're out of area, so the next run skips
    # them. (Failed detail requests have no description and are simply retried next time.)
    kept = {p.external_id for p in postings}
    rejected = [p for p in needs_details if p.external_id not in kept and p.description_html is not None]
    _sync_excluded(session, company, fetched, excluded, rejected, run_at)

    # Postings we have full data for get a full upsert; already-stored postings whose
    # details we skipped only get marked as seen (so we don't overwrite good data with stubs).
    full = [p for p in postings if p.description_html is not None or p.external_id not in known]
    seen_only = [p.external_id for p in postings if p.description_html is None and p.external_id in known]

    for i in range(0, len(full), CHUNK):
        rows = []
        for p in full[i : i + CHUNK]:
            relevance, reason = classify(p.title, p.description_html)
            rows.append({
                "company_id": company.id,
                "company_name": company.name,
                "source": company.source,
                "external_id": p.external_id,
                "title": p.title,
                "url": p.url,
                "location": p.location,
                "remote": p.remote,
                "department": p.department,
                "employment_type": p.employment_type,
                "description_html": p.description_html,
                "posted_at": p.posted_at,
                "content_hash": p.content_hash(),
                "raw": p.raw,
                "relevance": relevance,
                "relevance_reason": reason,
                "first_seen_at": run_at,
                "last_seen_at": run_at,
            })
        stmt = insert(Job).values(rows)
        update_cols = {
            c: stmt.excluded[c]
            for c in (
                "company_name", "title", "url", "location", "remote", "department",
                "employment_type", "description_html", "posted_at", "content_hash", "raw",
                "relevance", "relevance_reason", "last_seen_at",
            )
        }
        # first_seen_at is deliberately NOT updated: it records when we first saw the job.
        stmt = stmt.on_conflict_do_update(
            index_elements=["source", "external_id"],
            set_={**update_cols, "closed_at": None},  # reappeared -> reopen
        )
        session.execute(stmt)

    for i in range(0, len(seen_only), CHUNK):
        session.execute(
            update(Job)
            .where(Job.company_id == company.id, Job.external_id.in_(seen_only[i : i + CHUNK]))
            .values(last_seen_at=run_at, closed_at=None, company_name=company.name)
        )

    closed = session.execute(
        update(Job)
        .where(Job.company_id == company.id, Job.closed_at.is_(None), Job.last_seen_at < run_at)
        .values(closed_at=run_at)
    ).rowcount

    stats = {
        "fetched": len(fetched),
        "open": len(postings),
        "new": sum(1 for p in postings if p.external_id not in known),
        "closed": closed,
        "detail_failures": failed,
    }
    # Health snapshot for the Companies page.
    company.last_fetched_at = run_at
    company.last_success_at = run_at
    company.last_error = None
    company.last_fetched = stats["fetched"]
    company.last_in_area = stats["open"]
    company.last_new = stats["new"]
    company.last_closed = stats["closed"]
    return stats


def _sync_excluded(session, company, fetched, excluded, rejected, run_at) -> None:
    """Record newly rejected postings, mark still-listed ones as seen, prune ones that are gone."""
    if rejected:
        stmt = insert(ExcludedPosting).values([
            {"source": company.source, "external_id": p.external_id, "company_id": company.id,
             "location": p.location, "last_seen_at": run_at}
            for p in rejected
        ])
        session.execute(stmt.on_conflict_do_update(
            index_elements=["source", "external_id"], set_={"last_seen_at": run_at}))
    still_listed = [p.external_id for p in fetched if p.external_id in excluded]
    for i in range(0, len(still_listed), CHUNK):
        session.execute(
            update(ExcludedPosting)
            .where(ExcludedPosting.company_id == company.id,
                   ExcludedPosting.external_id.in_(still_listed[i : i + CHUNK]))
            .values(last_seen_at=run_at)
        )
    session.execute(
        delete(ExcludedPosting).where(
            ExcludedPosting.company_id == company.id, ExcludedPosting.last_seen_at < run_at)
    )


def run_refresh(company_filter: str | None = None, company_ids: list[int] | None = None) -> list[dict]:
    """Refresh every active company, or only those matching a name filter / list of IDs.
    Used by the CLI and the web UI.

    Returns one result dict per company: {"company", "ok", "error", and the refresh stats}.
    """
    with SessionLocal() as session:
        query = select(Company).where(Company.active).order_by(Company.name)
        if company_filter:
            query = query.where(Company.name.ilike(f"%{company_filter}%"))
        if company_ids is not None:
            query = query.where(Company.id.in_(company_ids))
        companies = session.scalars(query).all()

    headers = {"User-Agent": "JobScraper personal job tracker", "Accept": "application/json"}
    with httpx.Client(headers=headers, timeout=30, follow_redirects=True) as client:
        # Companies are independent (own transaction, own ATS host), so refresh a few at once.
        with ThreadPoolExecutor(COMPANY_WORKERS) as pool:
            results = [r for r in pool.map(lambda c: _refresh_one(c, client), companies) if r]
    return results


def _refresh_one(company: Company, client: httpx.Client) -> dict | None:
    name = company.name  # captured up front: a rollback expires the ORM object
    connector_cls = CONNECTORS.get(company.source)
    if connector_cls is None:
        log.info("%-40s skipped (no %s connector yet)", name, company.source)
        return None
    try:
        with SessionLocal.begin() as session:
            stats = refresh_company(session, connector_cls(client), session.merge(company))
        log.info(
            "%-40s fetched=%-5d in_area=%-5d new=%-5d closed=%-4d%s",
            name, stats["fetched"], stats["open"], stats["new"], stats["closed"],
            f" detail_failures={stats['detail_failures']}" if stats["detail_failures"] else "",
        )
        return {"company": name, "ok": True, "error": None, **stats}
    except Exception as e:  # one broken board shouldn't stop the rest
        log.error("%-40s FAILED: %r", name, e)
        # The refresh transaction rolled back; record the failure in a fresh one.
        with SessionLocal.begin() as session:
            session.execute(
                update(Company).where(Company.id == company.id)
                .values(last_error=repr(e)[:2000], last_error_at=datetime.now(timezone.utc))
            )
        return {"company": name, "ok": False, "error": repr(e)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh job postings from company ATS boards.")
    parser.add_argument("--company", help="only refresh companies whose name contains this")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # hide per-request lines
    run_refresh(args.company)


if __name__ == "__main__":
    main()


