@echo off
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\python -m pip install -r requirements.txt
)
rem exe 하나로 묶는 --onefile은 윈도우 디펜더가 Wacatac.B!ml로 오탐한다. 폴더 형태(--onedir)로 만든다.
.venv\Scripts\pyinstaller --noconfirm --onedir --noconsole --name "MapleSkillMirror" --collect-all dxcam --collect-all comtypes app.py
if exist MapleSkillMirror (
  copy /y MapleSkillMirror\config.json dist\MapleSkillMirror\ >nul 2>&1
  copy /y MapleSkillMirror\nametag.png dist\MapleSkillMirror\ >nul 2>&1
  rmdir /s /q MapleSkillMirror
)
move dist\MapleSkillMirror MapleSkillMirror >nul
rmdir /s /q build dist
del MapleSkillMirror.spec
echo 완료: MapleSkillMirror\MapleSkillMirror.exe
pause
