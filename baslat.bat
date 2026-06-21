@echo off
REM BilimFest festival kiosk - cift tikla baslat (venv uzerinde, policy/aktivasyon gerekmez)
cd /d "%~dp0"
".venv\Scripts\python.exe" "baslat.py" %*
echo.
echo === Kapandi. Pencereyi kapatabilirsiniz. ===
pause
