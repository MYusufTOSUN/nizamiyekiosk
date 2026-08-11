# ===========================================================================
#  DEVIR 1/2 - ESKI PC'de calistir: her seyi USB'ye hazirlar
#
#  Kullanim:
#     powershell -ExecutionPolicy Bypass -File scripts\devir_hazirla.ps1 -Hedef E:\
#     ... -AtlaModeller     17 GB'lik data/models'i disarida birak
#     ... -Dogrula          kopya sonrasi karsilastirma yapar (yavas ama kesin)
#
#  NE TASINIR / NE TASINMAZ - gerekceleriyle:
#    .venv          TASINMAZ. Icinde MUTLAK yollar gomulu (pyvenv.cfg,
#                   Scripts\*.exe shim'leri). Baska makinede calismaz.
#                   Yeni PC'de devir_kur.ps1 yeniden kurar. 7,6 GB tasarruf.
#    kiosk/videolar TASINMAZ. video/02_ham'a SABIT BAG (ayni inode). Diskte tek
#                   kopya; USB'ye kopyalanirsa iki kopya olur. Yeni PC'de
#                   devir_kur.ps1 baglari yeniden kurar. 4,5 GB tasarruf.
#    .git           TASINIR ama once temizlenir. Bu makinede 5,0 GB'in 4,92 GB'i
#                   OKSUZ nesne (351 blob, dangling commit YOK - fsck ile
#                   dogrulandi). git gc sonrasi ~10 MB.
#    Geri kalan HER SEY tasinir: 69 video, 573 ses (tum yedekler dahil),
#    master gorseller, makaleler, senaryolar, modeller, kod, git gecmisi.
# ===========================================================================

param(
  [Parameter(Mandatory=$true)][string]$Hedef,
  [switch]$AtlaModeller,
  [switch]$Dogrula
)

$ErrorActionPreference = "Stop"
$Kok = Split-Path -Parent $PSScriptRoot
Set-Location $Kok
$Ad  = Split-Path -Leaf $Kok
$Var = Join-Path $Hedef $Ad

Write-Host ""
Write-Host "=== DEVIR HAZIRLIGI ===" -ForegroundColor Cyan
Write-Host "  kaynak : $Kok"
Write-Host "  hedef  : $Var"
Write-Host ""

if (-not (Test-Path $Hedef)) { Write-Host "HATA: hedef yok: $Hedef" -ForegroundColor Red; exit 1 }

# --------------------------------------------------------------- 1) git temizligi
Write-Host "[1/5] Git deposu temizleniyor..." -ForegroundColor Yellow
$onceGit = (Get-ChildItem ".git" -Recurse -Force -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum).Sum
$kirli = git status --porcelain
if ($kirli) {
  Write-Host "  UYARI: commit edilmemis degisiklik var:" -ForegroundColor Yellow
  $kirli | Select-Object -First 10 | ForEach-Object { "    $_" }
  Write-Host "  Bunlar USB'ye kopyalanacak ama git gecmisinde OLMAYACAK." -ForegroundColor Yellow
}
git gc --prune=now --quiet
$sonraGit = (Get-ChildItem ".git" -Recurse -Force -ErrorAction SilentlyContinue |
             Measure-Object -Property Length -Sum).Sum
Write-Host ("  .git: {0:N0} MB -> {1:N0} MB" -f ($onceGit/1MB), ($sonraGit/1MB)) -ForegroundColor Green

