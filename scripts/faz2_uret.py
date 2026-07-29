# -*- coding: utf-8 -*-
"""
FAZ 2 — Tam uretim (Faz 0 testleriyle kilitlenen recete).

KARAKTER BAZLI RECETE:
  Gazali      : Murat Albayrak  · stability 0.60 · speed 0.95   (ses degismedi)
  Nizamulmulk : Seyfullah Kartal· stability 0.60 · speed 0.95
  Meliksah    : ALEX (YENI)     · stability 0.70 · speed 0.90   (agir/buyurgan tavir)

ORTAK:
  model eleven_multilingual_v2 · apply_text_normalization "on" · seed sabit
  previous_text/next_text = ayni karakterin komsu klipleri (prozodi surekliligi)
  -> "kelime kelime okuma / parcali his" sikayetinin ana caresi

Kullanim:
  python scripts/faz2_uret.py --ornek    -> sadece 3 ornek klip (_yeni/ altina)
  python scripts/faz2_uret.py --tam      -> 69 klibin tamami (_yeni/ altina)
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC = ROOT / "senaryolar" / "eleven"
OUT = ROOT / "kiosk" / "sesler" / "_yeni"
OUT.mkdir(parents=True, exist_ok=True)

MOD = "--tam" if "--tam" in sys.argv else "--ornek"


def api_key() -> str:
    k = os.environ.get("ELEVEN_API_KEY")
    if k:
        return k
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
        return winreg.QueryValueEx(h, "ELEVEN_API_KEY")[0]


KEY = api_key()
BASE = "https://api.elevenlabs.io"
SEED = 424242

# --- KILITLENEN RECETELER ---
RECETE = {
    "gazali":      {"voice": "krLzmW3By9JzaVy294Ux", "ad": "Murat Albayrak",
                    "vs": {"stability": 0.60, "similarity_boost": 0.75, "style": 0.0,
                           "use_speaker_boost": True, "speed": 0.95}},
    "nizamulmulk": {"voice": "mF7tIc9VLrznhGooGjaT", "ad": "Seyfullah Kartal",
                    "vs": {"stability": 0.60, "similarity_boost": 0.75, "style": 0.0,
                           "use_speaker_boost": True, "speed": 0.95}},
    "meliksah":    {"voice": "KediIz7pebzt5TaDHiiZ", "ad": "Alex (YENI)",
                    "vs": {"stability": 0.70, "similarity_boost": 0.75, "style": 0.0,
                           "use_speaker_boost": True, "speed": 0.90}},
}

ORNEK = ["gazali_k1_s2", "nizamulmulk_k1_s1_v1", "meliksah_k2_s1_v1"]


def req(path, data=None, raw=False, tries=4):
    for i in range(tries):
        try:
            r = urllib.request.Request(
                BASE + path,
                data=json.dumps(data).encode() if data else None,
                headers={"xi-api-key": KEY, "Content-Type": "application/json"},
                method="POST" if data else "GET",
            )
            with urllib.request.urlopen(r, timeout=240) as resp:
                body = resp.read()
                return body if raw else json.loads(body)
        except urllib.error.HTTPError as e:
            detay = e.read().decode("utf-8", "replace")[:200]
            if e.code in (429, 500, 502, 503) and i < tries - 1:
                bekle = 15 * (i + 1)
                print(f"      {e.code} -> {bekle}sn bekle...")
                time.sleep(bekle)
                continue
            raise RuntimeError(f"HTTP {e.code}: {detay}")
        except (urllib.error.URLError, TimeoutError):
            if i < tries - 1:
                time.sleep(10 * (i + 1))
                continue
            raise
    raise RuntimeError("tries bitti")


def son_cumleler(s, n=2):
    p = re.split(r"(?<=[.!?])\s+", s.strip())
    return " ".join(p[-n:])


def ilk_cumleler(s, n=2):
    p = re.split(r"(?<=[.!?])\s+", s.strip())
    return " ".join(p[:n])


# --- klipleri karaktere gore sirala (komsu baglami icin) ---
def sira_anahtari(kod):
    """int -> cevaplar -> out  (anlati sirasi)"""
    if "_int_" in kod:
        return (0, kod)
    if "_out_" in kod:
        return (2, kod)
    return (1, kod)


kliplerin = {}
for f in sorted(SRC.glob("*.txt")):
    kod = f.stem
    ch = kod.split("_")[0]
    kliplerin.setdefault(ch, []).append((kod, f.read_text(encoding="utf-8").strip()))
for ch in kliplerin:
    kliplerin[ch].sort(key=lambda t: sira_anahtari(t[0]))

hedef_kodlar = None if MOD == "--tam" else set(ORNEK)

print(f"=== FAZ 2 — {'TAM URETIM (69 klip)' if MOD=='--tam' else 'ORNEK (3 klip)'} ===")
for ch, r in RECETE.items():
    print(f"  {ch:<12} -> {r['ad']:<18} stab {r['vs']['stability']} · speed {r['vs']['speed']}")
print()

harcanan, uretilen, hata = 0, 0, []
for ch, liste in kliplerin.items():
    r = RECETE[ch]
    for i, (kod, metin) in enumerate(liste):
        if hedef_kodlar and kod not in hedef_kodlar:
            continue
        hedef = OUT / f"{kod}.mp3"
        if hedef.exists() and hedef.stat().st_size > 20_000:
            print(f"  atla (var): {kod}")
            continue
        body = {
            "text": metin,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": r["vs"],
            "apply_text_normalization": "on",
            "seed": SEED,
        }
        # komsu klip baglami (prozodi surekliligi)
        if i > 0:
            body["previous_text"] = son_cumleler(liste[i - 1][1])
        if i + 1 < len(liste):
            body["next_text"] = ilk_cumleler(liste[i + 1][1])
        try:
            ses = req(f"/v1/text-to-speech/{r['voice']}?output_format=mp3_44100_192", body, raw=True)
            hedef.write_bytes(ses)
            harcanan += len(metin)
            uretilen += 1
            print(f"  OK: {kod:<26} ({len(ses)//1024} KB, {len(metin)} kr)")
        except Exception as e:
            hata.append(kod)
            print(f"  HATA: {kod} -> {e}")
        time.sleep(0.5)

sub = req("/v1/user/subscription")
print("\n===== OZET =====")
print(f"uretilen: {uretilen} klip · ~{harcanan:,} karakter")
print(f"hatali  : {hata or 'YOK'}")
print(f"kredi   : {sub.get('character_count'):,}/{sub.get('character_limit'):,} "
      f"(kalan {sub.get('character_limit') - sub.get('character_count'):,})")
print(f"cikti   : {OUT}")
