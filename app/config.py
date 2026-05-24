"""
Lead Extractor - Configuration
Loads settings from .env file and provides app-wide constants.
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root (handle permission errors gracefully)
# Handle PyInstaller bundled app
if getattr(sys, 'frozen', False):
    # Running as bundled executable
    PROJECT_ROOT = Path(sys._MEIPASS)  # PyInstaller temp folder
    # For data files, use platform-specific user data directory
    if sys.platform == 'win32':
        # Windows: Use AppData\Roaming
        USER_DATA_DIR = Path(os.environ.get('APPDATA', Path.home())) / "HRExtractor"
    elif sys.platform == 'darwin':
        # macOS: Use Library/Application Support
        USER_DATA_DIR = Path.home() / "Library" / "Application Support" / "HRExtractor"
    else:
        # Linux/Other: Use .local/share
        USER_DATA_DIR = Path.home() / ".local" / "share" / "HRExtractor"
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
else:
    # Running as script
    PROJECT_ROOT = Path(__file__).parent.parent
    USER_DATA_DIR = PROJECT_ROOT

try:
    load_dotenv(PROJECT_ROOT / ".env")
except (PermissionError, OSError) as e:
    # If we can't read .env file (sandbox/permission restrictions), continue with defaults
    # Environment variables can still be set via system environment
    pass

# Google Custom Search API
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "")

# License
LICENSE_KEY = os.getenv("LICENSE_KEY", "")

# App settings
APP_NAME = "HR Recruitment Extractor"
APP_VERSION = "1.0.0"
APP_SUBTITLE = "Find HR Decision Makers & Hiring Managers"
MAX_RESULTS_PER_SEARCH = 100  # Google CSE max per query cycle
DEFAULT_RESULTS = 10
# Use user data directory for bundled app, project root for development
if getattr(sys, 'frozen', False):
    # Bundled app - use user's Application Support
    DATABASE_PATH = USER_DATA_DIR / "hr_leads.db"
    EXPORT_DIR = USER_DATA_DIR / "exports"
else:
    # Development - use project directory
    DATABASE_PATH = PROJECT_ROOT / "data" / "hr_leads.db"
    EXPORT_DIR = PROJECT_ROOT / "exports"

# Scraper settings
REQUEST_TIMEOUT = 15  # seconds
MAX_CONCURRENT_SCRAPES = 5
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# License secret (used for HMAC signing - keep this safe)
LICENSE_SECRET = "lead-extractor-pro-2026-secret-key"

# WebSocket / API URL for automation server (for cloud deployment)
# Set AUTOMATION_SERVER_URL env (e.g. https://your-api.onrender.com) or leave default for localhost
AUTOMATION_SERVER_URL = os.environ.get("AUTOMATION_SERVER_URL", "http://localhost:8001")
WEBSOCKET_URL = AUTOMATION_SERVER_URL.replace("http://", "ws://").replace("https://", "wss://").rstrip("/") + "/ws"

