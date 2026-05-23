"""
Email Enrichment Pipeline
=========================
Free Hunter.io / Apollo.io equivalent — no paid APIs.

Pipeline per lead (in order):
  1. Extract domain from source_url
  2. DNS-guess domain from company name
  3. Crawl /contact /team /about pages for raw emails
  4. Generate 15 professional email patterns (first.last@, flast@, …)
  5. SMTP-verify each pattern via MX RCPT TO (no email sent)
  6. Return best result with confidence score

Special handling:
  - Gmail/Outlook domains → skip SMTP (they block it), mark "unverifiable"
  - Catch-all servers → detect via probe address, lower confidence
  - LinkedIn source URLs → skip netloc (use business_name instead)
  - Rate limiting → asyncio.sleep(0.5–1.5s) between SMTP checks
"""
from __future__ import annotations

import asyncio
import re
import smtplib
import socket
from typing import Callable, Optional
from urllib.parse import urlparse, quote_plus

import dns.resolver
import httpx
from bs4 import BeautifulSoup

from app.extractors.email_extractor import JUNK_DOMAINS, JUNK_PREFIXES, EMAIL_PATTERN


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Domains where SMTP verification always returns 250 (catch-all) or blocks probes
UNVERIFIABLE_DOMAINS = {
    "gmail.com", "googlemail.com",
    "outlook.com", "hotmail.com", "live.com", "msn.com",
    "yahoo.com", "ymail.com",
    "icloud.com", "me.com", "mac.com",
    "protonmail.com", "pm.me",
    "zoho.com", "zohomail.com",
}

# Generic single-word company "names" useless for domain lookup
_GENERIC_COMPANY_NAMES = {
    "startup", "company", "organization", "organisation", "corporation", "corp",
    "inc", "ltd", "llc", "group", "firm", "enterprise", "enterprises", "agency",
    "tech", "technology", "technologies", "solutions", "services", "consulting",
    "management", "global", "international", "digital", "media", "studio",
    "labs", "works", "ventures", "partners", "associates", "unknown", "n/a",
}

# Common TLDs to try when guessing company domain from name
_CANDIDATE_TLDS = [".com", ".io", ".co", ".net", ".org", ".ai", ".app", ".us"]

# Company website paths likely to expose employee emails
_EMAIL_PAGES = [
    "", "/contact", "/contact-us", "/about", "/about-us",
    "/team", "/our-team", "/people", "/staff", "/leadership",
]

# The 15 standard professional email patterns
_PATTERNS = [
    "{f}.{l}",    # john.doe       — most common at large companies
    "{f}",         # john           — most common at small companies
    "{fi}.{l}",   # j.doe
    "{f}{l}",      # johndoe
    "{fi}{l}",     # jdoe
    "{f}.{li}",   # john.d
    "{l}.{f}",    # doe.john
    "{l}",         # doe
    "{l}.{fi}",   # doe.j
    "{l}{fi}",     # doej
    "{fi}.{li}",  # j.d
    "{f}_{l}",    # john_doe
    "{f}-{l}",    # john-doe
    "{fi}{li}",    # jd
    "{l}{f}",      # doejohn
]


# ---------------------------------------------------------------------------
# 1. Domain discovery
# ---------------------------------------------------------------------------

