# Nizamiye Kiosk - iki ekranli baslatici
#
# NEDEN BOYLE:
#   1) file:// DEGIL, http://127.0.0.1 uzerinden servis ediliyor. Iki pencerenin
#      birbiriyle konusabilmesi (BroadcastChannel) ayni kokende olmalarini sart
#      kosuyor; file:// kokeni "opaque" sayildigi icin orada calismiyor.
#   2) Pencereleri tarayiciya window.open ile actirmiyoruz - kiosk modunda
#      acilir pencere engelleniyor. Her ekrana ayri pencereyi BU betik aciyor.
#   3) Iki pencere AYNI --user-data-dir'i paylasiyor; farkli olsalardi ayri
#      tarayici ornegi olurlar ve BroadcastChannel aralarindan gecmezdi.

param(
  [int]$Port = 8777,
  [switch]$TekEkran          # ikinci ekran yokken/ istemezken
)

$ErrorActionPreference = "Stop"
$Kok    = Split-Path -Parent $PSScriptRoot
$KioskD = Join-Path $Kok "kiosk"
$Profil = Join-Path $env:LOCALAPPDATA "NizamiyeKiosk\tarayici"

if (-not (Test-Path (Join-Path $KioskD "index.html"))) {
  Write-Host "HATA: kiosk/index.html bulunamadi ($KioskD)" -ForegroundColor Red; exit 1
}

# ---------------------------------------------------------------- 1) sunucu
function Sunucu-Ayakta {
  try { (Invoke-WebRequest "http://127.0.0.1:$Port/index.html" -TimeoutSec 2 -UseBasicParsing).StatusCode -eq 200 }
  catch { $false }
}

if (Sunucu-Ayakta) {
  Write-Host "Sunucu zaten calisiyor (port $Port)." -ForegroundColor DarkGray
} else {
  $py = Join-Path $Kok ".venv\Scripts\python.exe"
  if (-not (Test-Path $py)) { $py = (Get-Command python -ErrorAction SilentlyContinue).Source }
  if (-not $py) { Write-Host "HATA: python bulunamadi." -ForegroundColor Red; exit 1 }
  Write-Host "Yerel sunucu baslatiliyor: http://127.0.0.1:$Port"
  Start-Process -FilePath $py `
    -ArgumentList @("-m","http.server",$Port,"--bind","127.0.0.1","--directory",$KioskD) `
    -WindowStyle Hidden
  $bekle = 0
  while (-not (Sunucu-Ayakta)) {
    Start-Sleep -Milliseconds 300; $bekle++
    if ($bekle -gt 30) { Write-Host "HATA: sunucu acilmadi." -ForegroundColor Red; exit 1 }
  }
}

# ---------------------------------------------------------------- 2) ekranlar
# NOT: Windows DPI olcegi varsa Bounds MANTIKSAL piksel verir. Bu makinede
# birincil ekran fiziksel 1920x1200 ama %125 olcekle 1536x960 bildiriliyor;
# pencereyi 1536x960 acmak ekranin tamamini kapliyor - dogrusu bu.
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public class NizDpi {
  [DllImport("gdi32.dll")] public static extern int GetDeviceCaps(IntPtr h, int i);
  [DllImport("user32.dll")] public static extern IntPtr GetDC(IntPtr h);
  public static int[] Fiziksel() {
    IntPtr dc = GetDC(IntPtr.Zero);
    return new int[] { GetDeviceCaps(dc, 118), GetDeviceCaps(dc, 117) };  // DESKTOPHORZ/VERTRES
  }
}
"@
$ekranlar = [System.Windows.Forms.Screen]::AllScreens
$birincil = $ekranlar | Where-Object { $_.Primary } | Select-Object -First 1
$ikincil  = $ekranlar | Where-Object { -not $_.Primary } | Select-Object -First 1
if ($TekEkran) { $ikincil = $null }

$fiz = [NizDpi]::Fiziksel()
$olcekYuzde = [math]::Round($fiz[0] / $birincil.Bounds.Width * 100)
Write-Host ("Birincil (kiosk)  : {0}x{1} @ {2},{3}   [fiziksel {4}x{5}, DPI %{6}]" -f `
  $birincil.Bounds.Width, $birincil.Bounds.Height, $birincil.Bounds.X, $birincil.Bounds.Y, `
  $fiz[0], $fiz[1], $olcekYuzde)
if ($ikincil) {
  Write-Host ("Ikincil (hologram): {0}x{1} @ {2},{3}" -f $ikincil.Bounds.Width, $ikincil.Bounds.Height, $ikincil.Bounds.X, $ikincil.Bounds.Y)
} else {
  Write-Host "Ikinci ekran yok - yalniz kiosk acilacak." -ForegroundColor Yellow
}

