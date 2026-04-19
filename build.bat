@echo off
chcp 65001 > nul

echo ========================================
echo  Blog Rank Checker - Build
echo ========================================
echo.

echo [1/2] Running PyInstaller...
python -m PyInstaller blog_tool.spec --clean -y
if errorlevel 1 (
    echo BUILD FAILED. Check the error above.
    pause
    exit /b 1
)

echo.
echo [2/2] Copying Playwright browsers...

set SRC=%LOCALAPPDATA%\ms-playwright
set DST=dist\blogranker\browsers

if not exist "%SRC%" (
    echo ERROR: Playwright browsers not found.
    echo Please run:  playwright install chromium
    pause
    exit /b 1
)

xcopy /E /I /Y "%SRC%" "%DST%"
copy /Y "selectors.json" "dist\blogranker\selectors.json" > nul

echo.
echo ========================================
echo  Done! Distribute: dist\blogranker\
echo ========================================
pause