async def find_company_domain(company_name: str) -> Optional[str]:
    """
    Find the primary domain for a company.
    First tries common TLD variants via DNS A-record lookup,
    then falls back to a DDG HTML search.
    Returns e.g. "acme.com" or None.
    """
    if not company_name or len(company_name.strip()) < 2:
        return None

    # Sanitise: keep alphanumeric + spaces, then make slug
    slug = re.sub(r"[^a-z0-9 ]", "", company_name.lower()).replace(" ", "")
    if not slug:
        return None

    # Quick DNS probe for common TLDs (no HTTP request needed)
    resolver = dns.resolver.Resolver()
    resolver.lifetime = 3.0
    for tld in _CANDIDATE_TLDS:
        candidate = slug + tld
        try:
            resolver.resolve(candidate, "A")
            return candidate          # First hit wins
        except Exception:
            continue

    # Fallback: DDG HTML search for "company_name official website"
    try:
        query = quote_plus(f'"{company_name}" official website')
        url = f"https://html.duckduckgo.com/html/?q={query}"
        async with httpx.AsyncClient(timeout=8, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0 (compatible)"}) as client:
            resp = await client.get(url)
        soup = BeautifulSoup(resp.text, "lxml")
        # DDG result links carry the real URL in the href
        for a in soup.select("a.result__url"):
            href = a.get_text(strip=True)
            if href and "." in href:
                # Strip protocol / path
                domain = re.sub(r"^https?://", "", href).split("/")[0].lstrip("www.")
                if domain and len(domain) > 3:
                    return domain
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# 2. Company website email scraping
# ---------------------------------------------------------------------------

async def scrape_domain_emails(domain: str) -> list[str]:
    """
    Crawl common company website pages and extract email addresses
    that belong to the target domain.
    Returns a deduplicated, filtered list.
    """
    if not domain:
        return []

    found: set[str] = set()
    domain_lower = domain.lower()

    async with httpx.AsyncClient(
        timeout=8,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; EmailFinder/1.0)"},
    ) as client:
        for path in _EMAIL_PAGES:
            url = f"https://{domain}{path}"
            try:
                resp = await client.get(url)
                # Extract from rendered text
                text = resp.text
                for email in EMAIL_PATTERN.findall(text):
                    email = email.lower().strip().rstrip(".")
                    edomain = email.split("@")[-1] if "@" in email else ""
                    if domain_lower in edomain or edomain == domain_lower:
                        if _is_useful_email(email):
                            found.add(email)
                # Also check mailto: links via BeautifulSoup
                soup = BeautifulSoup(text, "lxml")
                for a in soup.select("a[href^='mailto:']"):
                    email = a["href"].replace("mailto:", "").split("?")[0].strip().lower()
                    edomain = email.split("@")[-1] if "@" in email else ""
                    if domain_lower in edomain and _is_useful_email(email):
                        found.add(email)
            except Exception:
                continue

    return sorted(found)


def _is_useful_email(email: str) -> bool:
    """Filter out junk/system emails."""
    if "@" not in email or "." not in email.split("@")[-1]:
        return False
    domain = email.split("@")[-1]
    prefix = email.split("@")[0]
    if domain in JUNK_DOMAINS:
        return False
    if any(prefix.startswith(jp) for jp in JUNK_PREFIXES):
        return False
    # Skip generic inbox addresses
    if prefix in {"info", "contact", "hello", "support", "sales", "marketing",
                  "office", "enquiries", "enquiry", "general", "team"}:
        return False
    return True


# ---------------------------------------------------------------------------
# 3. Email pattern generation
# ---------------------------------------------------------------------------

def generate_email_patterns(first: str, last: str, domain: str) -> list[str]:
    """
    Generate up to 15 standard professional email patterns for a person.
    Deduplicates automatically (handles very short names).
    """
    f = first.lower().strip()
    l = last.lower().strip()
    fi = f[0] if f else ""
    li = l[0] if l else ""

    seen: set[str] = set()
    patterns: list[str] = []
    for tmpl in _PATTERNS:
        try:
            local = tmpl.format(f=f, l=l, fi=fi, li=li)
            email = f"{local}@{domain}"
            if email not in seen and local:
                seen.add(email)
                patterns.append(email)
        except Exception:
            continue
    return patterns


# ---------------------------------------------------------------------------
# 4. SMTP verification
# ---------------------------------------------------------------------------

async def smtp_verify(email: str, timeout: int = 8) -> tuple[str, float]:
    """
    Verify an email address exists via SMTP RCPT TO — no email is sent.

    Returns (status, confidence):
      "valid"       0.92   — SMTP 250 accepted
      "invalid"     0.95   — SMTP 550/551/552 rejected
      "catchall"    0.50   — Server accepts everything (probe detected)
      "unverifiable"0.40   — Gmail/Outlook/etc — blocks probes
      "mx_exists"   0.45   — Port 25 blocked but MX record confirmed (pattern is a good guess)
      "unknown"     0.30   — Timeout / greylisting / other 4xx
    """
    domain = email.split("@")[-1].lower() if "@" in email else ""
    if not domain:
        return "invalid", 0.99

    # Skip consumer/hosted domains that block probes
    if domain in UNVERIFIABLE_DOMAINS:
        return "unverifiable", 0.40

    # Run blocking SMTP in a thread so we don't block the event loop
    return await asyncio.get_event_loop().run_in_executor(
        None, _smtp_verify_sync, email, domain, timeout
    )


