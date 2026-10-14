"""Relevance rules. MUST_MATCH is the important list: jobs I could do must never be hidden.
When a real posting gets mislabeled, add its title here before fixing the rule."""

import pytest

from app.models import Relevance
from app.relevance import classify

MUST_MATCH = [
    "Software Engineer Intern",
    "Software Engineer - Full Stack TypeScript/React/C#",
    "New College Grad - IT Software Support Engineer",
    "New College Grad - ID1 IT System Administrator",
    "Salesforce Developer Intern",
    "Data Flow Engineer I",
    "Data Management & Reporting Intern",
    "Intern - Yield Enhancement, Data Analysis",
    "Associate Project Manager-Cybersecurity",
    "Associate Firmware Engineer",
    "Full Stack AI Engineer",
    "Junior Frontend Developer",
    "Security Analyst",
    "Cloud Engineer",
    "Mobile App Developer",
    "Technology Product Owner",
    "Technical Product Owner",
    "IT Product Owner - Chubbuck or Meridian",
    "Applications Integration Engineer I or II- Onsite in Boise, ID",
    "Network Automation Engineer (Service-Provider)",
    "Boise State University 2026 Fall Career Fair Applicants- Technology",
]

MUST_BE_SENIOR = [
    "Sr. Full Stack AI Software Engineer",
    "Staff Security Engineer",
    "Principal Data Scientist, TPG",
    "Embedded Software Lead",
    "Manager, Software Development",
    "DMTS, Systems Architect",
    "Lead Infrastructure Engineer III",
    "Software Engineer IV",
]

MUST_BE_NON_TECH = [
    "Associate Account Executive, Carrier",
    "Reconciliation Analyst",
    "Internal Recruiter (Contract)",
    "New College Grad - Dry Etch Process Engineer",
    "Gas Systems Engineer - O&M",
    "Facilities Coordinator - Security & ESG Liaison",
    "Assistant General Counsel -Technology Licensing",
    "Mobile Associate, Store-in-Store - Retail Sales - Full Time",
]


@pytest.mark.parametrize("title", MUST_MATCH)
def test_must_match(title):
    assert classify(title)[0] == Relevance.MATCH, classify(title)[1]


@pytest.mark.parametrize("title", MUST_BE_SENIOR)
def test_senior(title):
    assert classify(title)[0] == Relevance.SENIOR


@pytest.mark.parametrize("title", MUST_BE_NON_TECH)
def test_non_tech(title):
    assert classify(title)[0] == Relevance.NON_TECH, classify(title)[1]


def test_entry_level_beats_senior_words():
    # "Manager" would normally mean senior, but "New College Grad" wins.
    assert classify("New College Grad - IT Project Manager")[0] == Relevance.MATCH


def test_description_rescues_vague_title():
    desc = "<p>You will build tools in <b>Python</b> and C# backed by SQL Server.</p>"
    label, reason = classify("Operations Improvement Engineer", desc)
    assert label == Relevance.MATCH
    assert "python" in reason


def test_one_tool_in_description_is_not_enough():
    assert classify("Process Engineer", "<p>Some Python helpful.</p>")[0] == Relevance.NON_TECH
