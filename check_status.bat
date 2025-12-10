@echo off
REM Quick status check script for the bot

echo ====================================
echo Bot Status Check
echo ====================================
echo.

echo [1/5] Checking Python...
python --version
if errorlevel 1 (
    echo ERROR: Python not found
    pause
    exit /b 1
)
echo OK
echo.

echo [2/5] Checking NumPy version...
python -c "import numpy; print('NumPy:', numpy.__version__)"
if errorlevel 1 (
    echo ERROR: NumPy import failed
    pause
    exit /b 1
)
echo OK
echo.

echo [3/5] Checking dependencies...
python -c "import discord, motor, pymongo, PIL, imagehash; print('All core dependencies OK')"
if errorlevel 1 (
    echo ERROR: Missing dependencies
    pause
    exit /b 1
)
echo OK
echo.

echo [4/5] Checking .env configuration...
python -c "from dotenv import load_dotenv; import os; load_dotenv(); token=os.getenv('DISCORD_BOT_TOKEN'); mongo=os.getenv('MONGODB_URI'); print('Token:', 'Set' if token else 'MISSING'); print('MongoDB:', 'Set' if mongo else 'MISSING')"
echo.

echo [5/5] Testing bot imports...
python -c "import main; print('Bot code imports successfully')"
if errorlevel 1 (
    echo ERROR: Bot code has import errors
    pause
    exit /b 1
)
echo OK
echo.

echo ====================================
echo All checks passed!
echo ====================================
echo.
echo To start the bot, run: python main.py
echo.
pause
