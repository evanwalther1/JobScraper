"""Figure out which ATS (applicant tracking system) a company uses.

  python -m app.detect_ats "Clearwater Analytics" cwan.com
  python -m app.detect_ats --csv data/treasure_valley_candidates.csv

How it works:
  1. Load the company's homepage, the careers-looking links on it, and common careers
     paths (/careers, /jobs, careers.<domain>, ...).
  2. Search all that HTML for links to known ATS platforms. The URL usually contains the
     company's ID on that platform (e.g. micron.wd1.myworkdayjobs.com/External).
  3. If nothing turns up, guess slugs from the name against the public JSON APIs.
Detection is a starting point. Confirm the result before adding a company to the seed list.
"""

import argparse
import csv
import difflib
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

# (ats, regex). Group 1 onward is the company's identifier on that platform.
ATS_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([\w-]+)", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/([\w.-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+)", re.I)),
    ("workday", re.compile(r"([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([\w-]+)", re.I)),
    ("recruitee", re.compile(r"([\w-]+)\.recruitee\.com", re.I)),
    ("bamboohr", re.compile(r"([\w-]+)\.bamboohr\.com", re.I)),
    ("icims", re.compile(r"([\w-]+)\.icims\.com", re.I)),
    # Captures the host too, so the slug is exactly what the UKG connector expects.
    ("ukg", re.compile(r"(recruiting2?\.ultipro\.com)/([\w]+)/JobBoard/([\w-]+)", re.I)),
    ("successfactors", re.compile(r"(career\d*\.successfactors\.(?:com|eu))/career\?company=([\w]+)|([\w-]+)\.jobs\.hr\.cloud\.sap", re.I)),
    # host + site number ("eofd.fa.us6.oraclecloud.com/CX_1"), the Oracle connector's slug format.
    ("oracle", re.compile(r"([\w-]+\.fa\.[\w-]+\.oraclecloud\.com)(?:/hcmUI/CandidateExperience/[\w-]+/sites/(CX_\d+))?", re.I)),
    ("taleo", re.compile(r"([\w-]+)\.taleo\.net", re.I)),
    ("jobvite", re.compile(r"jobs\.jobvite\.com/([\w-]+)", re.I)),
    ("paylocity", re.compile(r"recruiting\.paylocity\.com/recruiting/jobs/(?:All|Details)/([\w-]+)", re.I)),
    ("adp", re.compile(r"workforcenow\.adp\.com[^\"'\s]*?cid=([\w-]+)|myjobs\.adp\.com/([\w-]+)", re.I)),
    ("dayforce", re.compile(r"jobs\.dayforcehcm\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)", re.I)),
    ("paycom", re.compile(r"paycomonline\.net[^\"'\s]*?clientkey=([\w-]+)", re.I)),
    ("paycor", re.compile(r"recruitingbypaycor\.com[^\"'\s]*?clientId=([\w-]+)", re.I)),
    ("jazzhr", re.compile(r"([\w-]+)\.applytojob\.com", re.I)),
    ("applicantpro", re.compile(r"([\w-]+)\.applicantpro\.com", re.I)),
    ("neogov", re.compile(r"governmentjobs\.com/careers/([\w-]+)", re.I)),
    ("eightfold", re.compile(r"([\w-]+)\.eightfold\.ai", re.I)),
    ("rippling", re.compile(r"ats\.rippling\.com/([\w-]+)", re.I)),
    ("breezy", re.compile(r"([\w-]+)\.breezy\.hr", re.I)),
    ("teamtailor", re.compile(r"([\w-]+)\.teamtailor\.com", re.I)),
    ("phenom", re.compile(r"cdn\.phenompeople\.com|phenompro", re.I)),
]
# Subdomains that belong to the platform itself, not a company.
_NOT_A_COMPANY = {"www", "api", "cdn", "static", "assets", "app", "boards", "jobs", "careers", "help", "support", "images"}

CAREER_PATHS = ["/careers", "/careers/", "/jobs", "/about/careers", "/company/careers",
                "/about-us/careers", "/join-us", "/work-with-us", "/employment", "/careers/jobs"]
CAREER_LINK = re.compile(r"""href=["']([^"'#]*(?:career|jobs|employment|join-us|work-with-us|opportunit)[^"'#]*)["']""", re.I)


@dataclass
class Detection:
    name: str
    domain: str
    ats: str | None = None
    slug: str | None = None
    evidence: str | None = None


MAX_PAGE_SECONDS = 10
MAX_PAGE_BYTES = 2_000_000


