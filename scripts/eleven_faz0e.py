# -*- coding: utf-8 -*-
"""
FAZ 0E — Ersen Tahsin'i "agirlastirma" testi.
Sorun: ses iyi ama HIZLI ve UMURSAMAZ tavir.
Kolayci: speed (tempo) + stability (olcululuk) + similarity (karakter) + noktalama (agirlik).
"""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent
OUT = ROOT / "kiosk" / "sesler" / "_test"
OUT.mkdir(parents=True, exist_ok=True)


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
ERSEN = "A2nJYsJQbhz9yDiDndcv"


def req(path, data=None, raw=False, tries=3):
    for i in range(tries):
        try:
            r = urllib.request.Request(
                BASE + path,
                data=json.dumps(data).encode() if data else None,
                headers={"xi-api-key": KEY, "Content-Type": "application/json"},
                method="POST" if data else "GET",
            )
            with urllib.request.urlopen(r, timeout=180) as resp:
                body = resp.read()
                return body if raw else json.loads(body)
        except urllib.error.HTTPError as e:
            detay = e.read().decode("utf-8", "replace")[:200]
            if e.code in (429, 500, 502, 503) and i < tries - 1:
                time.sleep(12)
                continue
            raise RuntimeError(f"HTTP {e.code}: {detay}")
    raise RuntimeError("tries bitti")


def uret(ad, metin, stab, spd, sim=0.75, boost=True, nxt=None):
    hedef = OUT / f"{ad}.mp3"
    if hedef.exists() and hedef.stat().st_size > 10_000:
        print(f"  atla (var): {ad}")
        return 0
    body = {
        "text": metin, "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": stab, "similarity_boost": sim, "style": 0.0,
                           "use_speaker_boost": boost, "speed": spd},
        "apply_text_normalization": "on", "seed": SEED,
    }
    if nxt:
        body["next_text"] = nxt
    try:
        ses = req(f"/v1/text-to-speech/{ERSEN}?output_format=mp3_44100_192", body, raw=True)
        hedef.write_bytes(ses)
        print(f"  OK: {ad}  (stab {stab} · speed {spd} · sim {sim})")
        return len(metin)
    except Exception as e:
        print(f"  HATA: {ad} -> {e}")
        return 0


# --- standart metin (Deney 3 temiz) ---
M = ("Hoş geldiniz, sefalar getirdiniz. Ben Sultan Melikşah, Alparslanın oğlu, "
     "Büyük Selçuklu Devletinin hükümdarıyım. Bir gün ordumla Akdeniz kıyısına vardım, "
     "kılıcımı üç kez suya daldırdım. Doğuda Kaşgardan bu batı denizine kadar uzanan "
     "ülkeler bana emanetti.")
M_NEXT = "Başkentim İsfahandan size selam getirdim. Haydi, sorun bakalım, anlatmak benden."

# --- agirlastirilmis noktalama: kisa cumleler + tek break (hukumdar tavri) ---
M_AGIR = ("Hoş geldiniz, sefalar getirdiniz. Ben Sultan Melikşah. Alparslanın oğlu, "
          "Büyük Selçuklu Devletinin hükümdarıyım. <break time=\"0.3s\"/> "
          "Bir gün ordumla Akdeniz kıyısına vardım. Kılıcımı üç kez suya daldırdım. "
          "Doğuda Kaşgardan bu batı denizine kadar uzanan ülkeler, bana emanetti.")

print("=== FAZ 0E: ERSEN TAHSIN — AGIRLASTIRMA ===")
print("  referans: M5_ErsenTahsin (stab 0.60 · speed 0.95) — senin 'hizli/umursamaz' dedigin\n")
harcanan = 0

# 1) sadece yavaslat
harcanan += uret("E1_speed090", M, 0.60, 0.90, nxt=M_NEXT)
# 2) daha da yavas
harcanan += uret("E2_speed085", M, 0.60, 0.85, nxt=M_NEXT)
# 3) yavas + olculu (stability yukari = daha az savruk/umursamaz)
harcanan += uret("E3_speed090_stab070", M, 0.70, 0.90, nxt=M_NEXT)
# 4) en kontrollu: yavas + yuksek stability + karaktere sadik
harcanan += uret("E4_speed088_stab075_sim085", M, 0.75, 0.88, sim=0.85, nxt=M_NEXT)
# 5) ayar + METIN agirlastirmasi (kisa cumleler + break)
harcanan += uret("E5_agir_noktalama", M_AGIR, 0.70, 0.90, nxt=M_NEXT)

sub = req("/v1/user/subscription")
print("\n===== OZET =====")
print(f"harcanan: ~{harcanan:,} karakter")
print(f"kredi: {sub.get('character_count'):,}/{sub.get('character_limit'):,} "
      f"(kalan {sub.get('character_limit') - sub.get('character_count'):,})")
