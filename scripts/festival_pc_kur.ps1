# =============================================================
# FESTIVAL PC KURULUM — 28 saat gozetimsiz kiosk sertlestirme
# YONETICI PowerShell'de calistir:
#   powershell -ExecutionPolicy Bypass -File scripts\festival_pc_kur.ps1
# Yaptiklari:
#   1) Uyku/ekran kapanmasi/hazirda beklet KAPALI
#   2) Acilista kiosk otomatik baslar (Baslangic klasoru kisayolu)
#   3) Gece 03:30 otomatik yeniden baslatma (bellek hijyeni)
#   4) Watchdog: 2 dakikada bir Edge kontrolu, cokmusse yeniden acar
#   5) Elle yapilacaklar listesini yazdirir (otomatik oturum vb.)
# =============================================================
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Write-Host "Kiosk koku: $root"

# --- 1) GUC: uyku ve ekran kapanmasi kapali ---
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /hibernate off 2>$null
Write-Host "[1/4] Guc ayarlari: uyku/ekran kapanmasi/hibernate KAPALI"

# --- 2) BASLANGIC: kiosk otomatik acilis ---
$startup = [Environment]::GetFolderPath("Startup")
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path $startup "NizamiyeKiosk.lnk"))
$lnk.TargetPath = (Join-Path $root "kiosk_baslat.bat")
$lnk.WorkingDirectory = $root
$lnk.Save()
Write-Host "[2/4] Baslangic kisayolu kuruldu: $startup\NizamiyeKiosk.lnk"

# --- 3) GECE YENIDEN BASLATMA (03:30) ---
schtasks /Create /F /TN "Kiosk\GeceRestart" /SC DAILY /ST 03:30 /RL HIGHEST `
    /TR "shutdown /r /f /t 30 /c \"Kiosk gece bakim yeniden baslatmasi\"" | Out-Null
Write-Host "[3/4] Gece 03:30 otomatik yeniden baslatma gorevi kuruldu"

# --- 4) WATCHDOG (2 dakikada bir Edge kontrolu) ---
$wd = Join-Path $PSScriptRoot "kiosk_watchdog.ps1"
schtasks /Create /F /TN "Kiosk\Watchdog" /SC MINUTE /MO 2 `
    /TR "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"$wd\"" | Out-Null
Write-Host "[4/4] Watchdog gorevi kuruldu (2 dk'da bir Edge kontrolu)"

# --- ELLE YAPILACAKLAR ---
Write-Host ""
Write-Host "=============== ELLE YAPILACAKLAR (5 dk) ==============="
Write-Host "A) OTOMATIK OTURUM ACMA: Win+R -> netplwiz -> 'Kullanicilar bu bilgisayari"
Write-Host "   kullanmak icin ad/parola girmeli' isaretini KALDIR -> kiosk kullanicisini sec."
Write-Host "B) WINDOWS UPDATE: Ayarlar -> Windows Update -> Etkin saatleri 08:00-23:00 yap"
Write-Host "   (festival gunlerinde gunduz restart atmasin)."
Write-Host "C) BILDIRIMLER: Ayarlar -> Sistem -> Bildirimler -> KAPALI + Odaklanma yardimi ACIK."
Write-Host "D) KLAVYE GUVENLIGI: Kiosk PC'sine KLAVYE/FARE TAKILI BIRAKMA (Alt+F4 ve"
Write-Host "   Ctrl+Alt+Del kiosk modda bile calisir — en saglam onlem fiziksel olarak"
Write-Host "   klavyeyi kaldirmaktir). Mudahale icin klavyeyi yanina al, isin bitince cikar."
Write-Host "E) SES: Ses seviyesini ayarla, sonra ses simgesinden test et; kiosk admin"
Write-Host "   panelindeki ses cubugu da (PIN 1206) ince ayar yapar."
Write-Host "========================================================"
Write-Host "KALDIRMAK ICIN: schtasks /Delete /TN Kiosk\GeceRestart /F ; schtasks /Delete /TN Kiosk\Watchdog /F"
Write-Host "               + Baslangic klasorunden NizamiyeKiosk.lnk sil."
