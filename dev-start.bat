@echo off
REM Local dev launcher - opens 2 windows: backend + frontend
SET ROOT=%~dp0
SET PATH=%PATH%;C:\Program Files\gs\gs10.04.0\bin;C:\Program Files\nodejs

start "PDFTools-Backend" cmd /k "cd /d %ROOT%backend && set PDFTOOLS_DEV_MODE=true&& set PDFTOOLS_STORAGE_DIR=%ROOT%backend\.devdata\sessions&& set PDFTOOLS_DB_PATH=%ROOT%backend\.devdata\meta.db&& set PDFTOOLS_GHOSTSCRIPT_BIN=gswin64c&& set PDFTOOLS_CORS_ORIGINS=http://localhost:5173&& .venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000"

timeout /t 3 /nobreak > nul

start "PDFTools-Frontend" cmd /k "cd /d %ROOT%frontend && set PATH=C:\Program Files\nodejs;%PATH%&& npm run dev"

echo.
echo Backend:  http://127.0.0.1:8000/docs
echo Frontend: http://localhost:5173
echo.
pause
