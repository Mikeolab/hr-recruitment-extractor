"""
Name Extractor
Extracts person and business names from text content using pattern matching.
"""
from __future__ import annotations
import re
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Words that disqualify a regex "name" match — addresses, holidays, generics
# ---------------------------------------------------------------------------
_NOT_NAME_WORDS: frozenset[str] = frozenset({
    # Street/address words (common false positives with Jr./Dr. prefixes)
    "boulevard", "blvd", "suite", "avenue", "ave", "drive", "dr", "street",
    "st", "road", "rd", "way", "lane", "ln", "court", "ct", "place", "pl",
    "circle", "highway", "hwy", "parkway", "pkwy", "terrace", "trail",
    # US holidays / famous names that appear in HR policy text
    "king", "lincoln", "washington", "columbus", "thanksgiving", "veterans",
    "memorial", "independence", "christmas", "holiday", "mlk",
    # Generic department / admin words
    "department", "office", "bureau", "division", "section", "unit", "branch",
    "committee", "board", "commission", "authority", "agency", "administration",
    # Calendar / form-field labels (show up in PDF table headers)
    "date", "year", "month", "day", "time", "period", "joining", "joining",
    "signature", "signed", "approved", "reviewed", "submitted",
})

# Email local-part fragments that look like acronyms / dept codes (not names)
_DEPT_CODE_RE = re.compile(r'^[a-z]{2,4}$')   # 2-4 lower-case letters only

# Common English words that would never be part of a personal name
_COMMON_WORDS: frozenset[str] = frozenset({
    "leave", "management", "forensics", "health", "safety", "benefits",
    "payroll", "recruiting", "staffing", "training", "learning", "people",
    "human", "resources", "services", "systems", "operations", "support",
    "info", "mail", "contact", "hello", "noreply", "admin", "office",
    "general", "enquiries", "team", "group", "care", "help", "jobs",
    "hiring", "careers", "talent", "recruitment",
})


def _is_plausible_name_word(word: str) -> bool:
    """Return True if this word could be part of a real person's name."""
    w = word.lower()
    if w in _NOT_NAME_WORDS:
        return False
    if w in _COMMON_WORDS:
        return False
    # Reject pure acronym/dept codes (all lower-case, 2-4 chars)
    if _DEPT_CODE_RE.match(w) and word == word.lower():
        return False
    return True


def _validate_name(name: str) -> bool:
    """
    Return True if a candidate name looks like a real person's name.
    Rejects address fragments, holiday references, dept codes.
    """
    parts = name.strip().split()
    # Need at least a first + last name (or title + first)
    if len(parts) < 2:
        return False
    # Strip leading titles
    titles = {"mr", "mrs", "ms", "miss", "dr", "prof", "rev", "sr", "jr"}
    name_parts = [p for p in parts if p.lower().rstrip(".") not in titles]
    if len(name_parts) < 1:
        return False
    # Each substantive word must look like a name part
    for p in name_parts:
        if not _is_plausible_name_word(p):
            return False
    # Reject if any part is all-caps and > 2 chars (likely an acronym/dept)
    for p in name_parts:
        if p.isupper() and len(p) > 2:
            return False
    return True


def extract_business_name(title: str, url: str, snippet: str = "") -> str:
    """
    Extract the most likely business name from a search result.
    """
    if not title:
        return ""

    name = title.strip()

    separators = [" | ", " - ", " – ", " — ", " :: ", " >> ", " : "]
    for sep in separators:
        if sep in name:
            parts = name.split(sep)
            common_pages = {
                "home", "about", "contact", "services", "products",
                "blog", "news", "faq", "help", "login", "sign up",
                "about us", "contact us", "our services",
            }
            for part in parts:
                part_clean = part.strip()
                if part_clean.lower() not in common_pages and len(part_clean) > 2:
                    name = part_clean
                    break
            break

    trailing_patterns = [
        r"\s*[-–—]\s*Home\s*$",
        r"\s*[-–—]\s*Official Site\s*$",
        r"\s*[-–—]\s*Official Website\s*$",
        r"\s*\|\s*Home\s*$",
        r"\s*®\s*$",
        r"\s*™\s*$",
        r"\s*Inc\.?\s*$",
        r"\s*LLC\.?\s*$",
        r"\s*Ltd\.?\s*$",
        r"\s*Corp\.?\s*$",
    ]
    for pattern in trailing_patterns:
        name = re.sub(pattern, "", name, flags=re.IGNORECASE).strip()

    return name if len(name) > 1 else ""


