"""
JT Platt — Career Inventory.

SOURCE OF TRUTH per project rules. Never extend, embellish, infer, or "fill in"
experience that isn't here. The scorer reads from this. If a posting requires
something that isn't in here, that's a gap — the scorer flags it.

Dates: years are confirmed. Months are not — JT verifies via SSA earnings
record before anything touches a CA STD 678 (signed under penalty of perjury).

When JT updates his career, he edits this file. Agent re-scores at next run.
"""

# --- Personal ---------------------------------------------------------------

PERSONAL = {
    "name": "James T. Platt",
    "preferred_name": "JT",
    "location": "Fair Oaks, California",
    "email": "James.T.Platt@gmail.com",
    "phone": "(530) 913-1980",
    "linkedin": "linkedin.com/in/jtplatt",
}

# --- Current Role -----------------------------------------------------------

CURRENT_ROLE = {
    "title": "Information Technology Specialist I",
    "employer": "FI$CAL (Financial Information System for California)",
    "employer_type": "ca_state",
    "location": "Sacramento, CA",
    "remote": True,
    "start_year": 2023,
    "end_year": None,
    "duties": [
        "Run RPA automations for accounting entries in BluePrism",
        "Perform security audits; manage WordPress site updates",
        "Track work and build custom forms in ServiceNow",
        "Support ADA/accessibility compliance for enterprise web and document publishing",
        "Contribute to external website and internal SharePoint development",
        "Publish and deploy websites via Azure",
    ],
    "skills_used": [
        "BluePrism", "RPA", "ServiceNow", "WordPress", "SharePoint",
        "security audits", "ADA compliance", "accessibility",
        "custom form design", "workflow design",
        "Azure (website publishing and deployment)",
    ],
}

# --- Prior Roles (reverse chronological) ------------------------------------

