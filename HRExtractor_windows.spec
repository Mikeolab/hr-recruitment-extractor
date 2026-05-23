# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for HR Recruitment Extractor — Windows build.

Entry point : launch_hr_windows.py
Output      : dist/HRExtractor.exe  (single-file, console window)

Build command (from repo root):
  python -m PyInstaller --clean --noconfirm HRExtractor_windows.spec
"""
import os
from PyInstaller.utils.hooks import collect_all

# ── Source data ───────────────────────────────────────────────────────────────
datas = [
    ("app", "app"),  # entire app package
]

binaries = []

# Bundle Playwright's Chromium browser (pre-downloaded by build step)
# PLAYWRIGHT_BROWSERS_PATH must point here so the EXE finds it at runtime.
if os.path.exists("playwright_browsers"):
    for root, _dirs, files in os.walk("playwright_browsers"):
        for fname in files:
            src  = os.path.normpath(os.path.join(root, fname))
            rel  = os.path.relpath(src, "playwright_browsers")
            dest = os.path.join("playwright_browsers", rel)
            datas.append((src, dest))

# ── Hidden imports ────────────────────────────────────────────────────────────
hiddenimports = [
    # UI / API
    "streamlit",
    "streamlit.web.cli",
    "streamlit.runtime.scriptrunner",
    "streamlit.runtime.scriptrunner.magic_funcs",
    "fastapi",
    "uvicorn",
    "uvicorn.lifespan.on",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.loops.asyncio",
    # WebSocket
    "websockets",
    "websocket",
    "websocket_client",
    # Browser automation
    "playwright",
    "playwright.sync_api",
    "playwright.async_api",
    # Data / export
    "pandas",
    "openpyxl",
    "fpdf",
    "fpdf2",
    "PIL",
    "PIL.Image",
    # PDF extraction
    "pdfplumber",
    "pdfminer",
    "pdfminer.high_level",
    # Scraping
    "httpx",
    "httpx._transports.default",
    "bs4",
    "lxml",
    "lxml.etree",
    "lxml._elementpath",
    # Email enrichment
    "dns",
    "dns.resolver",
    "dns.rdatatype",
    "dns.rdataclass",
    "dns.name",
    "dns.message",
    "dns.query",
    "email_validator",
    "smtplib",
    # Security
    "cryptography",
    "cryptography.fernet",
    "cryptography.hazmat.primitives.ciphers",
    "cryptography.hazmat.backends",
    "cryptography.hazmat.backends.openssl",
    "cryptography.hazmat.bindings._rust",
    "cryptography.hazmat.primitives.hashes",
    "cryptography.hazmat.primitives.kdf.pbkdf2",
    "cffi",
    "_cffi_backend",
    "keyring",
    # DB
    "sqlite3",
    # Stdlib email (keep before app.email resolution)
    "email",
    "email.mime",
    "email.mime.text",
    "email.mime.multipart",
    "email.mime.base",
    "email.utils",
    "email.encoders",
    # Misc
    "requests",
    "dotenv",
    "python_dotenv",
    "boto3",
    "botocore",
]


def _toc_to_src_dest(toc_list):
    """Convert PyInstaller TOC 3-tuples (dest, src, type) → (src, dest) 2-tuples."""
    out = []
    for item in toc_list:
        if len(item) == 3:
            dest, src, _ = item
            out.append((src, dest))
        else:
            out.append(item)
    return out


# Collect heavy packages so all their internal assets are included
for pkg in ("streamlit", "playwright", "fastapi", "uvicorn", "cryptography"):
    try:
        ret = collect_all(pkg)
        datas      += _toc_to_src_dest(ret[0])
        binaries   += _toc_to_src_dest(ret[1])
        hiddenimports += ret[2]
    except Exception as e:
        print(f"[spec] WARNING: collect_all('{pkg}') failed: {e}")

# bs4 / lxml need their data files too
for pkg in ("bs4", "lxml"):
    try:
        ret = collect_all(pkg)
        datas    += _toc_to_src_dest(ret[0])
        binaries += _toc_to_src_dest(ret[1])
        hiddenimports += ret[2]
    except Exception as e:
        print(f"[spec] WARNING: collect_all('{pkg}') failed: {e}")

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    ["launch_hr_windows.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["hook-email-stdlib.py"],   # keeps stdlib email before app.email
    excludes=[
        "tkinter",
        "matplotlib",
        "scipy",
        "numpy.testing",
        "unittest",
        "xml.etree.ElementTree",  # prefer lxml
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="HRExtractor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX can corrupt .pyd files on Windows — keep off
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,       # Show console so users/support can see errors
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,          # Swap in an .ico path here when you have one
)
