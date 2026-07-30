# -*- coding: utf-8 -*-
"""
FAZ 8 — _final2/*.mp3 dosyalarini kiosk/sesler/ icine gecirir (0 kredi).

Guvenlik:
  - eskiler kiosk/sesler/_yedek_eski2/ altina alinir (silinmez)
  - MELIKSAH dosyalarina dokunulmaz
  - md5 dogrulamasi: her klip _final2 ile AYNI ve yedekten FARKLI olmali
  - eksik klip varsa hicbir sey gecirilmez (yarim gecis olmaz)

  python faz8_gecir.py            -> kuru calisma
  python faz8_gecir.py --uygula   -> gecir
"""
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SES = ROOT / "kiosk" / "sesler"
FINAL = SES / "_final2"
YEDEK = SES / "_yedek_eski2"
YAZ = "--uygula" in sys.argv


def md5(p):
    return hashlib.md5(p.read_bytes()).hexdigest()


yeni = sorted(FINAL.glob("*.mp3"))
if not yeni:
    sys.exit("HATA: _final2/ bos — once faz7_post.py")

beklenen = sorted(
    p.stem for p in (ROOT / "senaryolar" / "eleven").glob("*.txt")
    if p.stem.startswith(("gazali_", "nizamulmulk_")))
gelen = sorted(p.stem for p in yeni)
eksik = set(beklenen) - set(gelen)
fazla = set(gelen) - set(beklenen)
print(f"beklenen {len(beklenen)} klip · _final2'de {len(gelen)} klip")
if eksik:
    sys.exit(f"HATA: {len(eksik)} klip eksik, gecis iptal: {sorted(eksik)}")
if fazla:
    sys.exit(f"HATA: beklenmeyen dosya: {sorted(fazla)}")
if any(p.stem.startswith("meliksah") for p in yeni):
    sys.exit("HATA: _final2 icinde meliksah var — dokunulmamali")

if YAZ:
    YEDEK.mkdir(exist_ok=True)
    for f in yeni:
        mevcut = SES / f.name
        if mevcut.exists():
            shutil.copy2(mevcut, YEDEK / f.name)
    for f in yeni:
        shutil.copy2(f, SES / f.name)

print(f"\n=== MD5 DOGRULAMA ({len(yeni)} klip) ===")
ayni_yeni = farkli_yedek = 0
uyari = []
for f in yeni:
    kurulu = SES / f.name
    if not kurulu.exists():
        uyari.append(f"{f.stem}: kiosk'ta yok")
        continue
    h_yeni, h_kurulu = md5(f), md5(kurulu)
    if h_yeni == h_kurulu:
        ayni_yeni += 1
    else:
        uyari.append(f"{f.stem}: kurulu dosya _final2 ile AYNI DEGIL")
    y = YEDEK / f.name
    if y.exists():
        if md5(y) != h_kurulu:
            farkli_yedek += 1
        else:
            uyari.append(f"{f.stem}: eski ses ile ayni (gecmemis olabilir)")

print(f"  _final2 ile birebir ayni   : {ayni_yeni}/{len(yeni)}")
print(f"  eski sesten farkli         : {farkli_yedek}/{len(yeni)}")
print(f"  meliksah dosyasi           : dokunulmadi ({len(list(SES.glob('meliksah_*.mp3')))} klip yerinde)")
if uyari:
    print("\n  !! UYARILAR:")
    for u in uyari:
        print(f"     {u}")
else:
    print("\n  uyari yok")
print("\n>>> GECIRILDI" if YAZ else "\n(kuru calisma — dosya degismedi)")