# --------------------------------------------------------------- 2) envanter
Write-Host ""
Write-Host "[2/5] Envanter cikariliyor..." -ForegroundColor Yellow
$envanter = Join-Path $Kok "DEVIR_ENVANTER.txt"
$satirlar = @("BilimFest devir envanteri", "olusturuldu: $(Get-Date -Format 'yyyy-MM-dd HH:mm')", "")
foreach ($k in @("video\02_ham", "kiosk\sesler", "video\00_master", "data\models",
                 "senaryolar", "makaleler", "kiosk", "scripts", "src")) {
  $p = Join-Path $Kok $k
  if (Test-Path $p) {
    $d = Get-ChildItem $p -Recurse -File -ErrorAction SilentlyContinue
    $satirlar += ("{0,-24} {1,6} dosya  {2,10:N1} MB" -f $k, $d.Count, (($d | Measure-Object Length -Sum).Sum/1MB))
  }
}
$satirlar += ""
$satirlar += "--- video/02_ham SHA256 (butunluk kontrolu icin) ---"
Get-ChildItem "video\02_ham\*.mp4" -ErrorAction SilentlyContinue | ForEach-Object {
  $satirlar += ("{0}  {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.Substring(0,16), $_.Name)
}
$satirlar | Set-Content $envanter -Encoding utf8
Write-Host "  yazildi: DEVIR_ENVANTER.txt ($($satirlar.Count) satir)" -ForegroundColor Green

# --------------------------------------------------------------- 3) kopyalama
Write-Host ""
Write-Host "[3/5] USB'ye kopyalaniyor (robocopy)..." -ForegroundColor Yellow
$disla = @(".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
           "node_modules", "kiosk\videolar")
if ($AtlaModeller) { $disla += "data\models" }

$xd = @()
foreach ($d in $disla) { $xd += "/XD"; $xd += (Join-Path $Kok $d) }

# /E (alt klasorler dahil kopyala) kullaniliyor, /MIR DEGIL.
# /MIR aynalama yapar: hedefte olup kaynakta olmayani SILER. USB'de baska
# projeler ve onyukleme medyasi olabilir - hicbir kosulda silme riski almiyoruz.
$rc = @($Kok, $Var, "/E", "/R:2", "/W:3", "/MT:8", "/NFL", "/NDL", "/NP",
        "/XF", "*.pyc", "uvicorn.out", "uvicorn.err") + $xd
Write-Host "  disarida: $($disla -join ', ')" -ForegroundColor DarkGray
robocopy @rc | Out-Null
$kod = $LASTEXITCODE
if ($kod -ge 8) { Write-Host "  HATA: robocopy $kod" -ForegroundColor Red; exit 1 }
Write-Host "  tamam (robocopy $kod)" -ForegroundColor Green

# --------------------------------------------------------------- 4) site deposu
Write-Host ""
Write-Host "[4/5] Web sitesi deposu..." -ForegroundColor Yellow
$site = Join-Path (Split-Path -Parent $Kok) "nizamiye"
if (Test-Path $site) {
  $siteVar = Join-Path $Hedef "nizamiye"
  robocopy $site $siteVar /E /R:2 /W:3 /MT:8 /NFL /NDL /NP /XD (Join-Path $site "node_modules") (Join-Path $site "dist") | Out-Null
  Write-Host "  kopyalandi: nizamiye (node_modules ve dist haric)" -ForegroundColor Green
} else {
  Write-Host "  bulunamadi: $site - atlandi" -ForegroundColor Yellow
}

# --------------------------------------------------------------- 5) ozet
Write-Host ""
Write-Host "[5/5] Dogrulama..." -ForegroundColor Yellow
function Say($yol, $desen) {
  if (Test-Path $yol) { (Get-ChildItem $yol -Filter $desen -ErrorAction SilentlyContinue).Count } else { 0 }
}
$kontrol = @(
  @{ ad = "video (02_ham)";  k = (Say "$Kok\video\02_ham" "*.mp4");        h = (Say "$Var\video\02_ham" "*.mp4") },
  @{ ad = "ses (kok)";       k = (Say "$Kok\kiosk\sesler" "*.mp3");        h = (Say "$Var\kiosk\sesler" "*.mp3") },
  @{ ad = "master (_v7)";    k = (Say "$Kok\video\00_master\kadrajli\_v7" "*.png"); h = (Say "$Var\video\00_master\kadrajli\_v7" "*.png") },
  @{ ad = "hafiza";          k = (Say "$Kok\docs\ajan_hafizasi" "*.md");   h = (Say "$Var\docs\ajan_hafizasi" "*.md") }
)
$hepsiTamam = $true
foreach ($c in $kontrol) {
  $ok = $c.k -eq $c.h -and $c.k -gt 0
  if (-not $ok) { $hepsiTamam = $false }
  $renk = if ($ok) { "Green" } else { "Red" }
  Write-Host ("  {0,-18} kaynak {1,4}  hedef {2,4}  {3}" -f $c.ad, $c.k, $c.h, $(if ($ok) { "TAMAM" } else { "EKSIK" })) -ForegroundColor $renk
}

$sesYedek = (Get-ChildItem "$Kok\kiosk\sesler" -Directory).Count
$sesYedekH = if (Test-Path "$Var\kiosk\sesler") { (Get-ChildItem "$Var\kiosk\sesler" -Directory).Count } else { 0 }
$renk = if ($sesYedek -eq $sesYedekH) { "Green" } else { "Red" }
Write-Host ("  {0,-18} kaynak {1,4}  hedef {2,4}  {3}" -f "ses yedek klasoru", $sesYedek, $sesYedekH,
  $(if ($sesYedek -eq $sesYedekH) { "TAMAM" } else { "EKSIK" })) -ForegroundColor $renk

if ($Dogrula) {
  Write-Host ""
  Write-Host "  SHA256 karsilastirmasi (video/02_ham)..." -ForegroundColor DarkGray
  $hata = 0
  Get-ChildItem "$Kok\video\02_ham\*.mp4" | ForEach-Object {
    $h2 = Join-Path "$Var\video\02_ham" $_.Name
    if (-not (Test-Path $h2)) { $hata++; Write-Host "    EKSIK: $($_.Name)" -ForegroundColor Red }
    elseif ((Get-FileHash $_.FullName -Algorithm SHA256).Hash -ne (Get-FileHash $h2 -Algorithm SHA256).Hash) {
      $hata++; Write-Host "    BOZUK: $($_.Name)" -ForegroundColor Red
    }
  }
  if ($hata -eq 0) { Write-Host "    69/69 birebir ayni" -ForegroundColor Green }
  else { $hepsiTamam = $false }
}

$boyut = (Get-ChildItem $Var -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
Write-Host ""
Write-Host ("USB'deki toplam: {0:N1} GB" -f ($boyut/1GB)) -ForegroundColor Cyan
if ($hepsiTamam) {
  Write-Host "HAZIR. USB'yi yeni PC'ye tak ve orada devir_kur.ps1'i calistir." -ForegroundColor Green
} else {
  Write-Host "EKSIK VAR - yukaridaki kirmizi satirlara bak." -ForegroundColor Red
  exit 1
}
