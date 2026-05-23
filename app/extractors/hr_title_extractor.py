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


def extract_company_info(text: str) -> Dict[str, str | None]:
    """
    Extract company information from text.

    Args:
        text: Text to search for company info

    Returns:
        Dictionary with company details
    """
    result = {
        "company_name": None,
        "industry": None,
        "company_size": None,
    }

    # Simple company size detection
    size_patterns = {
        "startup": r"\b(startup|early-stage|seed)\b",
        "small": r"\b(small|sme|10-50|1-10)\b",
        "mid": r"\b(mid-size|100-500|mid-market)\b",
        "large": r"\b(enterprise|large|500\+|1000\+)\b",
    }

    text_lower = text.lower()
    for size, pattern in size_patterns.items():
        if re.search(pattern, text_lower, re.IGNORECASE):
            result["company_size"] = size
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