def _smtp_verify_sync(email: str, domain: str, timeout: int) -> tuple[str, float]:
    """
    Synchronous SMTP verification (runs in thread pool).

    Falls back gracefully when port 25 is blocked by the ISP/host
    (common on cloud VMs and home ISPs):
      - If MX record exists but port 25 is unreachable → "mx_exists" (0.45)
      - If MX record is missing entirely → "invalid" (0.99)
    """
    try:
        # MX lookup — if this fails the domain has no email infrastructure
        mx_records = dns.resolver.resolve(domain, "MX")
        mx_host = sorted(mx_records, key=lambda r: r.preference)[0].exchange.to_text().rstrip(".")
    except dns.resolver.NXDOMAIN:
        return "invalid", 0.99
    except dns.resolver.NoAnswer:
        return "invalid", 0.95
    except Exception:
        return "unknown", 0.30

    def _probe(address: str) -> tuple[int, str]:
        """Single SMTP probe, returns (code, message)."""
        try:
            with smtplib.SMTP(timeout=timeout) as smtp:
                smtp.connect(mx_host, 25)
                smtp.ehlo("mail.hr-enricher.local")
                smtp.mail("enricher@hr-enricher.local")
                code, msg = smtp.rcpt(address)
                return code, msg.decode() if isinstance(msg, bytes) else str(msg)
        except smtplib.SMTPConnectError:
            return 0, "connect_error"
        except smtplib.SMTPServerDisconnected:
            return 0, "disconnected"
        except socket.timeout:
            return 0, "timeout"
        except OSError as e:
            # Port 25 blocked by firewall/ISP → note this specifically
            return -1, f"port25_blocked:{str(e)[:30]}"
        except Exception as e:
            return 0, str(e)

    # First probe the real email
    code, msg = _probe(email)

    if code == 250:
        # Check for catch-all: probe a clearly fake address
        fake = f"xyzfake99887766@{domain}"
        fake_code, _ = _probe(fake)
        if fake_code == 250:
            return "catchall", 0.50   # Server accepts everything
        return "valid", 0.92

    if code in (550, 551, 552, 553, 554):
        return "invalid", 0.95
    if 400 <= code < 500:
        return "unknown", 0.40    # Greylisting / temp rejection

    if code <= 0:
        # Connection failed — could be port 25 blocked or server down.
        # MX record EXISTS (we already resolved it above), so the domain
        # definitely has email infrastructure.  Return "mx_exists" so the
        # caller can decide whether to keep the candidate.
        return "mx_exists", 0.45

    return "unknown", 0.35


# ---------------------------------------------------------------------------
# 4b. Google/DDG dork email search (fallback when no domain known)
# ---------------------------------------------------------------------------

async def _dork_search_email(name: str, job_title: str, company: str) -> tuple[str, str]:
    """
    Search DDG for a person's email using dork patterns.
    Returns (email, source_label) or ("", "").
    Used as fallback when no company domain is known.
    """
    if not name:
        return "", ""

    parts = [f'"{name}"']
    if job_title and job_title.lower() not in ("", "n/a"):
        parts.append(f'"{job_title}"')
    if company and company.lower() not in _GENERIC_COMPANY_NAMES:
        parts.append(f'"{company}"')
    parts.append("email")

    query = " ".join(parts)
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"

    found_emails: list[str] = []
    try:
        async with httpx.AsyncClient(
            timeout=8,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible)"},
        ) as client:
            resp = await client.get(url)
        # Search the full SERP page for email patterns
        emails = EMAIL_PATTERN.findall(resp.text)
        for email in emails:
            email = email.lower().strip().rstrip(".")
            if _is_useful_email(email):
                found_emails.append(email)
    except Exception:
        return "", ""

    if not found_emails:
        return "", ""

    # Prefer emails that contain part of the name
    name_parts = [p.lower() for p in name.split() if len(p) > 2]
    for email in found_emails:
        local = email.split("@")[0]
        if any(p in local for p in name_parts):
            return email, "dork_name_match"

    return found_emails[0], "dork_first_result"


