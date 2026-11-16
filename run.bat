@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist .venv (
  echo [설치] 가상환경을 만들고 패키지를 설치합니다...
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
if not exist .env copy .env.example .env >nul
echo 대시보드: http://localhost:8000
start "" http://localhost:8000
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
