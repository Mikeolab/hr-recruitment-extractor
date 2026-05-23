"""
HR Recruitment Extractor - Modern UI
Find and track HR decision makers and hiring managers.
"""
from __future__ import annotations

import os
import sys
import streamlit as st
import pandas as pd
import json
import threading
import time
import websocket
from queue import Queue, Empty
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

# Add parent directory to path so we can import app module
sys.path.insert(0, str(Path(__file__).parent.parent))

# Thread-safe ref for WebSocket
_ws_client_ref = [None]

from app.config import APP_NAME, APP_VERSION, APP_SUBTITLE, EXPORT_DIR, WEBSOCKET_URL, AUTOMATION_SERVER_URL, PROJECT_ROOT
from app.database.db import (
    get_all_leads,
    get_lead_stats,
    get_searches_for_queries,
    get_leads_by_search,
    maybe_prune_stale_searches,
    save_search,
    save_leads,
    get_connection,
)
from app.database.hr_schema import init_hr_schema
from app.export.exporter import (
    export_to_csv,
    export_to_excel,
    COLUMN_PRESETS,
    leads_to_dataframe,
    filter_merged_leads_for_export,
)
from app.config_manager import load_settings, save_settings
from app.extractors.hr_title_extractor import extract_title, extract_company_info, extract_open_positions


# ─── HR Query Builder Templates ───────────────────────────────────────────────
# Each template pre-fills the three query builder columns independently.
# Titles × Patterns × Industries generates the full cross-product of queries.
HR_SEARCH_TEMPLATES: dict = {
    # ── Quick Start: focused, proven patterns that produce real emails ──────────
    "🚀 Quick Start — HR Contacts": {
        "titles":    "HR Manager\nHR Director\nTalent Acquisition Manager",
        "patterns":  "email contact\nstaff directory filetype:pdf\ncontact us email",
        "industries": "technology company\nhealthcare\nfinancial services",
    },
    # ── PDF directories: universities, hospitals, gov agencies publish these ──
    "📄 PDF Staff Directories": {
        "titles":    "HR Manager\nHR Director\nHuman Resources",
        "patterns":  "staff directory filetype:pdf\ncontact list filetype:pdf\nteam directory filetype:pdf\nemail directory filetype:pdf",
        "industries": "university\nhospital\ngovernment agency\nnon-profit organization\ncounty",
    },
    # ── Company team / contact pages — processed directly as HTML ─────────────
    "🌐 Company HR Contact Pages": {
        "titles":    "HR Manager\nRecruitment Manager\nPeople Operations Manager\nHead of HR",
        "patterns":  "email contact team\nour team email\ncontact us hr department",
        "industries": "tech startup\nSaaS company\ndigital agency\nfintech\nenterprise software",
    },
    # ── Talent acquisition & recruiting teams ─────────────────────────────────
    "🎯 Talent Acquisition Teams": {
        "titles":    "Talent Acquisition Manager\nSenior Recruiter\nHead of Talent\nTA Director",
        "patterns":  "email contact\nteam email\nrecruiter contact filetype:pdf",
        "industries": "technology\nfinancial services\nhealthcare\nmanufacturing",
    },
    # ── Senior HR leaders: VP, Director, CHRO ─────────────────────────────────
    "👔 HR Directors & VPs": {
        "titles":    "HR Director\nVP of People\nChief People Officer\nDirector of Human Resources",
        "patterns":  "email contact\nleadership team email\nexecutive contact filetype:pdf",
        "industries": "Fortune 500\nenterprise\ntechnology\nhealthcare system\nfinancial institution",
    },
    # ── Staffing & recruiting agencies — they actively look for candidates ─────
    "🏢 Staffing & Recruiting Agencies": {
        "titles":    "Corporate Recruiter\nStaffing Manager\nRecruitment Director\nAccount Manager",
        "patterns":  "email contact\nagency team email\nstaff directory filetype:pdf",
        "industries": "staffing agency\nrecruitment firm\nHR consulting\nheadhunting",
    },
    # ── HR conferences & associations — member directories & attendee lists ───
    "🎪 HR Conference Attendees & Members": {
        "titles":    "HR Manager\nHR Director\nCHRO\nPeople Operations\nTalent Acquisition",
        "patterns":  "attendees filetype:pdf\nspeakers filetype:pdf\nmembers directory filetype:pdf\nconference program filetype:pdf",
        "industries": "SHRM\nHR Tech conference\ntalent summit\npeople analytics\nworkforce conference",
    },
    # ── Tech company hiring teams ─────────────────────────────────────────────
    "💻 Tech Company Hiring Teams": {
        "titles":    "Engineering Recruiter\nTech Recruiter\nHiring Manager\nTechnical Sourcer",
        "patterns":  "email contact team\nour team email\nrecruiting contact filetype:pdf",
        "industries": "software company\nSaaS\ncloud computing\ncybersecurity\nartificial intelligence",
    },
}


def generate_hr_queries(titles: list, patterns: list, industries: list) -> list:
    """
    HR cross-product query generator: Title × Pattern × Industry.

    Each title is wrapped in quotes for exact phrase matching.
    If industries list is empty, generates Title × Pattern only.
    """
    queries = []
    for title in titles:
        title = title.strip()
        if not title:
            continue
        for pattern in patterns:
            pattern = pattern.strip()
            if not pattern:
                continue
            if industries:
                for industry in industries:
                    industry = industry.strip()
                    if not industry:
                        continue
                    q = f'"{title}" {pattern} {industry}'
                    queries.append(q)
            else:
                queries.append(f'"{title}" {pattern}')
    return queries


# ─── Page Config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title=APP_NAME,
    page_icon="👔",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Initialize HR schema
try:
    conn = get_connection()
    init_hr_schema(conn)
    conn.close()
except Exception as e:
    st.warning(f"Schema initialization: {e}")

# Delete lead/search data older than retention days
try:
    maybe_prune_stale_searches(interval_hours=24.0)
except Exception:
    pass