def _is_useful_company(company: str) -> bool:
    """Return True if company name is specific enough to domain-search."""
    if not company or len(company.strip()) < 3:
        return False
    return company.strip().lower() not in _GENERIC_COMPANY_NAMES


def _extract_company_from_snippet(snippet: str) -> str:
    """
    Try to pull a company name from a snippet like:
      'HR Manager at TechCorp · Experience: ...'
      'Head of HR | Acme Inc | LinkedIn'
    """
    if not snippet:
        return ""
    # Pattern: " at <Company>" or "| <Company> |" or "@ <Company>" or "– <Company>"
    # Terminator = boundary char or end-of-string (·|·•–—-(,()
    _T = r"(?=\s*[·|·•–—\-,()\[\]<]|\s*$)"
    for pat in [
        rf"\bat ([A-Z][A-Za-z0-9 &.,'-]{{2,40}}){_T}",
        r"\|\s*([A-Z][A-Za-z0-9 &.,'-]{2,40})\s*\|",
        rf"@\s*([A-Z][A-Za-z0-9 &.,'-]{{2,40}}){_T}",
        rf"[–—]\s*([A-Z][A-Za-z0-9 &.,'-]{{2,40}}){_T}",
    ]:
        m = re.search(pat, snippet)
        if m:
            c = m.group(1).strip().rstrip(".,")
            if _is_useful_company(c):
                return c
    return ""


# ---------------------------------------------------------------------------
# 5. Single-lead enrichment
# ---------------------------------------------------------------------------

