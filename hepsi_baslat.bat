@echo off
rem NIZAMIYE HEPSI-BIR-ARADA baslatici:
rem  1) Guncel yerel-ag IP'siyle QR'i yeniden uretir (telefonlar bu adrese gelir)
rem  2) Nizamiye web sitesini yerel agda yayinlar (port 4321)
rem  3) Kiosk'u tam ekran acar
rem NOT: Ziyaretci telefonu ile bu PC AYNI Wi-Fi'da olmali.
rem Ilk calistirmada Windows guvenlik duvari sorarsa "Ozel aglar" icin IZIN VER.
cd /d "%~dp0"

echo [1/3] QR guncelleniyor...
.venv\Scripts\python.exe scripts\kiosk_qr.py
if errorlevel 1 pause

echo [2/3] Web sitesi yayinlaniyor (http://0.0.0.0:4321)...
start "NizamiyeWeb" /min .venv\Scripts\python.exe -m http.server 4321 --bind 0.0.0.0 --directory "C:\Users\tosun\Desktop\nizamiye\dist"

echo [3/3] Kiosk aciliyor...
start "" msedge --kiosk "%~dp0kiosk\index.html" --edge-kiosk-type=fullscreen --autoplay-policy=no-user-gesture-required --no-first-run --disable-pinch --overscroll-history-navigation=0