def _get(client: httpx.Client, url: str) -> str:
    """Fetch a page with a hard wall-clock and size limit.

    httpx timeouts apply per network read, so a server that trickles bytes (or streams
    something endless) never trips them. Streaming lets us stop after N seconds / bytes.
    """
    start = time.monotonic()
    try:
        with client.stream("GET", url) as r:
            if r.status_code >= 400:
                return ""
            chunks, size = [], 0
            for chunk in r.iter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_PAGE_BYTES or time.monotonic() - start > MAX_PAGE_SECONDS:
                    break
            return b"".join(chunks).decode(r.encoding or "utf-8", errors="ignore")
    except (httpx.HTTPError, UnicodeError, LookupError):
        return ""


def _scan(html: str) -> tuple[str, str, str] | None:
    for ats, pattern in ATS_PATTERNS:
        for m in pattern.finditer(html):
            groups = [g for g in m.groups() if g] if m.groups() else []
            if groups and groups[0].lower() in _NOT_A_COMPANY:
                continue
            if ats == "oracle" and len(groups) == 1:
                groups.append("CX_1")  # the usual external candidate site
            return ats, "/".join(groups), m.group(0)
    return None


def _similar(a: str, b: str) -> bool:
    norm = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio() >= 0.6


def _probe_apis(client: httpx.Client, name: str) -> tuple[str, str] | None:
    """Guess slugs from the full company name against the ATSes with public JSON APIs.

    Only full-name slugs ("generaldynamics", "general-dynamics"), never a single word:
    "st" or "general" belong to some other company. Where the API reports the board's
    company name (Greenhouse, SmartRecruiters), it must resemble ours too.
    """
    words = re.sub(r"[^a-z0-9 ]", "", name.lower()).split()
    while len(words) > 1 and words[-1] in {"inc", "llc", "corp", "corporation", "co", "company", "ltd"}:
        words.pop()
    slugs = [s for s in dict.fromkeys(["".join(words), "-".join(words)]) if len(s) >= 4]

    def greenhouse_ok(r):
        return r.status_code == 200 and _similar(r.json().get("name", ""), name)

    def smartrecruiters_ok(r):
        content = r.json().get("content", []) if r.status_code == 200 else []
        return bool(content) and _similar((content[0].get("company") or {}).get("name", ""), name)

    for slug in slugs:
        checks = [
            ("greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{slug}", greenhouse_ok),
            ("lever", f"https://api.lever.co/v0/postings/{slug}?limit=1", lambda r: r.status_code == 200 and r.text.startswith("[")),
            ("ashby", f"https://api.ashbyhq.com/posting-api/job-board/{slug}", lambda r: r.status_code == 200),
            ("smartrecruiters", f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1", smartrecruiters_ok),
        ]
        for ats, url, ok in checks:
            try:
                if ok(client.get(url)):
                    return ats, slug
            except (httpx.HTTPError, ValueError):
                pass
    return None


def detect(name: str, domain: str, client: httpx.Client, budget_s: float = 40) -> Detection:
    """budget_s caps the time spent loading pages: some sites stall or block bots, and
    without a cap a single company could take minutes (20 pages x the request timeout)."""
    result = Detection(name, domain)
    deadline = time.monotonic() + budget_s
    home_url = f"https://{domain}"
    home = _get(client, home_url)
    pages = [home]
    links = [urljoin(home_url, h) for h in CAREER_LINK.findall(home)][:6]
    for url in links + [home_url + p for p in CAREER_PATHS] + [f"https://careers.{domain}", f"https://jobs.{domain}"]:
        if (hit := _scan("\n".join(pages))) or time.monotonic() > deadline:
            break
        pages.append(_get(client, url))
    hit = _scan("\n".join(pages))
    if hit:
        result.ats, result.slug, result.evidence = hit
    elif (probe := _probe_apis(client, name)):
        result.ats, result.slug = probe
        result.evidence = "API slug probe"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", nargs="?")
    parser.add_argument("domain", nargs="?")
    parser.add_argument("--csv", help="CSV with name,domain columns")
    args = parser.parse_args()

    if args.csv:
        with open(args.csv, newline="", encoding="utf-8") as f:
            rows = [(r["name"], r["domain"]) for r in csv.DictReader(f)]
    elif args.name and args.domain:
        rows = [(args.name, args.domain)]
    else:
        parser.error("give NAME DOMAIN or --csv FILE")

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JobScraper-ATS-detect"}
    with httpx.Client(headers=headers, timeout=15, follow_redirects=True) as client:
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(lambda r: detect(r[0], r[1], client), rows))

    writer = csv.writer(sys.stdout)
    writer.writerow(["name", "domain", "ats", "slug", "evidence"])
    for d in results:
        writer.writerow([d.name, d.domain, d.ats or "", d.slug or "", (d.evidence or "")[:90]])


if __name__ == "__main__":
    main()
