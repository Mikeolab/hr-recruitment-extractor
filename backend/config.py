"""Configuration settings for Lead Extractor."""
import os
from pathlib import Path

# Base paths
BASE_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "leads.db"

# License
LICENSE_MASTER_KEY = os.getenv("LICENSE_MASTER_KEY", "")

# Search settings
MAX_RESULTS_PER_SEARCH = int(os.getenv("MAX_RESULTS_PER_SEARCH", "20"))
MAX_CONCURRENT_SCRAPES = int(os.getenv("MAX_CONCURRENT_SCRAPES", "10"))

# SerpAPI (optional premium)
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")

# Server
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8001"))

# Rate limits per tier (searches per day)
TIER_LIMITS = {
    "free": 10,
    "pro": 100,
    "enterprise": 9999,
}

# User agent for scraping
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

