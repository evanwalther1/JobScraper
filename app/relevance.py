"""Is a job worth my attention? Labels every job; never deletes one.

  match     tech role at a level I can apply for         -> shown by default
  senior    tech or not, the title says senior/lead/mgmt -> hidden, still stored
  non_tech  no sign of software/IT/data/security work    -> hidden, still stored

Missing a job I could do is the worst outcome, so every rule leans toward "match":
  - Entry-level markers (intern, new grad, junior, ...) override the senior rules.
  - A title with no tech keywords can still match if its description names
    several programming languages or tools.
relevance_reason records which rule decided, so a wrong label shows which rule to fix.

Tuning: edit the patterns below, then `python -m app.relevance` to relabel stored jobs.
"""

import html
import re

from app.models import Relevance

# --- Seniority ---------------------------------------------------------------

ENTRY_LEVEL = re.compile(
    r"\b(intern(ship)?|new\s+(college\s+)?grad(uate)?|entry[\s-]level|junior|jr\.?"
    r"|apprentice(ship)?|early[\s-]career|co-?op|student|campus|university|graduate"
    r"|associate\s+(\w+\s+)?manager)\b",  # "Associate Project Manager" is a junior role
    re.IGNORECASE,
)
SENIOR = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|manager|director|head\s+of|vp|vice\s+president"
    r"|chief|architect|distinguished|fellow|supervisor|expert|[sd]mts)\b",
    re.IGNORECASE,
)
# Level suffixes like "Engineer III"; case-sensitive so "V" in "iv" etc. isn't matched.
SENIOR_LEVEL = re.compile(r"\b(III|IV|V|VI)\b|\blevel\s*[4-9]\b")

# --- Tech ----------------------------------------------------------------------

TECH_TITLE = re.compile(
    r"\b(software|developer|programmer|full[\s-]?stack|frontend|front[\s-]?end\s+(developer|engineer|web)"
    r"|back[\s-]?end|web|mobile\s+(developer|engineer|app)|ios|android|app(lication)?s?\s+(\w+\s+)?(developer|engineer|analyst|support)"
    r"|data\s+(\w+\s+)?(engineer|scientist|science|analyst|analysis|analytics|platform|specialist"
    r"|management|reporting)|analytics\s+engineer"
    r"|machine\s+learning|artificial\s+intelligence|computer\s+vision|deep\s+learning|llm"
    r"|cloud|devops|devsecops|site\s+reliability|platform\s+engineer|infrastructure"
    r"|systems?\s+(engineer|administrator|analyst|integrator)|network(ing)?"
    r"|(information|cyber|cloud|application|network|product|data|ai)\s+security"
    r"|security\s+(engineer|analyst|operations|architect|specialist|intern|administrator|researcher)"
    r"|cyber\w*|infosec|penetration|pentest|identity\s+(and\s+access|management|strategy|engineer|analyst)"
    r"|help\s*desk|service\s+desk|desktop\s+support"
    r"|technical\s+support|quality\s+assurance|test\s+(automation|engineer)|sdet"
    r"|automation\s+engineer|firmware|embedded|database|etl|business\s+intelligence"
    r"|computer\s+science|salesforce|servicenow|erp|crm|solutions?\s+engineer"
    r"|information\s+technology|technology\s+(intern|analyst|associate|specialist|support)"
    r"|technical\s+data|implementation\s+(engineer|analyst|specialist)"
    r"|(technology|technical|it|digital)\s+product\s+(owner|manager))\b"
    r"|[-–]\s*technology\s*$",  # department suffix, e.g. "Career Fair Applicants - Technology"
    re.IGNORECASE,
)
# Titles that mention a tech-ish word but are about physical plant, legal, etc.
# ("Gas Systems Engineer", "Facilities Coordinator - Security"). Overridden by STRONG_SOFTWARE.
NON_TECH_CONTEXT = re.compile(
    r"\b(facilit(y|ies)|gas|life\s+safety|water|electrical|mechanical|hvac|chemical"
    r"|counsel|legal|construction|ehs|retail|store)\b",
    re.IGNORECASE,
)
STRONG_SOFTWARE = re.compile(
    r"\b(software|developer|programmer|full[\s-]?stack|data|IT)\b"
)
# Short acronyms: case-sensitive so "it"/"ai"/"soc" in ordinary words don't count.
TECH_ACRONYM = re.compile(r"\b(IT|AI|ML|QA|SRE|BI|SOC|DBA|EDA|UI|UX|API)\b")

# Description rescue: this many distinct tools means real software work.
DESCRIPTION_TOOLS = re.compile(
    r"(?<![\w.])(python|java|javascript|typescript|c\#|c\+\+|golang|rust|sql|react|angular"
    r"|vue|node\.js|\.net|aws|azure|gcp|kubernetes|docker|linux|git|rest(ful)?\s+apis?"
    r"|terraform|powershell|bash|django|flask|fastapi)(?![\w])",
    re.IGNORECASE,
)
DESCRIPTION_MIN_TOOLS = 3

_TAG = re.compile(r"<[^>]+>")


def html_to_text(description_html: str | None) -> str:
    return html.unescape(_TAG.sub(" ", description_html or ""))


def classify(title: str, description_html: str | None = None) -> tuple[Relevance, str]:
    """Return (label, reason). Pure function: easy to test and to re-run over stored jobs."""
    entry = ENTRY_LEVEL.search(title)
    senior = SENIOR.search(title) or SENIOR_LEVEL.search(title)
    if senior and not entry:
        return Relevance.SENIOR, f"senior title: '{senior.group(0)}'"

    tech = TECH_TITLE.search(title) or TECH_ACRONYM.search(title)
    if tech:
        context = NON_TECH_CONTEXT.search(title)
        if context and not STRONG_SOFTWARE.search(title):
            return Relevance.NON_TECH, f"'{tech.group(0).strip()}' but non-tech context: '{context.group(0)}'"
        return Relevance.MATCH, f"tech title: '{tech.group(0).strip()}'"

    tools = sorted({m.group(0).lower() for m in DESCRIPTION_TOOLS.finditer(html_to_text(description_html))})
    if len(tools) >= DESCRIPTION_MIN_TOOLS:
        return Relevance.MATCH, f"description mentions {', '.join(tools)}"

    return Relevance.NON_TECH, "no tech keywords in title; few in description"


def main() -> None:
    """Relabel every stored job with the current rules (run after editing patterns)."""
    from collections import Counter

    from sqlalchemy import select, update

    from app.db import SessionLocal
    from app.models import Job

    counts: Counter[str] = Counter()
    with SessionLocal.begin() as session:
        for job_id, title, desc in session.execute(select(Job.id, Job.title, Job.description_html)):
            label, reason = classify(title, desc)
            counts[label.value] += 1
            session.execute(
                update(Job).where(Job.id == job_id).values(relevance=label, relevance_reason=reason)
            )
    print(dict(counts))


if __name__ == "__main__":
    main()
