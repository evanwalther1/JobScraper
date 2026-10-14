"""Database schema.

Three tables:
  companies    - who we poll, and which ATS (applicant tracking system) they use
  jobs         - one row per posting, upserted on every refresh
  applications - your status for a job you care about (saved, applied, ...)
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    # Predictable constraint names, so Alembic migrations can reference them.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Source(StrEnum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    WORKDAY = "workday"
    UKG = "ukg"
    PAYLOCITY = "paylocity"
    ORACLE = "oracle"
    MANUAL = "manual"


class Relevance(StrEnum):
    MATCH = "match"
    SENIOR = "senior"
    NON_TECH = "non_tech"


class Preference(StrEnum):
    """My opinion of a company, set on the Companies page."""
    FAVORITE = "favorite"  # starred, can filter to favorites only
    NORMAL = "normal"
    HIDDEN = "hidden"  # still refreshed, but its jobs are hidden from the default view


class Status(StrEnum):
    SAVED = "saved"
    APPLIED = "applied"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    DISMISSED = "dismissed"  # "not interested": hidden from the jobs list


def _enum(e: type[StrEnum], name: str) -> Enum:
    # Stored as VARCHAR + CHECK constraint rather than a native Postgres ENUM,
    # so adding a new value later is a simple migration.
    return Enum(
        e,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda cls: [m.value for m in cls],
    )


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("source", "ats_slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    source: Mapped[Source] = mapped_column(_enum(Source, "source"))
    # The company's identifier on its ATS, e.g. "stripe" in
    # boards-api.greenhouse.io/v1/boards/stripe/jobs
    ats_slug: Mapped[str] = mapped_column(String(200))
    careers_url: Mapped[str | None] = mapped_column(Text)
    # Extra regex that counts as "in the Treasure Valley" for this company only, for boards
    # that label locations by building or omit the state ("Meridian - Main", "City Hall").
    location_aliases: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    preference: Mapped[Preference] = mapped_column(
        _enum(Preference, "preference"), default=Preference.NORMAL, server_default=Preference.NORMAL.value
    )
    notes: Mapped[str | None] = mapped_column(Text)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Health: results of the most recent refresh, shown on the Companies page so a
    # silently broken board (moved ATS, changed layout) doesn't look like "no openings".
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_fetched: Mapped[int | None]
    last_in_area: Mapped[int | None]
    last_new: Mapped[int | None]
    last_closed: Mapped[int | None]

    jobs: Mapped[list["Job"]] = relationship(back_populates="company")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        # The upsert key: a refresh inserts new postings and bumps last_seen_at
        # on ones we already have.
        UniqueConstraint("source", "external_id"),
        Index("ix_jobs_first_seen_at", "first_seen_at"),
        Index("ix_jobs_last_seen_at", "last_seen_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[Source] = mapped_column(_enum(Source, "source"))
    external_id: Mapped[str] = mapped_column(String(200))
    # Denormalized copy of companies.name so the jobs table reads on its own.
    # Re-set on every refresh, so a renamed company catches up on the next run.
    company_name: Mapped[str] = mapped_column(String(200))

    title: Mapped[str] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    remote: Mapped[bool | None] = mapped_column(Boolean)
    department: Mapped[str | None] = mapped_column(Text)
    employment_type: Mapped[str | None] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(Text)
    description_html: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Change detection: first_seen_at = "new since yesterday",
    # closed_at is set when a posting disappears from its source.
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Set by app/relevance.py. Nothing is deleted for being irrelevant, only labeled.
    relevance: Mapped[Relevance | None] = mapped_column(_enum(Relevance, "relevance"), index=True)
    relevance_reason: Mapped[str | None] = mapped_column(Text)

    content_hash: Mapped[str | None] = mapped_column(String(64))
    # The untouched API response, so we can re-normalize without refetching.
    raw: Mapped[dict | None] = mapped_column(JSONB)

    company: Mapped[Company] = relationship(back_populates="jobs")
    application: Mapped["Application | None"] = relationship(back_populates="job")


class ExcludedPosting(Base):
    """Postings we fetched details for and found to be outside the area.

    Only IDs, no job data. Lets refresh skip the detail request next time for boards that
    hide locations in the list ("3 Locations"). Rows are pruned when the posting disappears.
    """

    __tablename__ = "excluded_postings"

    source: Mapped[Source] = mapped_column(_enum(Source, "source"), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    location: Mapped[str | None] = mapped_column(Text)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RadarCompany(Base):
    """A company seen hiring in the Boise area on Adzuna (the discovery radar).

    key is a normalized name ("Micron Technology, Inc." -> "micron"), shared with tracked
    companies so the radar can tell which employers we already cover.
    """

    __tablename__ = "radar_companies"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(300))
    # Set when this radar company is one we track (auto-matched by name, or linked by hand).
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"))
    ignored: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # ATS detection results for companies we don't track yet.
    domain: Mapped[str | None] = mapped_column(String(300))
    detected_ats: Mapped[str | None] = mapped_column(String(50))
    detected_slug: Mapped[str | None] = mapped_column(Text)
    detect_evidence: Mapped[str | None] = mapped_column(Text)
    detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    company: Mapped[Company | None] = relationship()
    postings: Mapped[list["RadarPosting"]] = relationship(back_populates="radar_company")


class RadarPosting(Base):
    """One Adzuna posting. Used only to discover companies and measure coverage; these
    are never shown as jobs (the real posting comes from the company's own ATS)."""

    __tablename__ = "radar_postings"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)  # Adzuna's ID
    company_key: Mapped[str] = mapped_column(
        ForeignKey("radar_companies.key", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    in_area: Mapped[bool] = mapped_column(Boolean)
    # Title/description says remote ("Remote - US", "telework"): Adzuna often tags national
    # remote jobs with a city. Excluded from coverage, since the tracker skips plain remote jobs.
    remote: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    relevance: Mapped[Relevance] = mapped_column(_enum(Relevance, "relevance"))
    redirect_url: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    radar_company: Mapped[RadarCompany] = relationship(back_populates="postings")


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), unique=True
    )
    status: Mapped[Status] = mapped_column(
        _enum(Status, "status"), default=Status.SAVED, server_default=Status.SAVED.value
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    job: Mapped[Job] = relationship(back_populates="application")
