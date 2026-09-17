@echo off
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\python -m pip install -r requirements.txt
)
.venv\Scripts\pyinstaller --noconfirm --onefile --noconsole --name "메이플스킬바미러" app.py
copy /y "dist\메이플스킬바미러.exe" .
rmdir /s /q build dist
del "메이플스킬바미러.spec"
echo 완료: 메이플스킬바미러.exe
pause