def domain_to_company_name(domain: str) -> str:
    """
    Convert a domain name to a human-readable company name.
    e.g. 'pitriverhealthservice.org' → 'Pit River Health Service'
         'chsalliance.org'           → 'CHS Alliance'
         'state.mn.us'               → ''  (generic gov domain — skip)
    """
    if not domain:
        return ""

    # Strip www. and port
    domain = re.sub(r"^www\.", "", domain.lower()).split(":")[0]

    # Skip generic / ambiguous domains
    _SKIP = {
        "state.mn.us", "state.ca.us", "state.tx.us", "state.ny.us",
        "state.fl.us", "state.wa.us", "state.oh.us", "state.il.us",
        "gov", "google.com", "bing.com", "duckduckgo.com", "linkedin.com",
        "indeed.com", "glassdoor.com",
    }
    if domain in _SKIP or domain.endswith(".gov"):
        return ""

    # Take the leftmost meaningful label (before first TLD-like part)
    tlds = {".com", ".org", ".net", ".io", ".co", ".us", ".gov", ".edu",
            ".ai", ".app", ".info", ".biz"}
    base = domain
    for tld in tlds:
        if base.endswith(tld):
            base = base[: -len(tld)]
            break

    # Split on hyphens and convert to Title Case
    words = re.sub(r"[-_]", " ", base).strip()
    # CamelCase → words
    words = re.sub(r"([a-z])([A-Z])", r"\1 \2", words)
    result = words.title()

    # Keep it reasonable length
    return result[:60] if len(result) > 2 else ""


def url_to_company_name(url: str) -> str:
    """Extract a company name from a URL by parsing its domain."""
    if not url:
        return ""
    try:
        host = urlparse(url).netloc.replace("www.", "").lower()
        return domain_to_company_name(host)
    except Exception:
        return ""


def extract_contact_names(text: str) -> list[str]:
    """
    Extract potential person names from text content.
    Uses conservative patterns and validates against non-name word blacklist.
    """
    names: set[str] = set()

    # Pattern: Title + Name (e.g., "Dr. John Smith", "Mr. Jane Doe")
    # Only match if the title prefix makes sense for a real person
    title_pattern = re.compile(
        r"\b(Mr|Mrs|Ms|Miss|Dr|Prof)"      # Removed Rev/Sr/Jr — too many false positives
        r"\.?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b"
    )
    for match in title_pattern.finditer(text):
        candidate = f"{match.group(1)}. {match.group(2)}"
        if _validate_name(candidate):
            names.add(candidate)

    # Pattern: "Contact: Name" or explicit contact labels
    contact_pattern = re.compile(
        r"(?:contact(?:\s+person)?|contact\s+name|name\s*[:\-–])"
        r"\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})",
        re.IGNORECASE,
    )
    for match in contact_pattern.finditer(text):
        candidate = match.group(1).strip()
        if _validate_name(candidate):
            names.add(candidate)

    # Pattern: "By FirstName LastName" (author attribution)
    author_pattern = re.compile(
        r"\b(?:by|author|written by|posted by)\s+([A-Z][a-z]+\s+[A-Z][a-z]+)\b"
    )
    for match in author_pattern.finditer(text):
        candidate = match.group(1).strip()
        if _validate_name(candidate):
            names.add(candidate)

    return sorted(names)


def extract_names_from_email(email: str) -> str:
    """
    Derive a person's name from an email address.
    Only returns a result when the parts strongly resemble a real name.

    Good: john.smith@company.com → 'John Smith'
    Bad:  dct.leave.management@dhs.mn.us → '' (dept codes, not a name)
    """
    if not email or "@" not in email:
        return ""

    local_part = email.split("@")[0]

    # Try common separators
    for sep in [".", "_", "-"]:
        if sep in local_part:
            raw_parts = local_part.split(sep)

            # Filter: keep parts that look like name fragments
            name_parts = []
            for p in raw_parts:
                if len(p) < 2:
                    continue
                if not p.isalpha():
                    continue                      # skip if contains digits
                if p.lower() in _COMMON_WORDS:
                    continue                      # skip dept/generic words
                if _DEPT_CODE_RE.match(p) and p == p.lower():
                    continue                      # skip 2-4 char dept codes
                if p.lower() in _NOT_NAME_WORDS:
                    continue
                name_parts.append(p.capitalize())

            # Only treat as name if we got 1-3 plausible parts
            # and total raw parts were ≤ 3 (real names rarely have 4+ dot-parts)
            if 1 <= len(name_parts) <= 3 and len(raw_parts) <= 3:
                if len(name_parts) >= 2:
                    return " ".join(name_parts[:2])
                # Single part: only use if it's long enough to be a first name
                if len(name_parts[0]) >= 4:
                    return name_parts[0]
            break

    return ""
