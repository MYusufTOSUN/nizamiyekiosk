# -*- coding: utf-8 -*-
"""
FAZ 7 — Gazali + Nizamulmulk son isleme (0 kredi).
  1) acompressor : klip ICI dinamik farkini bastirir
  2) loudnorm    : IKI GECISLI, EBU R128, hedef -16 LUFS / TP -1.5 dBTP

faz3'e gore duzeltme: DOGRULAMA olcumu kompresorsuz yapilir. faz3'te dogrulama
zincirine kompresor de girdigi icin cikti -21.8 LUFS gibi yanlis okunuyordu.

Girdi : kiosk/sesler/_yeni2/*.mp3
Cikti : kiosk/sesler/_final2/*.mp3
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC = ROOT / "kiosk" / "sesler" / "_yeni2"
OUT = ROOT / "kiosk" / "sesler" / "_final2"
OUT.mkdir(parents=True, exist_ok=True)

HEDEF_I, HEDEF_TP, HEDEF_LRA = -16.0, -1.5, 11.0
KOMPRESOR = "acompressor=threshold=-18dB:ratio=3:attack=20:release=250"


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def olc(dosya, kompresorlu):
    """loudnorm olcumu. kompresorlu=True -> 1. gecis; False -> saf dogrulama."""
    zincir = (f"{KOMPRESOR}," if kompresorlu else "") + \
             f"loudnorm=I={HEDEF_I}:TP={HEDEF_TP}:LRA={HEDEF_LRA}:print_format=json"
    r = run(["ffmpeg", "-hide_banner", "-i", str(dosya), "-af", zincir, "-f", "null", "-"])
    m = re.search(r"\{[^{}]*input_i[^{}]*\}", r.stderr, re.S)
    return json.loads(m.group()) if m else None


def uygula(dosya, hedef, m):
    lf = (f"{KOMPRESOR},loudnorm=I={HEDEF_I}:TP={HEDEF_TP}:LRA={HEDEF_LRA}"
          f":measured_I={m['input_i']}:measured_TP={m['input_tp']}"
          f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}"
          f":offset={m['target_offset']}:linear=true:print_format=summary")
    r = run(["ffmpeg", "-y", "-hide_banner", "-i", str(dosya), "-af", lf,
             "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "192k", str(hedef)])
    return r.returncode == 0


dosyalar = sorted(SRC.glob("*.mp3"))
if not dosyalar:
    sys.exit("HATA: _yeni2/ bos")

print(f"=== FAZ 7 — {len(dosyalar)} klip normalize ediliyor (hedef {HEDEF_I} LUFS) ===")
hatali, oncesi = [], []
for i, f in enumerate(dosyalar, 1):
    m = olc(f, True)
    if not m:
        hatali.append(f.stem)
        print(f"  [{i:2}/{len(dosyalar)}] OLCUM HATASI: {f.stem}")
        continue
    oncesi.append(float(m["input_i"]))
    if uygula(f, OUT / f.name, m):
        print(f"  [{i:2}/{len(dosyalar)}] OK: {f.stem:<28} {float(m['input_i']):6.2f} -> {HEDEF_I}")
    else:
        hatali.append(f.stem)
        print(f"  [{i:2}/{len(dosyalar)}] YAZMA HATASI: {f.stem}")

print(f"\n=== DOGRULAMA (kompresorsuz, TUM ciktilar) ===")
sonrasi, sapan = [], []
for f in sorted(OUT.glob("*.mp3")):
    m = olc(f, False)
    if not m:
        continue
    v = float(m["input_i"])
    sonrasi.append(v)
    if abs(v - HEDEF_I) > 1.0:
        sapan.append((f.stem, v))
print(f"  olculen: {len(sonrasi)} klip")
if sapan:
    print("  !! HEDEFTEN >1 LUFS SAPAN:")
    for k, v in sapan:
        print(f"     {k:<28} {v:6.2f}")
else:
    print("  hepsi hedefin 1 LUFS icinde")

print("\n===== OZET =====")
print(f"islenen : {len(dosyalar) - len(hatali)}/{len(dosyalar)}")
print(f"hatali  : {hatali or 'YOK'}")
if oncesi:
    print(f"once    : {min(oncesi):6.2f} .. {max(oncesi):6.2f} LUFS  (yayilim {max(oncesi)-min(oncesi):.2f})")
if sonrasi:
    print(f"sonra   : {min(sonrasi):6.2f} .. {max(sonrasi):6.2f} LUFS  (yayilim {max(sonrasi)-min(sonrasi):.2f})")
print(f"cikti   : {OUT}")
