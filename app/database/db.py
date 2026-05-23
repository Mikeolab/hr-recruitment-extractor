"""
Database Module — SQLite (local) or PostgreSQL/Neon (when DATABASE_URL is set).
"""
from __future__ import annotations
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dataclasses import dataclass, field
from app.config import DATABASE_PATH

# When DATABASE_URL is set (e.g. on Render + Neon), use PostgreSQL
_use_postgres = bool(os.environ.get("DATABASE_URL"))

if _use_postgres:
    from app.database import db_postgres as _db
else:
    _db = None


def get_connection():
    """Get a database connection, creating the DB and tables if needed."""
    if _use_postgres and _db:
        return _db.get_connection()
    # SQLite path
    # Ensure directory exists
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(DATABASE_PATH), timeout=60)
    conn.row_factory = sqlite3.Row
    # allow longer write-lock wait to avoid locked errors when concurrent threads hit DB
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.execute("PRAGMA journal_mode=WAL")

    # Create tables
    conn.executescript("""        CREATE TABLE IF NOT EXISTS searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            num_results INTEGER DEFAULT 0,
            num_leads INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            search_id INTEGER,
            business_name TEXT DEFAULT '',
            contact_name TEXT DEFAULT '',
            email TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            website TEXT DEFAULT '',
            source_url TEXT DEFAULT '',
            snippet TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (search_id) REFERENCES searches(id)
        );

        CREATE INDEX IF NOT EXISTS idx_leads_search_id ON leads(search_id);
        CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);
        CREATE INDEX IF NOT EXISTS idx_searches_created_at ON searches(created_at);

        -- External leads table for user-uploaded leads
        CREATE TABLE IF NOT EXISTS external_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_name TEXT DEFAULT '',
            contact_name TEXT DEFAULT '',
            email TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            website TEXT DEFAULT '',
            domain TEXT DEFAULT '',
            source TEXT DEFAULT 'manual_upload',
            file_name TEXT,
            upload_batch_id TEXT,
            is_validated BOOLEAN DEFAULT 0,
            validation_status TEXT DEFAULT 'pending',
            duplicate_of_search_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_external_leads_email ON external_leads(email);
        CREATE INDEX IF NOT EXISTS idx_external_leads_domain ON external_leads(domain);
        CREATE INDEX IF NOT EXISTS idx_external_leads_batch ON external_leads(upload_batch_id);
    """)

    conn.commit()
    return conn


def save_search(query: str, num_results: int = 0) -> int:
    """Save a search record and return its ID."""
    if _use_postgres and _db:
        return _db.save_search(query, num_results)
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO searches (query, num_results) VALUES (?, ?)",
        (query, num_results),
    )
    search_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return search_id


def save_leads(search_id: int, leads: list[dict], replace: bool = False) -> int:
    """
    Save a batch of leads for a search.

    Args:
        search_id: The search ID to associate leads with
        leads: List of lead dicts with keys: business_name, contact_name,
               email, phone, website, source_url, snippet
        replace: If True, delete existing leads for this search first (for incremental saves)

    Returns:
        Number of leads saved
    """
    if not leads:
        return 0
    if _use_postgres and _db:
        return _db.save_leads(search_id, leads, replace=replace)

    conn = get_connection()
    if replace:
        conn.execute("DELETE FROM leads WHERE search_id = ?", (search_id,))
    count = 0

    for lead in leads:
        conn.execute(
            """INSERT INTO leads 
               (search_id, business_name, contact_name, email, phone, website, source_url, snippet)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                search_id,
                lead.get("business_name", ""),
                lead.get("contact_name", ""),
                lead.get("email", ""),
                lead.get("phone", ""),
                lead.get("website", ""),
                lead.get("source_url", ""),
                lead.get("snippet", ""),
            ),
        )
        count += 1

    # Update search record with lead count
    conn.execute(
        "UPDATE searches SET num_leads = ? WHERE id = ?",
        (count, search_id),
    )

    conn.commit()
    conn.close()
    return count


def get_leads_without_email(search_id: int | None = None) -> list[dict]:
    """
    Return all leads that have no email address, optionally filtered by search_id.
    Each dict includes the lead 'id' field so it can be updated later.
    """
    conn = get_connection()
    if search_id is not None:
        rows = conn.execute(
            """SELECT id, search_id, business_name, contact_name, email, phone,
                      website, source_url, snippet
               FROM leads
               WHERE (email IS NULL OR email = '') AND search_id = ?""",
            (search_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, search_id, business_name, contact_name, email, phone,
                      website, source_url, snippet
               FROM leads
               WHERE (email IS NULL OR email = '')""",
        ).fetchall()
    conn.close()
    keys = ["id", "search_id", "business_name", "contact_name", "email",
            "phone", "website", "source_url", "snippet"]
    return [dict(zip(keys, row)) for row in rows]