async def enrich_lead(lead: dict, broadcast_fn: Optional[Callable] = None) -> dict:
    """
    Full enrichment pipeline for one lead dict.

    Adds/updates these fields in-place and returns the lead:
      email             — best email found
      email_verified    — "valid"|"invalid"|"catchall"|"unverifiable"|"unknown"|"found_on_site"
      email_confidence  — float 0.0–1.0
      email_source      — how it was found (e.g. "smtp_pattern", "scraped", "existing_verified")
    """

    async def _log(msg: str):
        if broadcast_fn:
            await broadcast_fn({"type": "status", "message": msg})

    name = (lead.get("contact_name") or "").strip()
    job_title = (lead.get("job_title") or "").strip()
    source_url = (lead.get("source_url") or "").strip()
    snippet = (lead.get("snippet") or lead.get("notes") or "").strip()

    # Resolve best company name: lead field → snippet fallback
    company = (lead.get("business_name") or "").strip()
    if not _is_useful_company(company):
        # Try to extract from snippet text (e.g. "HR Manager at Acme Corp")
        company_from_snippet = _extract_company_from_snippet(snippet)
        if company_from_snippet:
            company = company_from_snippet
            await _log(f"   🏢 Company extracted from snippet: {company}")

    # ── Already has an email → just verify it ────────────────────────────────
    existing_email = (lead.get("email") or "").strip()
    if existing_email:
        await _log(f"   📧 Verifying existing email: {existing_email}")
        status, conf = await smtp_verify(existing_email)
        lead["email_verified"] = status
        lead["email_confidence"] = conf
        lead["email_source"] = "existing_verified"
        icon = "✅" if status == "valid" else "⚠️"
        await _log(f"   {icon} {existing_email} → {status} ({int(conf*100)}%)")
        return lead

    # ── Find the company domain ───────────────────────────────────────────────
    domain: Optional[str] = None

    _SKIP_HOSTS = {"linkedin.com", "twitter.com", "x.com", "facebook.com",
                   "instagram.com", "github.com", "glassdoor.com", "indeed.com",
                   "xing.com", "wellfound.com"}

    # From source_url — skip social/job-board URLs
    if source_url:
        parsed_host = urlparse(source_url).netloc.replace("www.", "").lower()
        if parsed_host and not any(s in parsed_host for s in _SKIP_HOSTS):
            domain = parsed_host
            await _log(f"   📡 Domain from URL: {domain}")
        elif parsed_host:
            await _log(f"   ℹ️  Source is {parsed_host} — skipping URL domain, trying company name")

    # From company name (if URL didn't give us a domain)
    if not domain and _is_useful_company(company):
        await _log(f"   🔎 Looking up domain for: {company}")
        domain = await find_company_domain(company)
        if domain:
            await _log(f"   📡 Domain found: {domain}")
        else:
            await _log(f"   ⚠️  Could not resolve domain for: {company}")

    # No domain at all — fall back to DDG dork search
    if not domain:
        await _log(f"   🔍 No domain found — trying web search for {name}'s email...")
        dork_email, dork_source = await _dork_search_email(name, job_title, company)
        if dork_email:
            # Verify the found email before saving
            await _log(f"   📧 Found via web search: {dork_email} — verifying...")
            status, conf = await smtp_verify(dork_email)
            # Accept any non-"invalid" result — even "unknown"/"mx_exists" is better
            # than nothing when port 25 is blocked or the server is greylisting
            if status != "invalid":
                lead["email"] = dork_email
                lead["email_verified"] = status
                lead["email_confidence"] = conf
                lead["email_source"] = dork_source
                icon = "✅" if status in ("valid", "catchall") else "📧"
                await _log(f"   {icon} Email kept: {dork_email} ({status}, {int(conf*100)}%)")
                return lead
            else:
                await _log(f"   ❌ Web search email definitively rejected by SMTP — discarding")
        else:
            await _log(f"   ❌ No email found for {name or company} — no domain or web results")
        lead["email_verified"] = "not_found"
        lead["email_confidence"] = 0.0
        lead["email_source"] = "no_domain"
        return lead

    # ── Scrape company website for direct emails ──────────────────────────────
    await _log(f"   🌐 Scraping {domain} for email addresses...")
    direct_emails = await scrape_domain_emails(domain)

    if direct_emails:
        await _log(f"   📋 Found {len(direct_emails)} email(s) on site: {', '.join(direct_emails[:3])}")
        # Try to match by name
        if name:
            name_parts = [p.lower() for p in name.split() if len(p) > 2]
            for email in direct_emails:
                local = email.split("@")[0]
                if any(part in local for part in name_parts):
                    lead["email"] = email
                    lead["email_verified"] = "found_on_site"
                    lead["email_confidence"] = 0.85
                    lead["email_source"] = "scraped_name_match"
                    await _log(f"   ✅ Name-matched email: {email} (confidence 85%)")
                    return lead
        # No name match — store first as fallback, continue to SMTP patterns
        await _log(f"   📌 No name match in site emails — continuing to SMTP pattern check")
    else:
        await _log(f"   📭 No emails found on {domain} site pages")

    # ── Pattern generation + SMTP verification ────────────────────────────────
    if name and " " in name:
        parts = name.split()
        first, last = parts[0], parts[-1]
        patterns = generate_email_patterns(first, last, domain)
        await _log(f"   🔑 Trying {len(patterns)} patterns for {first} {last} @ {domain}...")

        best_mx_exists: tuple[str, float] | None = None  # best fallback when port 25 blocked

        for i, candidate in enumerate(patterns):
            status, conf = await smtp_verify(candidate)

            if status == "valid":
                icon = "✅"
                await _log(f"      {icon} {candidate} → {status} ({int(conf*100)}%)")
                lead["email"] = candidate
                lead["email_verified"] = "valid"
                lead["email_confidence"] = conf
                lead["email_source"] = f"smtp_pattern_{i+1}"
                await _log(f"   ✅ Email confirmed: {candidate} (confidence {int(conf*100)}%)")
                return lead

            if status == "catchall":
                icon = "🔶"
                await _log(f"      {icon} {candidate} → {status} ({int(conf*100)}%)")
                lead["email"] = candidate
                lead["email_verified"] = "catchall"
                lead["email_confidence"] = conf
                lead["email_source"] = f"smtp_pattern_{i+1}"
                await _log(f"   🔶 Catch-all domain — using best pattern: {candidate}")
                return lead

            if status == "mx_exists" and best_mx_exists is None:
                # Port 25 is blocked — record the first (most statistically likely) pattern
                best_mx_exists = (candidate, conf)
                icon = "📡"
                await _log(f"      {icon} {candidate} → {status} (port 25 blocked, MX confirmed)")
                # Don't keep trying patterns — port 25 is blocked for ALL of them
                break

            if status == "invalid":
                await _log(f"      ❌ {candidate} → {status}")
                await asyncio.sleep(0.3)
                continue

            await _log(f"      ⚠️  {candidate} → {status} ({int(conf*100)}%)")
            await asyncio.sleep(0.5 + 0.5 * (i % 3))

        # If port 25 was blocked but MX exists, keep the best-pattern candidate
        if best_mx_exists and not lead.get("email"):
            candidate, conf = best_mx_exists
            lead["email"] = candidate
            lead["email_verified"] = "mx_exists"
            lead["email_confidence"] = conf
            lead["email_source"] = "pattern_mx_confirmed"
            await _log(f"   📡 Port 25 blocked — using most likely pattern: {candidate} (MX confirmed, {int(conf*100)}%)")

    # ── Fallback: use best direct site email if SMTP patterns all failed ──────
    if not lead.get("email") and direct_emails:
        lead["email"] = direct_emails[0]
        lead["email_verified"] = "found_on_site"
        lead["email_confidence"] = 0.55
        lead["email_source"] = "scraped_fallback"
        await _log(f"   📌 Fallback: using site email {direct_emails[0]} (confidence 55%)")
        return lead

    # ── Nothing found ─────────────────────────────────────────────────────────
    if not lead.get("email"):
        await _log(f"   ❌ No email found for {name or company or '(unknown)'}")
        lead["email_verified"] = "not_found"
        lead["email_confidence"] = 0.0
        lead["email_source"] = "not_found"

    return lead


