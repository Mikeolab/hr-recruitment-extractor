"""
HR Title Extractor
Extracts job titles, departments, and seniority levels from text.
"""
from __future__ import annotations
import re
from typing import Dict, List, Optional, Union

# HR-specific job titles and roles
HR_TITLES = {
    "hr manager", "hr director", "head of hr", "human resources manager",
    "talent acquisition", "talent manager", "recruiter", "recruiting manager",
    "hiring manager", "recruitment director", "staffing manager",
    "employment manager", "personnel manager",
    "compensation manager", "benefits manager",
    "training manager", "learning & development",
    "employee relations manager",
    "organizational development",
    "chief people officer", "vp of people",
    "people ops manager", "people operations",
}

HIRING_KEYWORDS = {
    "recruiting", "hiring", "talent acquisition", "recruitment", "staffing",
    "onboarding", "recruitment partner", "headhunter",
}

SENIORITY_LEVELS = {
    "director", "head", "chief", "vp", "vice president", "executive",
    "senior", "lead", "principal", "manager", "coordinator", "specialist",
}

DEPARTMENTS = {
    "human resources", "hr", "people", "talent", "recruitment",
    "personnel", "staffing", "payroll", "operations", "administration",
}


def extract_title(text: str) -> Dict[str, str | List[str]]:
    """
    Extract HR titles and roles from text.

    Args:
        text: Text to search for titles

    Returns:
        Dictionary with extracted titles, seniority, department
    """
    text_lower = text.lower()

    result = {
        "title": None,
        "seniority": None,
        "department": None,
        "is_hiring_role": False,
        "keywords_found": [],
    }

    # Check for HR titles
    for title in HR_TITLES:
        if title in text_lower:
            result["title"] = title
            break

    # Check for seniority level
    for level in SENIORITY_LEVELS:
        if level in text_lower:
            result["seniority"] = level
            break

    # Check for department
    for dept in DEPARTMENTS:
        if dept in text_lower:
            result["department"] = dept
            break

    # Check for hiring-related keywords
    for keyword in HIRING_KEYWORDS:
        if keyword in text_lower:
            result["is_hiring_role"] = True
            result["keywords_found"].append(keyword)

    return result


def extract_company_info(text: str, source_url: str = "") -> Dict[str, str | None]:
    """
    Extract company information from text and/or the source URL.

    Returns a dict with:
      company_name  — best guess at the organisation name
      industry      — industry sector if detectable
      company_size  — startup / small / mid / large
    """
    from app.extractors.name_extractor import url_to_company_name

    result: Dict[str, str | None] = {
        "company_name": None,
        "industry": None,
        "company_size": None,
    }

    text_lower = text.lower()

    # ── Company name from text patterns ──────────────────────────────────────
    # Pattern: "Welcome to <Company>" / "About <Company>" / "<Company> HR Dept"
    company_patterns = [
        r"welcome\s+to\s+([A-Z][A-Za-z0-9 &.,'-]{2,50})",
        r"about\s+([A-Z][A-Za-z0-9 &.,'-]{2,50})\s*(?:\||–|—|\n)",
        r"©\s*\d{4}\s+([A-Z][A-Za-z0-9 &.,'-]{2,50})",
        r"([A-Z][A-Za-z0-9 &.,'-]{2,50})\s+(?:HR|Human Resources)\s+(?:Department|Team|Office)",
        r"([A-Z][A-Za-z0-9 &.,'-]{2,50})\s+(?:Careers|Jobs|Recruitment)",
    ]
    for pat in company_patterns:
        m = re.search(pat, text)
        if m:
            candidate = m.group(1).strip().rstrip(".,")
            if 3 < len(candidate) < 60:
                result["company_name"] = candidate
                break

    # ── Fallback: derive company name from source URL domain ─────────────────
    if not result["company_name"] and source_url:
        result["company_name"] = url_to_company_name(source_url) or None

    # ── Company size ──────────────────────────────────────────────────────────
    size_patterns = {
        "startup": r"\b(startup|early-stage|seed)\b",
        "small":   r"\b(small business|sme|10-50 employees|1-10 employees)\b",
        "mid":     r"\b(mid-size|100-500 employees|mid-market)\b",
        "large":   r"\b(enterprise|large company|500\+ employees|1000\+ employees)\b",
    }
    for size, pattern in size_patterns.items():
        if re.search(pattern, text_lower, re.IGNORECASE):
            result["company_size"] = size
            break

    # ── Industry ──────────────────────────────────────────────────────────────
    industry_map = {
        "healthcare": r"\b(hospital|clinic|health system|medical center|healthcare)\b",
        "technology": r"\b(software|tech company|saas|cloud|platform)\b",
        "government": r"\b(government|municipality|county|state agency|federal)\b",
        "education":  r"\b(university|college|school district|academy)\b",
        "finance":    r"\b(bank|insurance|financial services|investment)\b",
        "nonprofit":  r"\b(non-profit|nonprofit|501c|charity|foundation)\b",
    }
    for industry, pattern in industry_map.items():
        if re.search(pattern, text_lower, re.IGNORECASE):
            result["industry"] = industry
            break

    return result


def extract_open_positions(text: str) -> List[str]:
    """
    Extract mentions of open positions or hiring needs.

    Args:
        text: Text to search

    Returns:
        List of mentioned job positions
    """
    positions = []

    # Patterns for open positions
    patterns = [
        r"(?:hiring|recruiting|looking for|open position|vacancy|opening)(?:\s+(?:for|in|a))?\s+([a-zA-Z\s]+?)(?:\.|,|;|\)|$)",
        r"(?:open role)(?:\s+(?:for|in|a))?\s+([a-zA-Z\s]+?)(?:\.|,|;|\)|$)",
    ]

    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        positions.extend([m.strip() for m in matches if m.strip()])

    return list(set(positions))  # Deduplicate