PRIOR_ROLES = [
    {
        "title": "Founder & Operator",
        "employer": "The Sneaker Savant, LLC",
        "employer_type": "self_employed",
        "concurrent": True,  # overlaps current and prior roles
        "start_year": 2013,
        "end_year": None,
        "duties": [
            "Founded and operate a sneaker authentication and grading platform",
            "Developed proprietary Shoemetrics grading methodology",
            "Created 5+ sneaker trading card sets via 1 to Stock brand",
            "Built audience to 16,000+ Instagram followers; peak reach 1.5M/month",
            "Partnerships with marketplaces and brands (StockX advisor among others)",
        ],
        "skills_used": [
            "brand development", "content creation", "social media",
            "marketplace strategy", "product design",
        ],
        "note": "Concurrent with W-2 roles since 2013.",
    },
    {
        "title": "Mathematics & Computer Science Teacher",
        "employer": "ARISE High School",
        "employer_type": "ca_charter_school",
        "location": "Oakland, CA",
        "start_year": 2018,
        "end_year": 2020,
        "calsters_covered": True,
        "duties": [
            "Taught Geometry, Computer Science, and Advisory",
            "Developed custom Geometry curriculum aligned with CA state standards",
            "Used code.org curriculum for CS classes",
            "Managed mastery-based grading via PowerSchool",
            "Mentored at-risk students; coordinated with parents, community leaders, counselors",
            "Earned CA CTE credential during this role",
        ],
        "skills_used": [
            "curriculum design", "lesson planning", "classroom management",
            "code.org", "PowerSchool", "mastery-based grading", "mentorship",
        ],
    },
    {
        "title": "Teacher (Social Studies / English / Web Development)",
        "employer": "Oakland Charter Academy (Amethod Public Schools)",
        "employer_type": "ca_charter_school",
        "location": "Oakland, CA",
        "start_year": 2017,
        "end_year": 2018,
        "calsters_covered": True,
        "duties": [
            "Taught Social Studies and English",
            "Led after-school Web Development course",
            "Integrated Google Workspace for Education across instruction",
            "Served as de facto IT support for teachers and students",
        ],
        "skills_used": [
            "curriculum design", "Google Workspace for Education",
            "classroom management", "after-school programs",
        ],
    },
    {
        "title": "Project Manager / Front-End Developer",
        "employer": "CodeKings, Inc. (acquired by RewardStyle)",
        "location": "San Mateo, CA",
        "start_year": 2015,
        "end_year": 2017,
        "duties": [
            "Helped scale team from 4 to 20+ employees in under a year",
            "Managed ad-server systems for hundreds of clients",
            "Handled project planning, QA, and team coordination through acquisition",
        ],
        "skills_used": [
            "project management", "ad-server systems", "QA",
            "team coordination", "M&A transition",
        ],
    },
    {
        "title": "Front-End Web Developer",
        "employer": "Thismoment, Inc.",
        "location": "San Francisco, CA",
        "start_year": 2013,
        "end_year": 2014,
        "duties": [
            "Built and maintained marketing websites for Fortune 500 clients on in-house CMS",
            "Led team of 8 interns; delivered 85 client websites in one week",
            "Built Bud Light Heroes website for 2014 Super Bowl campaign",
        ],
        "skills_used": [
            "HTML", "CSS", "JavaScript", "Git", "JIRA", "Photoshop",
            "team leadership",
        ],
    },
    {
        "title": "IT & Accounting Consultant",
        "employer": "Tahoe Restaurant Collection, Inc.",
        "location": "Lake Tahoe, CA",
        "start_year": 2011,
        "end_year": 2013,
        "duties": [
            "Managed daily bookkeeping and accounting across 5 restaurant entities",
            "Oversaw POS system migrations",
            "Set up domains, email, social media",
            "Implemented Google Workspace for 100+ employees",
        ],
        "skills_used": [
            "bookkeeping", "POS systems", "Google Workspace administration",
            "domain/email setup",
        ],
    },
    {
        "title": "Quality Assurance Analyst",
        "employer": "Intuit",
        "location": "Reno, NV",
        "start_year": 2010,
        "end_year": 2011,
        "duties": [
            "Conducted functional and regression testing for QuickBooks platform updates",
            "Collaborated with developers in Agile environment on 5+ daily changes",
        ],
        "skills_used": [
            "functional testing", "regression testing", "Agile",
            "QuickBooks", "QA",
        ],
    },
    {
        "title": "Business Analyst (Middle Office)",
        "employer": "GFI Group",
        "location": "New York, NY",
        "start_year": 2008,
        "end_year": 2010,
        "duties": [
            "Designed and implemented a Generic Trade Capture System, reducing onboarding "
            "time for new financial products from months to days",
            "Bridged brokers, back office, and development teams across 20+ developers "
            "and 10+ trading desks",
        ],
        "skills_used": [
            "business analysis", "requirements gathering",
            "trade capture systems", "cross-functional liaison",
            "systems design",
        ],
    },
    {
        "title": "Senior Accountant / Financial Analyst",
        "employer": "GFI Group",
        "location": "New York, NY",
        "start_year": 2006,
        "end_year": 2008,
        "duties": [
            "Managed P&L reporting, contract negotiation, and budgeting for FX, "
            "Commodities, and Energy divisions",
            "Automated financial reporting systems",
        ],
        "skills_used": [
            "P&L reporting", "budgeting", "contract negotiation",
            "financial reporting automation", "FX/Commodities/Energy",
        ],
    },
    {
        "title": "Commodities AR Analyst",
        "employer": "Bear Stearns",
        "location": "New York, NY",
        "start_year": 2006,
        "end_year": 2006,
        "duration_months": 9,  # JT confirmed 100% certainty
        "note": "Left in 2006 sensing trouble. Bear Stearns collapsed March 2008.",
        "duties": [
            "Processed COMEX commodity trading desk reports (coffee, pork belly, orange juice)",
            "Replaced 1,000+ page dot-matrix printout with streamlined Excel-based reporting",
        ],
        "skills_used": [
            "commodities accounting", "Excel automation",
            "reporting redesign",
        ],
    },
    {
        "title": "Junior Accountant",
        "employer": "GFI Group",
        "location": "New York, NY",
        "start_year": 2004,
        "end_year": 2006,
        "duties": [
            "Managed daily global sales reports compiled from Oracle, MS Access, "
            "and Trade Capture Systems",
            "Provided financial analysis data for company's 2006 IPO roadshow",
            "Built Excel automation tools that cut reporting time significantly",
        ],
        "skills_used": [
            "Oracle", "MS Access", "Excel automation",
            "global sales reporting", "IPO support",
        ],
    },
    {
        "title": "Foreign Bureau Accountant",
        "employer": "Associated Press",
        "location": "New York, NY",
        "start_year": 2003,
        "end_year": 2004,
        "duties": [
            "Handled monthly accounting for 30+ foreign offices",
            "Calculated and published company-wide foreign exchange rates",
        ],
        "skills_used": [
            "international accounting", "foreign exchange",
            "multi-entity bookkeeping",
        ],
    },
]