# ─── Custom CSS - Modern Design ──────────────────────────────────────────────
st.markdown("""
<style>
    * {
        margin: 0;
        padding: 0;
    }

    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    .block-container {
        padding-top: 2rem;
        max-width: 1400px;
    }

    :root {
        --primary: #5B4FFF;
        --primary-dark: #4A3ACC;
        --success: #10B981;
        --warning: #F59E0B;
        --danger: #EF4444;
        --bg-light: #F8FAFC;
        --bg-card: #FFFFFF;
        --text-primary: #1E293B;
        --text-secondary: #64748B;
        --border: #E2E8F0;
    }

    .header-container {
        background: linear-gradient(135deg, #5B4FFF 0%, #7C3AED 100%);
        padding: 3rem 2rem;
        border-radius: 16px;
        color: white;
        margin-bottom: 2rem;
        box-shadow: 0 4px 20px rgba(91, 79, 255, 0.2);
    }

    .header-title {
        font-size: 2.5rem;
        font-weight: 700;
        margin: 0;
        letter-spacing: -0.5px;
    }

    .header-subtitle {
        font-size: 1.1rem;
        opacity: 0.95;
        margin-top: 0.5rem;
        font-weight: 300;
    }

    .header-meta {
        display: flex;
        gap: 2rem;
        margin-top: 1.5rem;
        font-size: 0.95rem;
        opacity: 0.9;
    }

    .metric-item {
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }

    .card-container {
        background: white;
        border-radius: 12px;
        padding: 1.5rem;
        border: 1px solid #E2E8F0;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
        margin-bottom: 1.5rem;
    }

    .card-title {
        font-size: 1.3rem;
        font-weight: 600;
        color: #1E293B;
        margin-bottom: 1rem;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }

    .stat-box {
        background: linear-gradient(135deg, #F0F4FF 0%, #F5F3FF 100%);
        border: 1px solid #E2E8F0;
        border-radius: 12px;
        padding: 1.5rem;
        text-align: center;
        border-left: 4px solid #5B4FFF;
    }

    .stat-number {
        font-size: 2rem;
        font-weight: 700;
        color: #5B4FFF;
    }

    .stat-label {
        font-size: 0.9rem;
        color: #64748B;
        margin-top: 0.5rem;
    }

    .activity-log {
        background: #0F172A;
        border: 1px solid #1E293B;
        border-radius: 8px;
        padding: 1rem;
        font-family: 'Menlo', 'Consolas', monospace;
        font-size: 0.82rem;
        color: #94A3B8;
        height: 340px;
        overflow-y: auto;
        line-height: 1.7;
    }

    .log-entry {
        padding: 0.15rem 0;
    }

    .log-success { color: #34D399; }
    .log-warning { color: #FBBF24; }
    .log-error { color: #F87171; }
    .log-info { color: #94A3B8; }

    .extraction-status-bar {
        background: linear-gradient(90deg, #1E3A5F 0%, #1a2d4a 100%);
        border: 1px solid #2563EB;
        border-radius: 8px;
        padding: 0.75rem 1rem;
        display: flex;
        align-items: center;
        gap: 0.75rem;
        margin-bottom: 1rem;
        font-size: 0.9rem;
        color: #93C5FD;
    }

    .live-dot {
        width: 10px;
        height: 10px;
        background: #22C55E;
        border-radius: 50%;
        display: inline-block;
        animation: pulse 1.5s infinite;
    }

    @keyframes pulse {
        0%, 100% { opacity: 1; transform: scale(1); }
        50% { opacity: 0.5; transform: scale(0.8); }
    }

    .extraction-meta {
        color: #64748B;
        font-size: 0.82rem;
    }

    .status-bar {
        background: #F8FAFC;
        border: 1px solid #E2E8F0;
        border-radius: 8px;
        padding: 0.5rem 1rem;
        font-size: 0.85rem;
        color: #64748B;
        margin-top: 0.5rem;
    }

    .badge {
        display: inline-block;
        padding: 0.25rem 0.75rem;
        border-radius: 20px;
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
    }

    .badge-success { background: #D1FAE5; color: #047857; }
    .badge-warning { background: #FEF3C7; color: #D97706; }
    .badge-info { background: #DBEAFE; color: #0284C7; }
    .badge-primary { background: #EDE9FE; color: #5B4FFF; }

    .section-divider {
        height: 1px;
        background: #E2E8F0;
        margin: 2rem 0;
    }

    .sr-only {
        position: absolute;
        width: 1px;
        height: 1px;
        padding: 0;
        margin: -1px;
        overflow: hidden;
        clip: rect(0, 0, 0, 0);
        white-space: nowrap;
        border-width: 0;
    }

    .query-count-badge {
        background: linear-gradient(135deg, #5B4FFF, #7C3AED);
        color: white;
        border-radius: 6px;
        padding: 0.4rem 0.9rem;
        font-size: 0.88rem;
        font-weight: 600;
        display: inline-block;
        margin-top: 0.5rem;
    }
</style>

<div class="sr-only">
    <h1>HR Recruitment Extractor - Find HR Decision Makers & Hiring Managers</h1>
</div>
""", unsafe_allow_html=True)


# ─── Session State Initialization ────────────────────────────────────────────
if "activity_log" not in st.session_state:
    st.session_state.activity_log = []
if "current_leads" not in st.session_state:
    st.session_state.current_leads = []
if "extracted_leads" not in st.session_state:
    st.session_state.extracted_leads = []
if "is_running" not in st.session_state:
    st.session_state.is_running = False
if "ws_connected" not in st.session_state:
    st.session_state.ws_connected = False
if "current_screenshot" not in st.session_state:
    st.session_state.current_screenshot = None
if "ws_message_queue" not in st.session_state:
    st.session_state.ws_message_queue = None
if "query_current" not in st.session_state:
    st.session_state.query_current = ""
if "query_progress_idx" not in st.session_state:
    st.session_state.query_progress_idx = 0
if "query_total" not in st.session_state:
    st.session_state.query_total = 0
if "emails_collected" not in st.session_state:
    st.session_state.emails_collected = 0
if "websocket_connected" not in st.session_state:
    st.session_state.websocket_connected = False
if "_prev_template_sel" not in st.session_state:
    st.session_state._prev_template_sel = "— Select a template —"