def update_lead_email(
    lead_id: int,
    email: str,
    verified_status: str,
    confidence: float,
    source: str,
) -> None:
    """
    Update a lead's email address and write enrichment metadata to hr_lead_enrichment.

    verified_status: "valid" | "found_on_site" | "catchall" | "unverifiable" | "unknown"
    confidence:      float 0.0–1.0
    source:          how the email was found (e.g. "smtp_pattern_3", "scraped_name_match")
    """
    from datetime import datetime

    conn = get_connection()
    # Update the email on the lead itself
    conn.execute(
        "UPDATE leads SET email = ? WHERE id = ?",
        (email, lead_id),
    )
    # Upsert into hr_lead_enrichment
    is_verified = 1 if verified_status == "valid" else 0
    notes = f"status={verified_status} confidence={confidence:.2f} source={source}"
    conn.execute(
        """INSERT INTO hr_lead_enrichment
               (lead_id, verified_email, last_enriched_at, data_source, enrichment_notes)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(lead_id) DO UPDATE SET
               verified_email    = excluded.verified_email,
               last_enriched_at  = excluded.last_enriched_at,
               data_source       = excluded.data_source,
               enrichment_notes  = excluded.enrichment_notes""",
        (lead_id, is_verified, datetime.utcnow().isoformat(), source, notes),
    )
    conn.commit()
    conn.close()


def get_recent_searches(limit: int = 20) -> list[dict]:
    """Get recent search history."""
    if _use_postgres and _db:
        return _db.get_recent_searches(limit)
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM searches ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_searches_for_query(query_text: str, limit: int = 40) -> list[dict]:
    """
    All saved sessions whose query matches (case-insensitive, trimmed).
    Used on Live page to merge/validate/download runs for the same query without opening Saved Leads.
    """
    if not query_text or not str(query_text).strip():
        return []
    if _use_postgres and _db:
        return _db.get_searches_for_query(query_text, limit)
    key = query_text.strip().lower()
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM searches
           WHERE LOWER(TRIM(query)) = ?
           ORDER BY created_at DESC LIMIT ?""",
        (key, limit),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_searches_for_queries(queries: list[str], limit: int = 50) -> list[dict]:
    """Union of sessions matching any query string (normalized), newest first."""
    if not queries:
        return []
    seen: set[int] = set()
    out: list[dict] = []
    for qt in queries:
        q = (qt or "").strip()
        if not q:
            continue
        for s in get_searches_for_query(q, limit=limit):
            sid = s.get("id")
            if sid is not None and sid not in seen:
                seen.add(sid)
                out.append(s)
    out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return out[:limit]


def get_leads_by_search(search_id: int) -> list[dict]:
    """Get all leads for a specific search."""
    if _use_postgres and _db:
        return _db.get_leads_by_search(search_id)
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM leads WHERE search_id = ? ORDER BY id",
        (search_id,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_all_leads(limit: int = 1000) -> list[dict]:
    """Get all leads across all searches."""
    if _use_postgres and _db:
        return _db.get_all_leads(limit)
    conn = get_connection()
    rows = conn.execute(
        """SELECT l.*, s.query as search_query 
           FROM leads l 
           LEFT JOIN searches s ON l.search_id = s.id 
           ORDER BY l.created_at DESC 
           LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def delete_search(search_id: int) -> None:
    """Delete a search and its associated leads."""
    if _use_postgres and _db:
        return _db.delete_search(search_id)
    conn = get_connection()
    conn.execute("DELETE FROM leads WHERE search_id = ?", (search_id,))
    conn.execute("DELETE FROM searches WHERE id = ?", (search_id,))
    conn.commit()
    conn.close()