# --- Credentials ------------------------------------------------------------

CREDENTIALS = [
    {
        "name": "CA Career Technical Education Credential",
        "subject": "Business & Information Systems / Information & Communications Technologies",
        "credential_id": "220055798",
        "issued": "2022-01-31",
        "expires": "2027-02-01",
        "status": "active",
        "renewal_note": (
            "Pandemic-era difficulty fulfilling holder responsibilities. "
            "Renewal in question. Use it or let it expire."
        ),
    },
    {
        "name": "Bitcoin Fundamentals Certificate",
        "issuer": "Pomp's Crypto Course",
        "issued": "2022-01",
    },
    {
        "name": "Web Developer Certificate",
        "issuer": "Coding Dojo Bootcamp",
        "location": "San Mateo, CA",
        "issued": "2013",
    },
]

EDUCATION = [
    {
        "degree": "BA, Business Management Economics",
        "institution": "University of California, Santa Cruz",
        "year": 2003,
        "gpa": 3.45,
    },
    {
        "degree": "High School Diploma",
        "institution": "Truckee High School",
        "year": 1998,
    },
]

# --- Skills (aggregated) ----------------------------------------------------

SKILLS = {
    "programming_web": ["HTML", "CSS", "JavaScript", "Git", "JIRA", "WordPress"],
    "automation_rpa": [
        "BluePrism", "Excel macros", "business process automation",
    ],
    "financial_systems": [
        "FI$CAL", "Oracle", "MS Access", "proprietary trade capture systems",
        "QuickBooks",
    ],
    "it_service": [
        "ServiceNow", "SharePoint", "custom form design",
        "Azure (website publishing/deployment)",
    ],
    "education_tools": [
        "curriculum design", "Google Workspace for Education",
        "PowerSchool", "code.org", "mastery-based grading",
    ],
    "qa": ["functional testing", "regression testing", "Agile"],
    "finance_core": [
        "P&L reporting", "budgeting", "contract negotiation",
        "financial analysis", "foreign exchange", "multi-entity accounting",
        "IPO support",
    ],
    "leadership": [
        "project management", "team leadership",
        "cross-functional liaison", "requirements gathering",
        "stakeholder communication",
    ],
    "content_marketing": [
        "social media growth", "brand partnerships",
        "content creation",
    ],
    "accessibility_compliance": [
        "ADA compliance", "accessibility for web/document publishing",
    ],
}

# --- Pension Status (CRITICAL for employer allow-list) ----------------------

