@echo off
rem Nizamiye Kiosk baslatici — Edge'i tam-ekran kiosk modunda acar.
rem Cikis: klavye baglayip Alt+F4 (ya da Ctrl+Alt+Del ile oturum).
cd /d "%~dp0"
start "" msedge --kiosk "%~dp0kiosk\index.html" --edge-kiosk-type=fullscreen --autoplay-policy=no-user-gesture-required --no-first-run --disable-pinch --overscroll-history-navigation=0
rem Edge yoksa Chrome ile (ustteki satiri silip bunu acin):
rem start "" chrome --kiosk "%~dp0kiosk\index.html" --autoplay-policy=no-user-gesture-required --no-first-run --disable-pinch
