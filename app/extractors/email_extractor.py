"""
Email Extractor
Extracts email addresses from text and HTML content.
"""
from __future__ import annotations
import re
from urllib.parse import unquote


# Comprehensive email regex
EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)

# Common junk/false-positive emails to filter out
JUNK_DOMAINS = {
    "example.com",
    "example.org",
    "test.com",
    "sentry.io",
    "wixpress.com",
    "wordpress.com",
    "w3.org",
    "schema.org",
    "googleapis.com",
    "googleusercontent.com",
    "gstatic.com",
    "facebook.com",
    "twitter.com",
    "instagram.com",
    # Search engines — their own emails appear in SERP pages
    "duckduckgo.com",
    "google.com",
    "bing.com",
    "yahoo.com",
    "ask.com",
    "baidu.com",
    # CDN / infra domains that leak into scraped pages
    "cloudflare.com",
    "amazonaws.com",
    "akamaihd.net",
    "fastly.net",
    "cdn.net",
    # Job boards and social that shouldn't be treated as company emails
    "linkedin.com",
    "glassdoor.com",
    "indeed.com",
    "monster.com",
    "ziprecruiter.com",
    "reddit.com",
    "quora.com",
    "medium.com",
}

JUNK_PREFIXES = {
    "noreply",
    "no-reply",
    "mailer-daemon",
    "postmaster",
    "webmaster",
    "abuse",
    "admin@wordpress",
}

# File extensions that look like email TLDs but aren't
FALSE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js", ".webp"}


def extract_emails(text: str, html: str = "") -> list[str]:
    """
    Extract unique email addresses from text and HTML content.

    Args:
        text: Plain text content
        html: Raw HTML content (for mailto: links etc.)

    Returns:
        List of unique, cleaned email addresses
    """
    emails = set()

    # Extract from plain text
    if text:
        found = EMAIL_PATTERN.findall(text)
        emails.update(found)

    # Extract from HTML (especially mailto: links)
    if html:
        # Find mailto: links
        mailto_pattern = re.compile(r"mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})", re.IGNORECASE)
        mailto_found = mailto_pattern.findall(html)
        emails.update(mailto_found)

        # Also search decoded HTML
        decoded_html = unquote(html)
        found = EMAIL_PATTERN.findall(decoded_html)
        emails.update(found)

    # Clean and filter
    cleaned = []
    for email in emails:
        email = email.lower().strip().rstrip(".")

        # Skip junk domains
        domain = email.split("@")[-1] if "@" in email else ""
        if domain in JUNK_DOMAINS:
            continue

        # Skip junk prefixes
        prefix = email.split("@")[0] if "@" in email else ""
        if any(prefix.startswith(jp) for jp in JUNK_PREFIXES):
            continue

        # Skip false extensions (e.g., image@2x.png)
        if any(email.endswith(ext) for ext in FALSE_EXTENSIONS):
            continue

        # Basic validation: must have @ and at least one dot after @
        if "@" in email and "." in email.split("@")[-1]:
            cleaned.append(email)

    # Sort and deduplicate
    return sorted(set(cleaned))

