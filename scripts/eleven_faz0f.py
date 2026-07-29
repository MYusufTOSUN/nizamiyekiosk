# -*- coding: utf-8 -*-
"""
FAZ 0F — Meliksah adaylarina E5 RECETESI (agir tavir) uygulanmis hali.
Onceki turda adaylar duz ayarla duyulmustu; E5 (stab 0.70 + speed 0.90 + kisa kesin
cumleler + break) kullanicinin begendigi tavri veriyor. Ayni tavri hepsine uyguluyoruz.
Ek olarak: 'buyurgan ama daha parlak/net' profilinde 2 yeni aday.
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

# E5 RECETESI (kullanici onayli tavir)
AYAR = {"stability": 0.70, "similarity_boost": 0.75, "style": 0.0,
        "use_speaker_boost": True, "speed": 0.90}


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


# E5 metni: kisa kesin cumleler + tek duraklama (hukumdar tavri)
M = ("Hoş geldiniz, sefalar getirdiniz. Ben Sultan Melikşah. Alparslanın oğlu, "
     "Büyük Selçuklu Devletinin hükümdarıyım. <break time=\"0.3s\"/> "
     "Bir gün ordumla Akdeniz kıyısına vardım. Kılıcımı üç kez suya daldırdım. "
     "Doğuda Kaşgardan bu batı denizine kadar uzanan ülkeler, bana emanetti.")
M_NEXT = "Başkentim İsfahandan size selam getirdim. Haydi, sorun bakalım, anlatmak benden."


def uret(ad, voice_id, aciklama):
    hedef = OUT / f"{ad}.mp3"
    if hedef.exists() and hedef.stat().st_size > 10_000:
        print(f"  atla (var): {ad}")
        return 0
    body = {"text": M, "model_id": "eleven_multilingual_v2", "voice_settings": AYAR,
            "apply_text_normalization": "on", "seed": SEED, "next_text": M_NEXT}
    try:
        ses = req(f"/v1/text-to-speech/{voice_id}?output_format=mp3_44100_192", body, raw=True)
        hedef.write_bytes(ses)
        print(f"  OK: {ad:<24} — {aciklama}")
        return len(M)
    except Exception as e:
        print(f"  HATA: {ad} -> {e}")
        return 0


ADAYLAR = [
    # daha once duz ayarla duyulanlar — simdi E5 tavriyla
    ("X1_CavitPancar_e5", "Y2T2O1csKPgWgyuKcU0a", "Cavit Pancar 'Epic Powerful Historical' (67bin)"),
    ("X2_Cem_e5",         "D1xRw7f8ZHedI7xJgfvz", "Cem 'Heroic, Intense and CRISP' (net/parlak)"),
    ("X3_UgurDundar_e5",  "4wIwWOJQDSnu6fx2m4Lp", "Ugur Dundar 'Bass-heavy' (en kalin)"),
    ("X4_Altay_e5",       "pBWsNSeOv3iObhaIuRO1", "Altay 'Deep, Cinematic & Rich'"),
    # yeni: buyurgan AMA daha parlak/net profil (kullanici 'frekans yukari' istedi)
    ("X5_Polat_e5",       "lV90UmdRoVFQHzkxUPeu", "Polat 'Deep, Dynamic and Confident' (classy)"),
    ("X6_Alex_e5",        "KediIz7pebzt5TaDHiiZ", "Alex 'Deep, CLEAR and Fluent' (37bin, net)"),
]

print("=== FAZ 0F: ADAYLAR x E5 RECETESI (stab 0.70 · speed 0.90 · kisa cumle + break) ===\n")
harcanan = 0
for ad, vid, aciklama in ADAYLAR:
    harcanan += uret(ad, vid, aciklama)

sub = req("/v1/user/subscription")
print("\n===== OZET =====")
print(f"harcanan: ~{harcanan:,} karakter")
print(f"kredi: {sub.get('character_count'):,}/{sub.get('character_limit'):,} "
      f"(kalan {sub.get('character_limit') - sub.get('character_count'):,})")