# Enrichment state
if "enrichment_active" not in st.session_state:
    st.session_state.enrichment_active = False
if "enrichment_current" not in st.session_state:
    st.session_state.enrichment_current = 0
if "enrichment_total" not in st.session_state:
    st.session_state.enrichment_total = 0
if "enrichment_done_count" not in st.session_state:
    st.session_state.enrichment_done_count = 0
if "enrichment_processed" not in st.session_state:
    st.session_state.enrichment_processed = 0
# Query builder columns — each is a newline-separated list
if "qb_titles" not in st.session_state:
    st.session_state.qb_titles = ""
if "qb_patterns" not in st.session_state:
    st.session_state.qb_patterns = ""
if "qb_industries" not in st.session_state:
    st.session_state.qb_industries = ""


# ─── WebSocket Client ─────────────────────────────────────────────────────────
def websocket_client(
    queries: List[str],
    max_pages: int,
    delay_pages: float,
    delay_actions: float,
    msg_queue: Queue,
    target_leads: int,
    search_engine: str,
    headless: bool,
    reload_between_queries: bool = False,
    email_domain_allowlist: str = "",
    search_site_domains: str = "",
    auto_enrich: bool = True,
):
    """Connect to automation WebSocket server and stream results into msg_queue."""
    def on_message(ws, message):
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            if msg_type == "status":
                msg_queue.put(("status", data.get("message", "")))
            elif msg_type == "screenshot":
                msg_queue.put(("screenshot", data.get("data", "")))
            elif msg_type == "ping":
                pass
            elif msg_type == "leads":
                msg_queue.put(("leads", data.get("data", [])))
            elif msg_type == "lead_count":
                msg_queue.put(("lead_count", (data.get("count", 0), data.get("target", 0))))
            elif msg_type == "query_progress":
                msg_queue.put(("query_progress", {
                    "current_query": data.get("current_query", ""),
                    "current": data.get("current", 0),
                    "total": data.get("total", 0),
                }))
            elif msg_type == "complete":
                msg_queue.put(("complete", data.get("data", [])))
            elif msg_type == "error":
                msg_queue.put(("error", data.get("message", "")))
        except Exception as e:
            msg_queue.put(("status", f"❌ Error: {str(e)}"))

    def on_error(ws, error):
        msg_queue.put(("error", str(error)))
        msg_queue.put(("closed", None))

    def on_close(ws, close_status_code, close_msg):
        msg_queue.put(("closed", None))

    def on_open(ws):
        _ws_client_ref[0] = ws
        msg_queue.put(("connected", None))
        ws.send(json.dumps({
            "command": "start",
            "queries": queries,
            "max_pages": max_pages,
            "delay_pages": delay_pages,
            "delay_actions": delay_actions,
            "target_leads": target_leads,
            "search_engine": search_engine,
            "headless": headless,
            "reload_between_queries": reload_between_queries,
            "email_domain_allowlist": email_domain_allowlist,
            "search_site_domains": search_site_domains,
            "auto_enrich": auto_enrich,
        }))

    MAX_RECONNECT_ATTEMPTS = 5
    reconnect_delay = 3  # seconds

    for attempt in range(MAX_RECONNECT_ATTEMPTS):
        try:
            _ws_client_ref[0] = None
            ws = websocket.WebSocketApp(
                WEBSOCKET_URL,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
                on_open=on_open,
            )
            ws.run_forever(ping_interval=25, ping_timeout=10)
        except Exception as e:
            msg_queue.put(("error", str(e)))
        finally:
            _ws_client_ref[0] = None

        # Check if we received a clean "complete" or if we should retry
        # We peek at the queue — if "complete" or "closed" was the last msg, stop retrying
        # Heuristic: if connection was alive for > 5s, don't auto-retry (likely intentional close)
        # Just send closed and exit — let the user click Start again if needed
        break  # Exit after first attempt (retries handled by UI reconnect button)

    msg_queue.put(("closed", None))


def run_websocket_client(
    queries: List[str],
    max_pages: int,
    delay_pages: float,
    delay_actions: float,
    msg_queue: Queue,
    target_leads: int,
    search_engine: str,
    headless: bool,
    reload_between_queries: bool = False,
    email_domain_allowlist: str = "",
    search_site_domains: str = "",
    auto_enrich: bool = True,
):
    websocket_client(
        queries, max_pages, delay_pages, delay_actions,
        msg_queue, target_leads, search_engine, headless,
        reload_between_queries, email_domain_allowlist, search_site_domains,
        auto_enrich=auto_enrich,
    )


def drain_ws_queue(msg_queue: Queue) -> bool:
    """Drain WebSocket message queue into session state. Returns True if UI needs refresh."""
    if msg_queue is None:
        return False
    changed = False
    while True:
        try:
            item = msg_queue.get_nowait()
        except Empty:
            break
        msg_type, data = item
        if msg_type == "status":
            st.session_state.activity_log.append(data)
            if len(st.session_state.activity_log) > 200:
                st.session_state.activity_log = st.session_state.activity_log[-200:]
            changed = True
        elif msg_type == "screenshot" and data:
            if not hasattr(st.session_state, "last_screenshot_update"):
                st.session_state.last_screenshot_update = 0
            now = time.time()
            if (now - st.session_state.last_screenshot_update) >= 0.5:
                st.session_state.current_screenshot = data
                st.session_state.last_screenshot_update = now
                changed = True
        elif msg_type == "leads":
            st.session_state.extracted_leads.extend(data)
            st.session_state.current_leads = st.session_state.extracted_leads
            changed = True
        elif msg_type == "lead_count":
            st.session_state.emails_collected = data[0]
            changed = True
        elif msg_type == "query_progress":
            st.session_state.query_current = data.get("current_query", "")
            st.session_state.query_progress_idx = data.get("current", 0)
            st.session_state.query_total = data.get("total", 0)
            changed = True
        elif msg_type == "complete":
            if data:
                st.session_state.extracted_leads = data
                st.session_state.current_leads = data
            st.session_state.activity_log.append("🎉 Extraction complete!")
            st.session_state.is_running = False
            st.session_state.query_current = "✅ Done"
            st.session_state._terminal_rerun = True
            changed = True
        elif msg_type == "error":
            st.session_state.activity_log.append(f"❌ {data}")
            if "fatal" in str(data).lower() or "crash" in str(data).lower():
                st.session_state.is_running = False
                st.session_state._terminal_rerun = True
            changed = True
        elif msg_type == "connected":
            st.session_state.websocket_connected = True
            st.session_state.activity_log.append("✅ Connected to automation server — starting...")
            changed = True
        elif msg_type == "closed":
            st.session_state.websocket_connected = False
            st.session_state.is_running = False
            st.session_state._terminal_rerun = True
            changed = True
        elif msg_type == "enrichment_progress":
            st.session_state.enrichment_active = True
            st.session_state.enrichment_current = data.get("current", 0)
            st.session_state.enrichment_total = data.get("total", 1)
            changed = True
        elif msg_type == "enrichment_complete":
            st.session_state.enrichment_active = False
            st.session_state.enrichment_done_count = data.get("total_enriched", 0)
            st.session_state.enrichment_processed = data.get("total_processed", 0)
            changed = True
    return changed


