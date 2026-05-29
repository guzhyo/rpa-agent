@echo off
chcp 65001 >nul
echo ==========================================
echo RPA Build Script
echo ==========================================
echo.

echo [1/5] Clean old build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
echo [OK] Done
echo.

echo [2/5] Install dependencies...
pip install --upgrade pip
pip install pyinstaller
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Dependency install failed
    pause
    exit /b 1
)
echo [OK] Done
echo.

echo [3/5] Start building...
.venv\Scripts\pyinstaller.exe rpa_tool.spec --clean
if errorlevel 1 (
    echo [ERROR] Build failed
    pause
    exit /b 1
)
echo [OK] Done
echo.

echo [4/5] Check output...
if exist "dist\RPA.exe" (
    echo [OK] EXE generated successfully!
    echo.
    echo Output: dist\RPA.exe
    echo.
    echo ==========================================
    echo Build Complete!
    echo ==========================================
) else (
    echo [WARNING] Check dist folder manually
    dir dist
)

pause
