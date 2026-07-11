# Kiosk bekcisi: Edge kapanmis/cokmusse kiosk'u yeniden acar.
# Zamanlanmis gorevle 2 dakikada bir calisir (festival_pc_kur.ps1 kurar).
$edge = Get-Process msedge -ErrorAction SilentlyContinue
if (-not $edge) {
    $bat = Join-Path (Split-Path $PSScriptRoot -Parent) "kiosk_baslat.bat"
    Start-Process -FilePath $bat
    Add-Content -Path (Join-Path $env:TEMP "kiosk_watchdog.log") -Value "$(Get-Date -Format s) Edge yoktu, kiosk yeniden baslatildi"
}
