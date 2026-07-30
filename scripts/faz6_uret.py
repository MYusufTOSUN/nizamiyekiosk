# -*- coding: utf-8 -*-
"""
FAZ 6 — Gazali (Ahmet) + Nizamulmulk (Serkan Demirci) tam uretim: 46 klip.

RECETE = ses secmesinde (faz5) kullanicinin ONAYLADIGI ayarlarin birebir aynisi.
Bilerek stitching YOK (previous_text/next_text):
  - kiosk klipleri tek basina oynuyor, previous_text ilk kelimelerin tonunu bozuyordu
    (Meliksah'ta kanitlandi, faz4 retake'lerinde kaldirilinca duzeldi)
  - secme klipleri de stitching'siz uretildi; onaylanan tini o tinidir
Yeniden calistirilabilir: var olan dosyayi atlar, bu yuzden cokme kredi yakmaz.
"""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC = ROOT / "senaryolar" / "eleven"
OUT = ROOT / "kiosk" / "sesler" / "_yeni2"
OUT.mkdir(parents=True, exist_ok=True)

SEED = 424242
RECETE = {
    "gazali": {
        "isim": "Ahmet (Deep, Reassuring and Calm)",
        "voice": "75SIZa3vvET95PHhf1yD",
        "vs": {"stability": 0.60, "similarity_boost": 0.75, "style": 0.0,
               "use_speaker_boost": True, "speed": 0.95},
    },
    "nizamulmulk": {
        "isim": "Serkan Demirci (Deep, Soft and Balanced)",
        "voice": "f4D8xroRt4ZvAzDe9FGL",
        "vs": {"stability": 0.60, "similarity_boost": 0.75, "style": 0.0,
               "use_speaker_boost": True, "speed": 0.95},
    },
}


def api_key() -> str:
    k = os.environ.get("ELEVEN_API_KEY")
    if k:
        return k
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
        return winreg.QueryValueEx(h, "ELEVEN_API_KEY")[0]


KEY = api_key()


def req(path, data=None, raw=False, tries=4):
    for i in range(tries):
        try:
            r = urllib.request.Request(
                "https://api.elevenlabs.io" + path,
                data=json.dumps(data).encode() if data else None,
                headers={"xi-api-key": KEY, "Content-Type": "application/json"},
                method="POST" if data else "GET")
            with urllib.request.urlopen(r, timeout=240) as resp:
                b = resp.read()
                return b if raw else json.loads(b)
        except urllib.error.HTTPError as e:
            d = e.read().decode("utf-8", "replace")[:200]
            if e.code in (429, 500, 502, 503) and i < tries - 1:
                time.sleep(20)
                continue
            raise RuntimeError(f"HTTP {e.code}: {d}")
        except Exception:
            if i < tries - 1:
                time.sleep(10)
                continue
            raise
    raise RuntimeError("bitti")


isler = []
for kar in ("gazali", "nizamulmulk"):
    for p in sorted(SRC.glob(f"{kar}_*.txt")):
        isler.append((kar, p.stem, p.read_text(encoding="utf-8").strip()))

print(f"=== FAZ 6 — TAM URETIM ({len(isler)} klip) ===")
for kar, r in RECETE.items():
    print(f"  {kar:<13} -> {r['isim']}  speed={r['vs']['speed']} stability={r['vs']['stability']}")
print()

harcanan = basarili = atlanan = 0
hata = []
for i, (kar, kod, metin) in enumerate(isler, 1):
    hedef = OUT / f"{kod}.mp3"
    if hedef.exists() and hedef.stat().st_size > 10_000:
        atlanan += 1
        print(f"[{i:>2}/{len(isler)}] atla   {kod}")
        continue
    r = RECETE[kar]
    body = {"text": metin, "model_id": "eleven_multilingual_v2",
            "voice_settings": r["vs"], "apply_text_normalization": "on", "seed": SEED}
    try:
        ses = req(f"/v1/text-to-speech/{r['voice']}?output_format=mp3_44100_192",
                  body, raw=True)
        hedef.write_bytes(ses)
        harcanan += len(metin)
        basarili += 1
        print(f"[{i:>2}/{len(isler)}] OK     {kod:<26} {len(metin):>4} kr  {len(ses)//1024:>4} KB")
    except Exception as e:
        hata.append((kod, str(e)))
        print(f"[{i:>2}/{len(isler)}] HATA   {kod} -> {e}")
    time.sleep(0.5)

sub = req("/v1/user/subscription")
print("\n===== OZET =====")
print(f"uretildi : {basarili}   atlandi: {atlanan}   hata: {len(hata)}")
print(f"harcanan : ~{harcanan:,} kr")
print(f"kalan    : {sub['character_limit'] - sub['character_count']:,}")
if hata:
    print("\nHATALAR (script'i tekrar calistir, sadece eksikler uretilir):")
    for k, e in hata:
        print(f"  {k}: {e}")
