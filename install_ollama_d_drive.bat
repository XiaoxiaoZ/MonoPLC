@echo off
REM =======================================================================
REM Ollama Custom Installation Script (Install to D: Drive + Models on D: Drive)
REM =======================================================================
echo Checking for Administrator privileges...
net session >nul 2>&1
if %errorLevel% NEQ 0 (
    echo [ERROR] This script requires Administrator privileges.
    echo Please right-click and "Run as Administrator".
    pause
    exit /b 1
)

cd /d "%~dp0"

echo.
echo [1/3] Setting User Environment Variable OLLAMA_MODELS...
setx OLLAMA_MODELS "D:\Ollama\Models"
REM Also set it for the current session so the installer picks it up
set OLLAMA_MODELS=D:\Ollama\Models

echo.
echo [2/3] Creating Directories on D: drive...
mkdir D:\Ollama 2>nul
mkdir D:\Ollama\Models 2>nul

echo.
echo [3/3] Running Ollama Setup...
echo The installer will now launch. 
echo IMPORTANT: During installation, click "Options" or change the install path to "D:\Ollama" if prompted.
echo If it installs silently to C:, don't worry, the models will still be stored on D:\Ollama\Models.
start /wait OllamaSetup.exe

echo.
echo =======================================================================
echo Installation Complete!
echo - Executable location: D:\Ollama (if changed during setup) or LocalAppData
echo - Models location: D:\Ollama\Models (Configured via OLLAMA_MODELS env var)
echo =======================================================================
echo.
echo NOTE: You may need to restart your terminal or IDE for the environment 
echo variable changes to take effect before running "ollama run llama3".
pause
