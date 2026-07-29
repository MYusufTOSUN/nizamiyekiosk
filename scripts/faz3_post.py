# -*- coding: utf-8 -*-
"""
FAZ 3 — Son isleme (0 kredi).
  1) acompressor : klip ICI dinamik farkini bastirir (sonda bagirma sorunu)
  2) loudnorm    : IKI GECISLI, EBU R128, hedef -16 LUFS / TP -1.5 dBTP
                   -> tum klipler ayni ses seviyesine gelir

Girdi : kiosk/sesler/_yeni/*.mp3
Cikti : kiosk/sesler/_final/*.mp3
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC = ROOT / "kiosk" / "sesler" / "_yeni"
OUT = ROOT / "kiosk" / "sesler" / "_final"
OUT.mkdir(parents=True, exist_ok=True)

HEDEF_I, HEDEF_TP, HEDEF_LRA = -16.0, -1.5, 11.0
KOMPRESOR = "acompressor=threshold=-18dB:ratio=3:attack=20:release=250"


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def olc(dosya):
    """1. gecis: loudnorm olcumu"""
    r = run(["ffmpeg", "-hide_banner", "-i", str(dosya), "-af",
             f"{KOMPRESOR},loudnorm=I={HEDEF_I}:TP={HEDEF_TP}:LRA={HEDEF_LRA}:print_format=json",
             "-f", "null", "-"])
    m = re.search(r"\{[^{}]*input_i[^{}]*\}", r.stderr, re.S)
    return json.loads(m.group()) if m else None


def uygula(dosya, hedef, m):
    """2. gecis: olculen degerlerle normalize + tek sefer mp3"""
    lf = (f"{KOMPRESOR},loudnorm=I={HEDEF_I}:TP={HEDEF_TP}:LRA={HEDEF_LRA}"
          f":measured_I={m['input_i']}:measured_TP={m['input_tp']}"
          f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}"
          f":offset={m['target_offset']}:linear=true:print_format=summary")
    r = run(["ffmpeg", "-y", "-hide_banner", "-i", str(dosya), "-af", lf,
             "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "192k", str(hedef)])
    return r.returncode == 0


dosyalar = sorted(SRC.glob("*.mp3"))
if not dosyalar:
    sys.exit("HATA: _yeni/ bos")

print(f"=== FAZ 3 — {len(dosyalar)} klip normalize ediliyor (hedef {HEDEF_I} LUFS) ===")
basarili, hatali = 0, []
oncesi, sonrasi = [], []

for i, f in enumerate(dosyalar, 1):
    m = olc(f)
    if not m:
        hatali.append(f.stem)
        print(f"  [{i:2}/{len(dosyalar)}] OLCUM HATASI: {f.stem}")
        continue
    oncesi.append(float(m["input_i"]))
    if uygula(f, OUT / f.name, m):
        basarili += 1
        print(f"  [{i:2}/{len(dosyalar)}] OK: {f.stem:<26} {float(m['input_i']):6.2f} -> {HEDEF_I} LUFS")
    else:
        hatali.append(f.stem)
        print(f"  [{i:2}/{len(dosyalar)}] YAZMA HATASI: {f.stem}")

# dogrulama: ciktilarin gercek seviyesi
print("\n=== DOGRULAMA (ornekleme) ===")
for f in list(OUT.glob("*.mp3"))[:6]:
    m = olc(f)
    if m:
        sonrasi.append(float(m["input_i"]))
        print(f"  {f.stem:<28} {float(m['input_i']):6.2f} LUFS")

print("\n===== OZET =====")
print(f"islenen : {basarili}/{len(dosyalar)}")
print(f"hatali  : {hatali or 'YOK'}")
if oncesi:
    print(f"once    : {min(oncesi):6.2f} .. {max(oncesi):6.2f} LUFS  (yayilim {max(oncesi)-min(oncesi):.2f})")
if sonrasi:
    print(f"sonra   : {min(sonrasi):6.2f} .. {max(sonrasi):6.2f} LUFS  (yayilim {max(sonrasi)-min(sonrasi):.2f})")
print(f"cikti   : {OUT}")