def _escape_html(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_live_panel():
    """Live browser screenshot + activity log panel."""
    n = len(st.session_state.extracted_leads)
    last = str(st.session_state.activity_log[-1]) if st.session_state.activity_log else ""

    if st.session_state.is_running:
        st.markdown(
            '<div class="extraction-status-bar">'
            '<span class="live-dot"></span>'
            "<strong>Extracting HR Leads</strong>"
            f'<span class="extraction-meta">&nbsp;·&nbsp;{n} lead(s) collected · live</span>'
            "</div>",
            unsafe_allow_html=True,
        )
        if last:
            st.markdown(
                f'<div class="extraction-detail" style="font-size:0.82rem;color:#64748B;margin-bottom:0.75rem;'
                f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"'
                f'title="{_escape_html(last)}">{_escape_html(last)}</div>',
                unsafe_allow_html=True,
            )

    col_browser, col_log = st.columns([1.2, 0.8])
    with col_browser:
        st.markdown("#### 🖥️ Live Browser View")
        if st.session_state.current_screenshot:
            st.image(
                f"data:image/png;base64,{st.session_state.current_screenshot}",
                use_container_width=True,
                caption="Live browser automation",
            )
        else:
            st.info("Click **▶️ Start Search** to see live browser automation here.")
    with col_log:
        st.markdown("#### 📋 Activity Log")
        log_lines = st.session_state.activity_log[-60:]
        log_html = ""
        for line in log_lines:
            text = str(line)
            css = (
                "log-success" if any(x in text for x in ["✅", "🎉", "💾"]) else
                "log-error" if any(x in text for x in ["❌", "✕"]) else
                "log-warning" if any(x in text for x in ["⚠️", "⏹"]) else
                "log-info"
            )
            escaped = _escape_html(text)
            log_html += f'<div class="log-entry"><span class="{css}">{escaped}</span></div>'
        if not log_html:
            log_html = '<span class="log-info">Waiting for automation activity...</span>'
        st.markdown(f'<div class="activity-log">{log_html}</div>', unsafe_allow_html=True)


@st.fragment(run_every=timedelta(seconds=1.15))
def _live_automation_fragment():
    """Poll WebSocket queue and refresh live panel every ~1s — runs independently of the main page."""
    q = st.session_state.get("ws_message_queue")
    if q:
        drain_ws_queue(q)
    if st.session_state.pop("_terminal_rerun", False):
        st.rerun()

    enriching = st.session_state.get("enrichment_active", False)
    is_running = st.session_state.is_running

    # ── Status bar ─────────────────────────────────────────────────────────────
    if is_running:
        status_txt = "🔄 Running..."
    elif enriching:
        status_txt = "🔬 Enriching emails..."
    else:
        status_txt = "✅ Ready"
    status_txt += " · ✅ Server connected" if st.session_state.websocket_connected else " · ⚠️ Server not connected"
    st.markdown(f'<div class="status-bar">{status_txt}</div>', unsafe_allow_html=True)

    # ── Live progress counters (update every 1.15s inside fragment) ────────────
    if is_running or enriching or st.session_state.query_current:
        q_idx = st.session_state.query_progress_idx
        q_total = max(st.session_state.query_total, 1)
        leads_count = st.session_state.emails_collected
        current_q = st.session_state.query_current or "—"

        mc1, mc2, mc3 = st.columns(3)
        with mc1:
            st.metric("🎯 Leads Collected", leads_count,
                      delta=f"+{leads_count}" if leads_count > 0 else None,
                      delta_color="normal")
        with mc2:
            st.metric("📋 Query Progress", f"{q_idx} / {q_total}")
        with mc3:
            phase = "🔬 Enriching" if enriching else ("🔍 Searching" if is_running else "✅ Done")
            st.metric("⚙️ Phase", phase)

        # Progress bar
        if q_total > 0:
            st.progress(min(1.0, q_idx / q_total), text=f"Query {q_idx}/{q_total}: {current_q[:60]}")

        # Download button while running
        if st.session_state.extracted_leads:
            dl_df = pd.DataFrame([
                {
                    "Name": l.get("contact_name", ""),
                    "Email": l.get("email", ""),
                    "Phone": l.get("phone", ""),
                    "Company": l.get("business_name", ""),
                    "Job Title": l.get("job_title", ""),
                }
                for l in st.session_state.extracted_leads
            ])
            st.download_button(
                f"⬇️ Download {leads_count} leads (in progress)",
                data=dl_df.to_csv(index=False),
                file_name="hr_leads_live.csv",
                mime="text/csv",
                use_container_width=True,
                key="dl_inprogress",
            )

    # ── Enrichment progress bar ────────────────────────────────────────────────
    if enriching:
        cur = st.session_state.get("enrichment_current", 0)
        tot = max(st.session_state.get("enrichment_total", 1), 1)
        pct = cur / tot
        st.markdown(
            f'<div style="background:#0f172a;border:1px solid #1e3a5f;border-radius:6px;'
            f'padding:0.6rem 1rem;margin-bottom:0.5rem;">'
            f'<span style="color:#38bdf8;font-weight:600;">🔬 Email Enrichment Phase</span>'
            f'<span style="color:#94a3b8;font-size:0.85rem;"> — {cur}/{tot} leads enriched</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.progress(pct)
    elif st.session_state.get("enrichment_done_count", 0) > 0 and not is_running:
        done = st.session_state.enrichment_done_count
        processed = st.session_state.get("enrichment_processed", done)
        st.success(f"🔬 Enrichment complete — {done}/{processed} leads gained a verified email address")

    if not is_running and not enriching and not st.session_state.current_screenshot:
        return
    _render_live_panel()


# ─── Helper Functions ────────────────────────────────────────────────────────
def get_all_leads_data():
    try:
        return get_all_leads()
    except Exception:
        return []


def display_stats():
    stats = get_lead_stats()
    all_leads_data = get_all_leads_data()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f"""
        <div class="stat-box">
            <div class="stat-number">{stats['total_leads']}</div>
            <div class="stat-label">Total HR Contacts</div>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        st.markdown(f"""
        <div class="stat-box">
            <div class="stat-number">{stats['total_searches']}</div>
            <div class="stat-label">Searches Performed</div>
        </div>
        """, unsafe_allow_html=True)
    with col3:
        unique_emails = len(set([l.get('email') for l in all_leads_data if l.get('email')]))
        st.markdown(f"""
        <div class="stat-box">
            <div class="stat-number">{unique_emails}</div>
            <div class="stat-label">Unique Emails</div>
        </div>
        """, unsafe_allow_html=True)
    with col4:
        hiring = len([l for l in all_leads_data if l.get('is_hiring_role')])
        st.markdown(f"""
        <div class="stat-box">
            <div class="stat-number">{hiring}</div>
            <div class="stat-label">Hiring Managers</div>
        </div>
        """, unsafe_allow_html=True)


# ─── Header ──────────────────────────────────────────────────────────────────
is_running = st.session_state.is_running
conn_label = "🟢 Connected" if st.session_state.websocket_connected else ("🔄 Running" if is_running else "🟡 Ready")

st.markdown(f"""
<div class="header-container">
    <h1 class="header-title">👔 {APP_NAME}</h1>
    <p class="header-subtitle">{APP_SUBTITLE}</p>
    <div class="header-meta">
        <div class="metric-item">v{APP_VERSION}</div>
        <div class="metric-item">{conn_label}</div>
        <div class="metric-item">🎯 {st.session_state.emails_collected} leads collected</div>
    </div>
</div>
""", unsafe_allow_html=True)


# ─── Main Tabs ────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs([
    "🔍 Search & Extract",
    "📊 Leads Database",
    "📈 Campaigns",
    "⚙️ Settings",
])


# ─────────────────────────────────────────────────────────────────────────────
# TAB 1: SEARCH & EXTRACT
# ─────────────────────────────────────────────────────────────────────────────
with tab1:

    # ── Quick Run Template (paste & run) ─────────────────────────────────────
    with st.expander("⚡ Quick Run — Paste Queries Directly", expanded=False):
        st.markdown(
            "Paste any list of search queries here (one per line) and click **▶️ Quick Start**. "
            "No need to use the 3-column builder below. "
            "Edit these anytime — they're saved per session.",
            unsafe_allow_html=False,
        )
        quick_queries_text = st.text_area(
            "Quick Queries (one per line)",
            height=160,
            key="quick_queries_text",
            placeholder=(
                '"HR Manager" email contact technology company\n'
                '"Talent Acquisition Manager" staff directory filetype:pdf\n'
                '"HR Director" team email healthcare\n'
                '"Head of HR" contact us email fintech\n'
                '"Recruiting Manager" email filetype:pdf consulting firm\n'
                '"Chief People Officer" leadership team email\n'
                '"VP of People" contact email SaaS company\n'
                '"Staffing Manager" agency directory email'
            ),
        )
        qr_c1, qr_c2, qr_c3 = st.columns([2, 1, 1])
        quick_queries = [q.strip() for q in (quick_queries_text or "").split("\n") if q.strip()]
        with qr_c1:
            if quick_queries:
                st.caption(f"✓ {len(quick_queries)} queries ready to run")
            else:
                st.caption("Add queries above to enable Quick Start")
        with qr_c2:
            qr_mode = st.radio(
                "Mode", ["Stealth (DDG)", "Google"],
                key="qr_mode", horizontal=True,
                help="Stealth = DuckDuckGo (no CAPTCHA). Google = may hit CAPTCHAs."
            )
        with qr_c3:
            qr_headless = st.checkbox("Headless", value=True, key="qr_headless",
                                       help="Run browser in background (no visible window)")
        quick_start_btn = st.button(
            f"⚡ Quick Start ({len(quick_queries)} queries)" if quick_queries else "⚡ Quick Start",
            disabled=st.session_state.is_running or not bool(quick_queries),
            type="primary",
            use_container_width=True,
            key="quick_start_btn",
        )

    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    # ── Template selector ────────────────────────────────────────────────────
    t_col1, t_col2 = st.columns([2, 1])
    with t_col1:
        template_options = ["— Select a template —"] + list(HR_SEARCH_TEMPLATES.keys())
        chosen_template = st.selectbox(
            "Load Search Template",
            template_options,
            key="_hr_template_sel",
            help="Pre-fills all three columns with proven HR search patterns.",
        )
    with t_col2:
        search_mode = st.radio(
            "Search Mode",
            ["Stealth Mode (Recommended)", "LinkedIn Direct", "Google Direct"],
            help=(
                "Stealth Mode: DDG stealth search for PDFs, company pages, directories. "
                "LinkedIn Direct: log in to LinkedIn and search people. "
                "Google Direct: Google SERP (may hit CAPTCHAs)."
            ),
            horizontal=True,
        )
    if "Stealth" in search_mode:
        search_engine = "duckduckgo"
    elif "LinkedIn" in search_mode:
        search_engine = "linkedin_direct"
    else:
        search_engine = "google"

    # When template changes, update all three query builder columns
    if (
        chosen_template != "— Select a template —"
        and chosen_template != st.session_state._prev_template_sel
    ):
        tmpl = HR_SEARCH_TEMPLATES[chosen_template]
        st.session_state.qb_titles = tmpl["titles"]
        st.session_state.qb_patterns = tmpl["patterns"]
        st.session_state.qb_industries = tmpl["industries"]
        st.session_state._prev_template_sel = chosen_template
        st.rerun()

    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    # ── 3-Column Query Builder ────────────────────────────────────────────────
    st.markdown(
        '<div class="card-title">🔍 Query Builder</div>'
        '<p style="font-size:0.85rem;color:#64748B;margin-bottom:1rem;">'
        "Every combination of <strong>Job Title × Search Pattern × Industry/Location</strong> "
        "becomes a separate search run. Leave Industries empty to skip that dimension.</p>",
        unsafe_allow_html=True,
    )

    col_t, col_p, col_i = st.columns(3)

    with col_t:
        st.markdown("**👤 HR Job Titles**")
        titles_text = st.text_area(
            "titles", height=190, label_visibility="collapsed",
            key="qb_titles",
            placeholder=(
                "HR Manager\n"
                "Talent Acquisition Manager\n"
                "HR Director\n"
                "Recruiting Manager\n"
                "Chief People Officer"
            ),
        )

    with col_p:
        st.markdown("**🔗 Search Pattern**")
        patterns_text = st.text_area(
            "patterns", height=190, label_visibility="collapsed",
            key="qb_patterns",
            placeholder=(
                'email contact\n'
                'team email\n'
                'staff directory filetype:pdf\n'
                'contact list filetype:pdf\n'
                'our team email "@"'
            ),
        )

    with col_i:
        st.markdown("**🏢 Industry / Location** *(optional)*")
        industries_text = st.text_area(
            "industries", height=190, label_visibility="collapsed",
            key="qb_industries",
            placeholder=(
                "tech startup\n"
                "Fortune 500\n"
                "San Francisco\n"
                "New York\n"
                "financial services"
            ),
        )

    titles    = [t.strip() for t in titles_text.split("\n") if t.strip()]
    patterns  = [p.strip() for p in patterns_text.split("\n") if p.strip()]
    industries = [i.strip() for i in industries_text.split("\n") if i.strip()]

    # Cross-product count display
    total_queries = len(titles) * len(patterns) * max(len(industries), 1)
    if titles and patterns:
        industry_part = f" × {len(industries)} industries" if industries else " (no industry filter)"
        st.markdown(
            f'<div class="query-count-badge">'
            f'✓ {len(titles)} titles × {len(patterns)} patterns'
            f'{industry_part} = <strong>{total_queries} queries</strong>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        st.caption("Fill in Job Titles and Search Pattern to generate queries.")

    queries = generate_hr_queries(titles, patterns, industries)

    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    # ── Options row ───────────────────────────────────────────────────────────
    opt_c1, opt_c2, opt_c3 = st.columns(3)
    with opt_c1:
        max_pages = st.slider(
            "Max Pages per Query", 1, 10, 3,
            help="Search result pages to process per query",
        )
    with opt_c2:
        delay_pages = st.slider(
            "Delay Between Pages (sec)", 1, 30, 5,
            help="Higher = safer from rate limits",
        )
    with opt_c3:
        target_leads = st.number_input(
            "Stop after N leads (0 = unlimited)",
            min_value=0, max_value=100000, value=0, step=100,
        )
        headless_mode = st.checkbox(
            "Headless mode (no browser window)", value=False,
        )

    # ── Action buttons ────────────────────────────────────────────────────────
    col_btn1, col_btn2, col_btn3, col_btn4 = st.columns([1.5, 1, 1, 1])

    with col_btn1:
        start_btn = st.button(
            "▶️ Start Search",
            use_container_width=True,
            key="start_search_btn",
            disabled=st.session_state.is_running or not bool(queries),
            type="primary",
        )
    with col_btn2:
        stop_btn = st.button(
            "⏹️ Stop",
            use_container_width=True,
            key="stop_search_btn",
            disabled=not st.session_state.is_running,
        )
    with col_btn3:
        clear_btn = st.button("🗑️ Clear", use_container_width=True)
    with col_btn4:
        check_srv = st.button("🔌 Check Server", use_container_width=True)

    # ── Button handlers ───────────────────────────────────────────────────────
    def _launch_run(run_queries: list, engine: str, headless: bool):
        """Shared helper: start a WebSocket automation thread."""
        st.session_state.is_running = True
        st.session_state.extracted_leads = []
        st.session_state.current_leads = []
        st.session_state.activity_log = []
        st.session_state.current_screenshot = None
        st.session_state.query_current = ""
        st.session_state.query_progress_idx = 0
        st.session_state.query_total = len(run_queries)
        st.session_state.emails_collected = 0
        st.session_state.enrichment_active = False
        st.session_state.enrichment_done_count = 0
        msg_q: Queue = Queue()
        st.session_state.ws_message_queue = msg_q
        _settings = load_settings()
        _headless = headless or bool(_settings.get("headless_mode", False))
        _auto_enrich = st.session_state.get("auto_enrich_setting", True)
        t = threading.Thread(
            target=run_websocket_client,
            args=(run_queries, int(max_pages), float(delay_pages), 1.0, msg_q,
                  int(target_leads), engine, _headless),
            kwargs={"auto_enrich": _auto_enrich},
            daemon=True,
        )
        t.start()
        st.rerun()

    if quick_start_btn and quick_queries:
        _qr_engine = "google" if "Google" in st.session_state.get("qr_mode", "") else "duckduckgo"
        _launch_run(quick_queries, _qr_engine, bool(st.session_state.get("qr_headless", True)))

    if start_btn and queries:
        _launch_run(queries, search_engine, headless_mode)

    if stop_btn:
        st.session_state.is_running = False
        ws = _ws_client_ref[0] if _ws_client_ref else None
        if ws:
            try:
                ws.send(json.dumps({"command": "stop"}))
                st.session_state.activity_log.append("⏹ Stop sent to automation server.")
            except Exception as e:
                st.session_state.activity_log.append(f"⚠️ Stop requested ({str(e)[:50]}).")
        else:
            st.session_state.activity_log.append("⏹ Stopped (no active connection).")
        st.rerun()

    if clear_btn:
        st.session_state.activity_log = []
        st.session_state.extracted_leads = []
        st.session_state.current_screenshot = None
        st.session_state.query_current = ""
        st.session_state.query_progress_idx = 0
        st.session_state.emails_collected = 0
        st.rerun()

    if check_srv:
        try:
            import httpx
            with st.spinner("Checking automation server..."):
                resp = httpx.get(f"{AUTOMATION_SERVER_URL.rstrip('/')}/", timeout=3)
            if resp.status_code == 200:
                st.session_state.websocket_connected = True
                st.success("✅ Automation server is online and ready.")
            else:
                st.error(f"Server returned status {resp.status_code}.")
        except Exception as e:
            st.session_state.websocket_connected = False
            st.error(
                f"⚠️ Cannot reach automation server at `{AUTOMATION_SERVER_URL}`. "
                f"Start it with `python -m uvicorn app.server.automation_server:app --port 8001`. "
                f"Error: {str(e)[:80]}"
            )

    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    # ── Live automation fragment (polls every ~1.15s, owns counters + status) ──
    # Progress board, status bar, live browser + activity log all live here.
    _live_automation_fragment()

    # ── Quick stats when idle ─────────────────────────────────────────────────
    if not st.session_state.is_running:
        st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
        st.markdown('<div class="card-title">📊 Database Summary</div>', unsafe_allow_html=True)
        display_stats()


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2: LEADS DATABASE
# ─────────────────────────────────────────────────────────────────────────────
with tab2:
    st.markdown('<div class="card-container">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">📋 Extracted HR Leads</div>', unsafe_allow_html=True)

    all_leads = get_all_leads_data()

    if all_leads:
        df_display = pd.DataFrame([
            {
                "Name": lead.get("contact_name", ""),
                "Email": lead.get("email", ""),
                "Phone": lead.get("phone", ""),
                "Company": lead.get("business_name", ""),
                "Job Title": lead.get("job_title", ""),
                "Seniority": lead.get("seniority_level", ""),
                "Dept": lead.get("department", ""),
                "Hiring": "✓" if lead.get("is_hiring_role") else "✗",
                "Quality": lead.get("contact_quality_score", 0),
            }
            for lead in all_leads
        ])

        col_f1, col_f2, col_f3 = st.columns(3)
        with col_f1:
            filter_hiring = st.checkbox("Show only hiring managers", value=False)
        with col_f2:
            filter_company = st.text_input("Filter by company")
        with col_f3:
            dept_opts = ["All"] + sorted(set(df_display["Dept"].dropna().tolist()))
            filter_dept = st.selectbox("Filter by department", dept_opts)

        if filter_hiring:
            df_display = df_display[df_display["Hiring"] == "✓"]
        if filter_company:
            df_display = df_display[df_display["Company"].str.contains(filter_company, case=False, na=False)]
        if filter_dept != "All":
            df_display = df_display[df_display["Dept"] == filter_dept]

        st.dataframe(df_display, use_container_width=True, hide_index=True)

        st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
        col_exp1, col_exp2, col_exp3 = st.columns(3)

        with col_exp1:
            if st.button("📥 Export to CSV", use_container_width=True):
                csv = export_to_csv(all_leads)
                st.download_button(
                    label="Download CSV",
                    data=csv,
                    file_name=f"hr_leads_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
        with col_exp2:
            if st.button("📊 Export to Excel", use_container_width=True):
                excel = export_to_excel(all_leads)
                st.download_button(
                    label="Download Excel",
                    data=excel,
                    file_name=f"hr_leads_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
        with col_exp3:
            if st.button("📧 Copy All Emails", use_container_width=True):
                emails = [lead.get("email", "") for lead in all_leads if lead.get("email")]
                st.info(f"**{len(emails)} emails:**\n\n" + "\n".join(emails))
    else:
        st.info("📭 No leads extracted yet. Use the **Search & Extract** tab to find HR contacts.")

    # ── Re-enrich button ──────────────────────────────────────────────────────
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.markdown('<div class="card-title">🔬 Email Enrichment</div>', unsafe_allow_html=True)
    from app.database.db import get_leads_without_email as _get_no_email
    _leads_no_email = _get_no_email()
    st.caption(
        f"**{len(_leads_no_email)}** lead(s) in the database have no email address yet. "
        "Click below to run the email enrichment pipeline on all of them."
    )
    _enrich_col1, _enrich_col2 = st.columns([1, 3])
    with _enrich_col1:
        if st.button("🔬 Enrich Now", use_container_width=True, disabled=st.session_state.is_running or st.session_state.get("enrichment_active", False)):
            try:
                import requests as _req
                resp = _req.post(f"{AUTOMATION_SERVER_URL}/enrich", timeout=5)
                result = resp.json()
                if result.get("status") == "enrichment_started":
                    st.session_state.enrichment_active = True
                    st.success(f"✅ Enrichment started for {result.get('count', 0)} lead(s). Watch the activity log in Search & Extract tab.")
                elif result.get("status") == "busy":
                    st.warning(result.get("message", "Server is busy."))
                else:
                    st.info(result.get("message", "No leads to enrich."))
            except Exception as e:
                st.error(f"Could not reach automation server: {e}")
    with _enrich_col2:
        if st.session_state.get("enrichment_done_count", 0) > 0:
            done = st.session_state.enrichment_done_count
            processed = st.session_state.get("enrichment_processed", done)
            st.info(f"Last enrichment: **{done}/{processed}** leads gained a verified email.")

    st.markdown("</div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3: CAMPAIGNS
# ─────────────────────────────────────────────────────────────────────────────
with tab3:
    st.markdown('<div class="card-container">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">📈 Campaign Management</div>', unsafe_allow_html=True)

    col_camp1, col_camp2 = st.columns(2)
    with col_camp1:
        st.markdown("#### Create New Campaign")
        campaign_name = st.text_input("Campaign Name", placeholder="e.g., Q2 Talent Acquisition")
        target_position = st.text_input("Target Position", placeholder="e.g., Tech Lead, Senior Engineer")
        target_company = st.text_input("Target Company", placeholder="Leave blank for any")
    with col_camp2:
        st.markdown("#### Campaign Settings")
        target_location = st.text_input("Target Location", placeholder="e.g., San Francisco, USA")
        campaign_notes = st.text_area("Campaign Notes", placeholder="Add notes...", height=80)
        if st.button("✅ Create Campaign", use_container_width=True):
            if campaign_name:
                st.success(f"✓ Campaign '{campaign_name}' created successfully!")
            else:
                st.warning("Enter a campaign name first.")

    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.markdown('<div class="card-container">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">📊 Active Campaigns</div>', unsafe_allow_html=True)
    st.info("💡 Create campaigns to organize and track your HR lead outreach efforts.")
    st.markdown("</div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 4: SETTINGS
# ─────────────────────────────────────────────────────────────────────────────
with tab4:
    st.markdown('<div class="card-container">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">⚙️ Application Settings</div>', unsafe_allow_html=True)

    col_set1, col_set2 = st.columns(2)
    with col_set1:
        st.markdown("#### Data Retention")
        retention_days = st.slider("Keep data for (days)", 7, 365, 30)
        st.caption("Leads older than this will be automatically removed.")

        st.markdown("#### Search Defaults")
        auto_delay = st.checkbox("Auto-adjust delays (prevent blocks)", value=True)
        headless_default = st.checkbox("Headless mode by default (no browser window)", value=False)
        auto_enrich_setting = st.checkbox(
            "🔬 Auto-enrich emails after each run",
            value=True,
            key="auto_enrich_setting",
            help="After extraction, automatically find verified emails for leads that have none (SMTP verification, company website crawl).",
        )

    with col_set2:
        st.markdown("#### Export Settings")
        include_scores = st.checkbox("Include quality scores in exports", value=True)
        include_metadata = st.checkbox("Include metadata (source, timestamp)", value=False)

        st.markdown("#### Server")
        st.caption(f"Automation server: `{AUTOMATION_SERVER_URL}`")
        st.caption(f"WebSocket: `{WEBSOCKET_URL}`")

    if st.button("💾 Save Settings", use_container_width=True):
        save_settings({
            "retention_days": retention_days,
            "auto_delay": auto_delay,
            "headless_mode": headless_default,
            "include_scores": include_scores,
            "include_metadata": include_metadata,
        })
        st.success("✓ Settings saved successfully!")

    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    # ── LinkedIn Credentials ──────────────────────────────────────────────────
    st.markdown('<div class="card-container">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">🔗 LinkedIn Direct — Account Credentials</div>', unsafe_allow_html=True)
    st.caption(
        "Used only by **LinkedIn Direct** search mode. "
        "Credentials are stored locally on your machine and never sent anywhere externally. "
        "A session cookie is saved after first login so re-login is rare."
    )

    _li_creds_path = Path(__file__).parent.parent / "data" / "linkedin_creds.json"
    _li_session_path = Path(__file__).parent.parent / "data" / "linkedin_session.json"

    _existing_email = ""
    if _li_creds_path.exists():
        try:
            _c = json.loads(_li_creds_path.read_text())
            _existing_email = _c.get("email", "")
        except Exception:
            pass

    li_col1, li_col2 = st.columns(2)
    with li_col1:
        li_email = st.text_input(
            "LinkedIn Email",
            value=_existing_email,
            placeholder="you@email.com",
            key="li_email_input",
        )
    with li_col2:
        li_password = st.text_input(
            "LinkedIn Password",
            value="",
            type="password",
            placeholder="Your LinkedIn password",
            key="li_password_input",
        )

    li_b1, li_b2, li_b3 = st.columns(3)
    with li_b1:
        if st.button("💾 Save LinkedIn Credentials", use_container_width=True):
            if li_email and li_password:
                _li_creds_path.parent.mkdir(exist_ok=True)
                _li_creds_path.write_text(json.dumps({"email": li_email, "password": li_password}))
                st.success("✓ LinkedIn credentials saved.")
            else:
                st.error("Enter both email and password.")
    with li_b2:
        if st.button("🗑️ Clear Saved Session", use_container_width=True, help="Force re-login on next LinkedIn run"):
            if _li_session_path.exists():
                _li_session_path.unlink()
                st.success("✓ Session cleared — will re-login on next run.")
            else:
                st.info("No saved session found.")
    with li_b3:
        _session_status = "✅ Session saved" if _li_session_path.exists() else "⚪ No saved session"
        _creds_status = f"🔑 Credentials: {_existing_email}" if _existing_email else "⚪ No credentials saved"
        st.markdown(f"**Status**  \n{_creds_status}  \n{_session_status}")

    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    st.markdown('<div class="card-container">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">ℹ️ About</div>', unsafe_allow_html=True)

    st.markdown(f"""
**{APP_NAME}** v{APP_VERSION}

- 🎯 Find HR decision makers and hiring managers
- 🔍 Multi-query batch extraction — run dozens of searches in sequence
- 📊 Track and organize leads by campaign
- 💾 Export to CSV or Excel

**Supported Search Patterns:**
- `site:linkedin.com/in "Job Title" email` — LinkedIn profile search
- `"Job Title" "@domain.com" filetype:pdf` — PDF directory search
- `intext:"email" intext:"HR Manager" site:company.com` — Company website search
- Google operators: `site:`, `intext:`, `filetype:`, `intitle:`, `OR`, `"exact phrase"`
""")

    st.markdown("</div>", unsafe_allow_html=True)


# ─── Footer ──────────────────────────────────────────────────────────────────
st.markdown("""
<div style="text-align: center; color: #94A3B8; font-size: 0.8rem; padding: 2rem 0 1rem;">
    HR Recruitment Extractor &nbsp;·&nbsp; Professional Edition &nbsp;·&nbsp;
    Search responsibly — respect terms of service.
</div>
""", unsafe_allow_html=True)
