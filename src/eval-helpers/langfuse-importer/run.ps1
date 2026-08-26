@echo off
setlocal
cd /d %~dp0

if not exist .venv (
  python -m venv .venv
)

call .venv\Scripts\activate.bat
pip install -r requirements.txt

if not exist ui\dist\index.html (
  pushd ui
  call npm install
  call npm run build
  popd
)

python -m importer
