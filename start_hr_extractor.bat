@echo off
REM HR Recruitment Extractor - Windows Quick Start Script

echo.
echo ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
echo  HR Recruitment Extractor - Starting...
echo ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
echo.

REM Check if Python 3 is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo X Python 3 is not installed. Please install Python 3.9+ first.
    echo   Download from: https://www.python.org/downloads/
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version') do set PYTHON_VERSION=%%i
echo 3 Python found: %PYTHON_VERSION%

REM Create virtual environment if it doesn't exist
if not exist ".venv" (
    echo.
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo X Failed to create virtual environment
        pause
        exit /b 1
    )
)

REM Activate virtual environment
call .venv\Scripts\activate.bat
echo 3 Virtual environment activated

REM Install/upgrade requirements
echo.
echo 7 Installing requirements (this may take a few minutes)...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo X Failed to install requirements
    echo   Check your internet connection and try again
    pause
    exit /b 1
)
echo 3 Requirements installed successfully

REM Install Playwright browsers
echo.
echo 6 Setting up browser automation...
python -m playwright install chromium
if errorlevel 1 (
    echo X Failed to install Playwright
    echo   Try running as Administrator
    pause
    exit /b 1
)
echo 3 Browser automation ready

REM Start the app
echo.
echo ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
echo  Starting HR Recruitment Extractor...
echo ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
echo.
echo 7 App will open at: http://localhost:8502
echo   (automatically in your default browser)
echo.
echo Press Ctrl+C in this window to stop the server
echo.

REM Run streamlit
streamlit run app/main.py --server.port 8502 --logger.level=info

REM Deactivate venv
call .venv\Scripts\deactivate.bat
