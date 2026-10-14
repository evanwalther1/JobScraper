"""Seed the companies table with Treasure Valley employers.

Run:  python -m app.seed_companies
Safe to re-run: existing (source, ats_slug) rows are updated, not duplicated.

ats_slug formats:
  greenhouse  board token          boards-api.greenhouse.io/v1/boards/{slug}/jobs
  lever       company slug         api.lever.co/v0/postings/{slug}
  workday     tenant/wdN/site      {tenant}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
  ukg         host/tenant/board    {host}/{tenant}/JobBoard/{board}
  paylocity   company GUID         recruiting.paylocity.com/recruiting/jobs/All/{guid}
  oracle      host/siteNumber      {host}/hcmUI/CandidateExperience/en/sites/{siteNumber}

Finding a new company's ATS: `python -m app.detect_ats "Name" domain.com`.
Candidates we couldn't connect yet are listed in data/treasure_valley_candidates.csv.
"""

from sqlalchemy.dialects.postgresql import insert

from app.db import SessionLocal
from app.models import Company, Source

G, W, U, P, O = Source.GREENHOUSE, Source.WORKDAY, Source.UKG, Source.PAYLOCITY, Source.ORACLE

# (name, source, ats_slug, careers_url, location_aliases)
# location_aliases: extra regex counted as "in the area" for boards that label locations
# by building or leave off the state.
COMPANIES = [
    # Greenhouse
    ("Truckstop", G, "truckstop", "https://truckstop.com/company/careers/", None),
    ("CompuNet", G, "compunetinc", "https://www.compunet.biz/careers/", None),
    ("VYNYL", G, "vynyl", "https://vynyl.com/careers", None),
    ("Speechify", G, "speechify", "https://speechify.com/careers/", None),
    # Workday
    ("Micron Technology", W, "micron/wd1/External", "https://careers.micron.com", None),
    ("Clearwater Analytics", W, "clearwateranalytics/wd1/Clearwater_Analytics_Careers", "https://cwan.com/careers/", None),
    ("Schweitzer Engineering Laboratories", W, "selinc/wd1/SEL", "https://selinc.com/careers/", None),
    ("HP", W, "hp/wd5/ExternalCareerSite", "https://jobs.hp.com", None),
    ("Idaho Power", W, "idahopowercompany/wd1/External", "https://www.idahopower.com/careers/", None),
    ("Blue Cross of Idaho", W, "bcidaho/wd5/BCI", "https://www.bcidaho.com/careers", r"^Meridian - Main$"),
    ("Saint Alphonsus Health System", W, "trinityhealth/wd1/Jobs", "https://www.saintalphonsus.org/careers", None),
    ("Lamb Weston", W, "lambweston/wd1/Lamb_External", "https://www.lambweston.com/careers", None),
    ("KLA", W, "kla/wd1/Search", "https://www.kla.com/careers", None),
    ("Sparklight (Cable One)", W, "cableone/wd1/cable_one_external_careers", "https://www.sparklight.com/careers", None),
    ("Moss Adams (Baker Tilly)", W, "bakertilly/wd5/BTCareers", "https://www.bakertilly.com/careers", None),
    # City of Meridian labels jobs by building; every one of them is in Meridian, ID.
    ("City of Meridian", W, "meridiancity/wd12/meridian_city", "https://meridiancity.org/jobs", r"."),
    ("Kount (Equifax)", W, "equifax/wd5/External", "https://careers.equifax.com", None),
    ("T-Mobile", W, "tmobile/wd1/External", "https://careers.t-mobile.com", None),
    ("Applied Materials", W, "amat/wd1/External", "https://careers.appliedmaterials.com", None),
    # UKG (UltiPro)
    ("Idaho Central Credit Union", U, "recruiting.ultipro.com/IDA1000/26bbbd4e-7790-a471-7482-a40a1f3f8f25", "https://www.iccu.com/careers/", None),
    ("Boise Cascade", U, "recruiting2.ultipro.com/BOI1001BOIS/d37b562e-1487-4cc9-8cc4-d95be5cacd55", "https://www.bc.com/careers/", None),
    ("Western States Cat", U, "recruiting2.ultipro.com/WES1008TTCI/12c3b909-9215-4720-aef9-f50089d1254c", "https://www.westernstatescat.com/careers/", None),
    ("Intermountain Gas (MDU)", U, "recruiting2.ultipro.com/MDU1000MDUR/e10b960e-6e6f-4e58-9d5e-4f2aff2cbc15", "https://www.intgas.com/careers/", None),
    ("Washington Trust Bank", U, "recruiting2.ultipro.com/WAS1000WTB/cb002c76-8419-4941-9c78-d28ae4e9c89e", "https://www.watrust.com/careers", None),
    # Paylocity
    ("Scentsy", P, "f135c024-3c7c-436a-a993-c9cedc402423", "https://scentsy.com/careers", None),
    ("Ednetics", P, "006934dc-ae7f-44c1-8ab0-d84d3e0f51b7", "https://www.ednetics.com/careers", None),
    ("Syringa Networks", P, "0e349db7-b4fe-436e-816a-f8dcbcde4444", "https://www.syringanetworks.net/careers", None),
    ("Northwest Nazarene University", P, "dc628dad-def7-40c4-a8e8-46445f141a37", "https://www.nnu.edu/employment", None),
    # Oracle Recruiting Cloud
    ("Albertsons Companies", O, "eofd.fa.us6.oraclecloud.com/CX_1", "https://www.albertsonscompanies.com/careers", None),
]


def main() -> None:
    rows = [
        {"name": n, "source": s, "ats_slug": slug, "careers_url": url, "location_aliases": aliases}
        for n, s, slug, url, aliases in COMPANIES
    ]
    stmt = insert(Company).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["source", "ats_slug"],
        set_={
            "name": stmt.excluded.name,
            "careers_url": stmt.excluded.careers_url,
            "location_aliases": stmt.excluded.location_aliases,
        },
    )
    with SessionLocal.begin() as session:
        session.execute(stmt)
    print(f"Seeded {len(rows)} companies.")


if __name__ == "__main__":
    main()