# ---------------------------------------------------------------- 3) tarayici
$pf   = $env:ProgramFiles
$pf86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
$adaylar = @(
  (Join-Path $pf86 "Microsoft\Edge\Application\msedge.exe"),
  (Join-Path $pf   "Microsoft\Edge\Application\msedge.exe"),
  (Join-Path $pf   "Google\Chrome\Application\chrome.exe"),
  (Join-Path $pf86 "Google\Chrome\Application\chrome.exe")
)
$tarayici = $null
foreach ($a in $adaylar) {
  if ($a -and (Test-Path $a)) { $tarayici = $a; break }
}
if (-not $tarayici) { Write-Host "HATA: Edge/Chrome bulunamadi." -ForegroundColor Red; exit 1 }
Write-Host "Tarayici: $tarayici" -ForegroundColor DarkGray

$ortak = @(
  "--user-data-dir=$Profil"                    # iki pencere AYNI profil - kanal icin sart
  "--autoplay-policy=no-user-gesture-required" # video kendiliginden oynasin
  "--no-first-run"
  "--no-default-browser-check"
  "--disable-features=Translate,TranslateUI"
  "--disable-session-crashed-bubble"
  "--disable-pinch"
  "--overscroll-history-navigation=0"
  "--disable-background-timer-throttling"      # arka plan penceresi yavaslamasin
  "--disable-backgrounding-occluded-windows"
  "--disable-renderer-backgrounding"
)

function Pencere-Ac($url, $ekran) {
  $b = $ekran.Bounds
  $arg = $ortak + @(
    "--app=$url"
    "--window-position=$($b.X),$($b.Y)"
    "--window-size=$($b.Width),$($b.Height)"
    "--start-fullscreen"
  )
  Start-Process -FilePath $tarayici -ArgumentList $arg
}

# Once HOLOGRAM: kiosk acildiginda karsisinda hazir bulsun.
if ($ikincil) {
  Pencere-Ac "http://127.0.0.1:$Port/hologram.html" $ikincil
  Start-Sleep -Milliseconds 1400
}
Pencere-Ac "http://127.0.0.1:$Port/index.html" $birincil

# --------------------------------------------------------- 4) pencereleri yerlestir
# Edge ikinci --app penceresini bazen SIMGE DURUMUNDA aciyor (-32000,-32000).
# Baslangic bayraklarina guvenmeyip pencereleri Win32 ile yerine oturtuyoruz.
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class NizPencere {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr p);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern int  GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int t, bool r);
  public delegate bool EnumProc(IntPtr h, IntPtr p);
  public static IntPtr Bul(string parca) {
    IntPtr bulunan = IntPtr.Zero;
    EnumWindows((h, p) => {
      var sb = new StringBuilder(300); GetWindowText(h, sb, 300);
      if (sb.ToString().IndexOf(parca, StringComparison.OrdinalIgnoreCase) >= 0) { bulunan = h; return false; }
      return true;
    }, IntPtr.Zero);
    return bulunan;
  }
  public static void Yerlestir(IntPtr h, int x, int y, int w, int t) {
    ShowWindow(h, 9);                 // SW_RESTORE - simge durumundan cikar
    MoveWindow(h, x, y, w, t, true);
    SetForegroundWindow(h);
  }
}
"@

function Yerine-Koy($parca, $ekran, $etiket) {
  for ($i = 0; $i -lt 24; $i++) {
    $h = [NizPencere]::Bul($parca)
    if ($h -ne [IntPtr]::Zero) {
      $b = $ekran.Bounds
      [NizPencere]::Yerlestir($h, $b.X, $b.Y, $b.Width, $b.Height)
      Write-Host ("  {0}: yerlestirildi -> {1},{2}  {3}x{4}" -f $etiket, $b.X, $b.Y, $b.Width, $b.Height)
      return $true
    }
    Start-Sleep -Milliseconds 400
  }
  Write-Host ("  {0}: pencere bulunamadi ({1})" -f $etiket, $parca) -ForegroundColor Yellow
  return $false
}

Start-Sleep -Milliseconds 1200
Write-Host ""
Write-Host "Pencereler yerlestiriliyor:"
if ($ikincil) { Yerine-Koy "Hologram Ekran" $ikincil "hologram" | Out-Null }
Yerine-Koy "Medresesi" $birincil "kiosk" | Out-Null

Write-Host ""
Write-Host "Hazir." -ForegroundColor Green
Write-Host "  Kiosk    : http://127.0.0.1:$Port/index.html"
if ($ikincil) { Write-Host "  Hologram : http://127.0.0.1:$Port/hologram.html" }
Write-Host ""
Write-Host "Kapatmak icin: pencerelerde Alt+F4, sonra bu pencerede Ctrl+C." -ForegroundColor DarkGray