# ---------------------------------------------------------------------------
# 6. Batch enrichment
# ---------------------------------------------------------------------------

async def enrich_leads_batch(
    leads: list[dict],
    broadcast_fn: Optional[Callable] = None,
    stop_flag_fn: Optional[Callable[[], bool]] = None,
    max_smtp_checks: int = 50,
) -> list[dict]:
    """
    Enrich a list of leads sequentially.

    - Skips leads that already have email_confidence > 0.80
    - Broadcasts verbose progress messages
    - Respects stop_flag_fn() to cancel mid-run
    - Caps total SMTP RCPT TO checks at max_smtp_checks

    Returns the same list (mutated in place) with enriched fields.
    """

    async def _log(msg: str):
        if broadcast_fn:
            await broadcast_fn({"type": "status", "message": msg})

    needs = [l for l in leads if not l.get("email") or l.get("email_confidence", 0) < 0.80]
    total = len(needs)

    if total == 0:
        await _log("🔬 All leads already have verified emails — enrichment skipped.")
        return leads

    await _log(f"🔬 Enrichment phase starting: {total} lead(s) need email lookup")
    smtp_checks_used = 0
    enriched_count = 0

    for i, lead in enumerate(needs):
        if stop_flag_fn and stop_flag_fn():
            await _log("⏹ Enrichment stopped by user.")
            break

        if smtp_checks_used >= max_smtp_checks:
            await _log(f"⚠️  SMTP check cap ({max_smtp_checks}) reached — pausing enrichment.")
            break

        name = lead.get("contact_name") or lead.get("business_name") or "Unknown"
        company = lead.get("business_name") or ""
        await _log(f"🔬 Enriching lead {i+1}/{total}: {name}" + (f" ({company})" if company and company != name else ""))

        # Count approximate SMTP calls this lead will make (max 15 patterns + 1 catch-all probe)
        prev_email = lead.get("email", "")
        await enrich_lead(lead, broadcast_fn=broadcast_fn)
        smtp_checks_used += 16  # conservative estimate per lead

        if lead.get("email") and not prev_email:
            enriched_count += 1

        # Broadcast progress for UI progress bar
        if broadcast_fn:
            await broadcast_fn({
                "type": "enrichment_progress",
                "current": i + 1,
                "total": total,
                "lead": name,
            })

        # Pause between leads (avoid hammering MX servers)
        await asyncio.sleep(0.5)

    await _log(f"✅ Enrichment complete: {enriched_count}/{total} leads gained a new email address")
    return leads
