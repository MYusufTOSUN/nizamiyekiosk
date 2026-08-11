@echo off
rem ============================================================
rem  Nizamiye Kiosk - cift tikla baslat
rem
rem  Birincil ekran : dokunmatik kiosk
rem  Ikinci ekran   : hologram (kabin televizyonu, tam siyah)
rem
rem  Tek ekranda denemek icin:  kiosk_baslat.bat /tek
rem  Cikis: pencerelerde Alt+F4
rem ============================================================
cd /d "%~dp0"

set EK=
if /i "%~1"=="/tek" set EK=-TekEkran

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\kiosk_baslat.ps1" %EK%

if errorlevel 1 (
  echo.
  echo === HATA - yukaridaki mesaja bakin. ===
  pause
)