PENSION = {
    "calpers_years_approx": "3+",      # current FI$CAL service since 2023
    "calsters_years_approx": "2-3",    # 2017-2020 teaching
    "calsters_vested": False,          # 5-year vest not reached
    "reciprocity_status_unknown": True,
    "reciprocity_note": (
        "CalSTRS service ended ~2020; CalPERS service started 2023. "
        "Gap exceeds 6-month reciprocity window. JT to confirm with both "
        "systems whether STRS↔PERS link is established."
    ),
    "contributions_status": "on_deposit",  # never refunded — must stay that way
    "vested_anywhere": False,
}

# --- Preferences ------------------------------------------------------------

PREFERENCES = {
    "primary_criteria": [
        # A job must meet at least one of these.
        "fully_remote",
        "teaching",
        "meaningful_flexibility",
    ],
    "secondary_preferences": [
        "ca_state_education_departments",
        "public_sector_stability",
        "international_overseas_component",
    ],
    "deal_breakers": [
        "Current employer: FI$CAL — wants out, not laterally within",
        "Requires CPA, JD, MD, PE license, RN, LCSW",
        "Requires active high-tier vendor certs JT doesn't hold "
        "(CCIE, CISSP, AWS SA Pro, GCP Pro, Azure Expert)",
        "Salary below current ITS I rate",
        "Full classroom teaching day (4+ hours of classroom contact)",
    ],
    "pension_priority_order": [
        "CalPERS-continuing (state, CSU)",
        "SCERS-reciprocal (Sacramento County)",
        "Federal (FERS — separate system)",
        "Overseas teaching (DODEA, accredited international schools — JT prioritizes "
        "overseas exposure over pension continuity for these)",
    ],
    "ideal_workday_shape": (
        "A couple hours of in-person, explain-hard-things work in the morning. "
        "Async/admin work from home off-peak. Person-to-person, not screen-to-face. "
        "Light on formality. Not 4+ hours of classroom contact. Adult treated as adult."
    ),
    "target_titles_yes": [
        "Information Technology Specialist I",
        "Information Technology Specialist II",
        "IT Specialist",  # federal short form of "Information Technology Specialist"
        "Business Systems Analyst",
        "Applications Analyst",
        "Training Officer",
        "Education Programs Consultant",
        "Adult Education Instructor",
        "IT Consultant (CSU)",
        "Instructional Designer",
        "Staff Services Analyst (training/program focus)",
        "Educational Programs Assistant",
    ],
    "target_titles_no": [
        "Senior Software Engineer",
        "Database Administrator",
        "Network Architect",
        "Senior Full-Stack Developer",
        "Lead DevOps Engineer",
        "Cybersecurity Analyst requiring CISSP/CCIE",
    ],
    # Geographic wishlist for hard-filter location matching. Substring,
    # case-insensitive — "Sacramento" catches "Sacramento, California" and
    # "West Sacramento" alike. Sacramento metro (home), Hawaii (Honolulu),
    # Monterey Bay (CSUMB), Santa Cruz (UCSC town), Oceanside (CSUSM
    # commute), and the Tahoe/Truckee corridor (childhood home).
    "target_locations": [
        "Sacramento",
        "Yolo",
        "Placer",
        "El Dorado",
        "Honolulu",
        "Hawaii",
        "Monterey",
        "Santa Cruz",
        "Oceanside",
        "Truckee",
        "Tahoe",
    ],
}

# --- Convenience: flattened skill keywords for scorer -----------------------

def all_skill_keywords():
    """Return a flat list of all skill phrases JT has used in past roles."""
    out = []
    for bucket in SKILLS.values():
        out.extend(bucket)
    # Pull skills_used from all roles too
    for role in [CURRENT_ROLE] + PRIOR_ROLES:
        out.extend(role.get("skills_used", []))
    # Dedup, case-fold for matching
    return sorted({s.lower() for s in out})


def all_employers():
    """All past employers, in case useful for context-aware matching."""
    out = [CURRENT_ROLE["employer"]]
    out.extend(r["employer"] for r in PRIOR_ROLES)
    return out