def delete_leads_by_ids(lead_ids: list) -> int:
    """Delete specific leads by ID from the leads table. Returns number deleted."""
    if not lead_ids:
        return 0
    conn = get_connection()
    placeholders = ",".join("?" for _ in lead_ids)
    cursor = conn.execute(
        f"DELETE FROM leads WHERE id IN ({placeholders})", lead_ids
    )
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


def prune_old_searches_and_leads(retention_days: int | None = None) -> dict:
    """
    Delete search sessions and their leads older than retention_days (by searches.created_at).
    Set env LEADS_RETENTION_DAYS=0 to disable pruning from callers that check this.
    """
    if retention_days is None:
        retention_days = int(os.environ.get("LEADS_RETENTION_DAYS", "30"))
    if retention_days <= 0:
        return {"skipped": True, "reason": "LEADS_RETENTION_DAYS <= 0"}

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    if _use_postgres and _db:
        return _db.prune_old_searches_and_leads_cutoff(cutoff)

    conn = get_connection()
    n_leads = conn.execute(
        "SELECT COUNT(*) FROM leads WHERE search_id IN (SELECT id FROM searches WHERE created_at < ?)",
        (cutoff_str,),
    ).fetchone()[0]
    n_search = conn.execute(
        "SELECT COUNT(*) FROM searches WHERE created_at < ?",
        (cutoff_str,),
    ).fetchone()[0]
    conn.execute(
        "DELETE FROM leads WHERE search_id IN (SELECT id FROM searches WHERE created_at < ?)",
        (cutoff_str,),
    )
    conn.execute("DELETE FROM searches WHERE created_at < ?", (cutoff_str,))
    conn.commit()
    conn.close()
    return {
        "searches_deleted": n_search,
        "leads_deleted": n_leads,
        "retention_days": retention_days,
        "cutoff_utc": cutoff_str,
    }


def maybe_prune_stale_searches(interval_hours: float = 24.0) -> dict | None:
    """
    Run prune_old_searches_and_leads at most once per interval_hours (persisted next to DB file).
    Safe to call on every Streamlit rerun / server startup.
    """
    retention_days = int(os.environ.get("LEADS_RETENTION_DAYS", "30"))
    if retention_days <= 0:
        return None
    marker = DATABASE_PATH.parent / ".leads_retention_last_prune"
    try:
        if marker.exists():
            last = float(marker.read_text().strip())
            if time.time() - last < interval_hours * 3600:
                return None
    except (ValueError, OSError):
        pass
    result = prune_old_searches_and_leads(retention_days=retention_days)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(time.time()))
    except OSError:
        pass
    return result


def get_lead_stats() -> dict:
    """Get summary statistics about all leads."""
    if _use_postgres and _db:
        return _db.get_lead_stats()
    conn = get_connection()

    total_searches = conn.execute("SELECT COUNT(*) FROM searches").fetchone()[0]
    total_leads = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    unique_emails = conn.execute(
        "SELECT COUNT(DISTINCT email) FROM leads WHERE email != ''"
    ).fetchone()[0]
    unique_domains = conn.execute(
        "SELECT COUNT(DISTINCT website) FROM leads WHERE website != ''"
    ).fetchone()[0]

    conn.close()

    return {
        "total_searches": total_searches,
        "total_leads": total_leads,
        "unique_emails": unique_emails,
        "unique_domains": unique_domains,
    }

