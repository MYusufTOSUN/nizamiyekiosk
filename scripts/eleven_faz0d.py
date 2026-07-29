# -*- coding: utf-8 -*-
"""
FAZ 0D — Meliksah icin TOK / KALIN / BUYURGAN ses adaylari.
Kilitlenen recete: stability 0.60, speed 0.95, normalize on, next_text,
metin temizligi = DENEY 3 (kesme yok, noktali virgul yok, sayilar yaziyla, diakritik).
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
AYAR = {"stability": 0.60, "similarity_boost": 0.75, "style": 0.0,
        "use_speaker_boost": True, "speed": 0.95}


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


def uret(ad, voice_id, metin, nxt=None):
    hedef = OUT / f"{ad}.mp3"
    if hedef.exists() and hedef.stat().st_size > 10_000:
        print(f"  atla (var): {ad}")
        return 0
    body = {"text": metin, "model_id": "eleven_multilingual_v2", "voice_settings": AYAR,
            "apply_text_normalization": "on", "seed": SEED}
    if nxt:
        body["next_text"] = nxt
    try:
        ses = req(f"/v1/text-to-speech/{voice_id}?output_format=mp3_44100_192", body, raw=True)
        hedef.write_bytes(ses)
        print(f"  OK: {ad}  ({len(ses)//1024} KB)")
        return len(metin)
    except Exception as e:
        print(f"  HATA: {ad} -> {e}")
        return 0


# --- Meliksah test metni: intro + buyurgan pasaj (DENEY 3 kurallariyla temizlenmis) ---
# kesme yok, noktali virgul yok, Alp Arslan -> Alparslan (bosluk duraklamasi)
M = ("Hoş geldiniz, sefalar getirdiniz. Ben Sultan Melikşah, Alparslanın oğlu, "
     "Büyük Selçuklu Devletinin hükümdarıyım. Bir gün ordumla Akdeniz kıyısına vardım, "
     "kılıcımı üç kez suya daldırdım. Doğuda Kaşgardan bu batı denizine kadar uzanan "
     "ülkeler bana emanetti.")
M_NEXT = "Başkentim İsfahandan size selam getirdim. Haydi, sorun bakalım, anlatmak benden."

ADAYLAR = [
    ("M0_Aykut_mevcut",   "VtLFdkOJSt8TuXqwEzD8", "Aykut Akkaşoğlu — MEVCUT ses (kıyas)"),
    ("M1_CavitPancar",    "Y2T2O1csKPgWgyuKcU0a", "Cavit Pancar — 'Epic Powerful Historical' (67bin klon)"),
    ("M2_Cem_heroik",     "D1xRw7f8ZHedI7xJgfvz", "Cem — 'Heroic, Intense and Crisp' (30bin)"),
    ("M3_UgurDundar",     "4wIwWOJQDSnu6fx2m4Lp", "Ugur Dundar — 'Bass-heavy' KALIN, profesyonel"),
    ("M4_Arthur_bass",    "OMPrkDOvnYasPecb05Rb", "Arthur — 'Bass, Deep and Clear' (7bin)"),
    ("M5_ErsenTahsin",    "A2nJYsJQbhz9yDiDndcv", "Ersen Tahsin — derin, TOK (rich), net (8bin)"),
    ("M6_Altay_sinema",   "pBWsNSeOv3iObhaIuRO1", "Altay — 'Deep, Cinematic & Rich'"),
]

print("=== FAZ 0D: MELIKSAH — TOK/KALIN/BUYURGAN SESLER ===")
harcanan = 0
for ad, vid, aciklama in ADAYLAR:
    print(f"  -> {aciklama}")
    harcanan += uret(ad, vid, M, nxt=M_NEXT)

sub = req("/v1/user/subscription")
print("\n===== OZET =====")
print(f"harcanan: ~{harcanan:,} karakter")
print(f"kredi: {sub.get('character_count'):,}/{sub.get('character_limit'):,} "
      f"(kalan {sub.get('character_limit') - sub.get('character_count'):,})")
