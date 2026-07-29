# -*- coding: utf-8 -*-
"""
FAZ 0B — Gazali icin alternatif ses adaylari + stability 0.60 dogrulamasi.
Kazanan recete (kullanici karari): stability 0.60, speed 0.95, normalize on, next_text, diakritik.
"""
import json
import os
import sys
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


# KAZANAN RECETE (kullanici karari: stability 0.60)
RECETE = {"stability": 0.60, "similarity_boost": 0.75, "style": 0.0,
          "use_speaker_boost": True, "speed": 0.95}


def uret(ad, voice_id, metin, ayar=RECETE, nxt=None):
    hedef = OUT / f"{ad}.mp3"
    if hedef.exists() and hedef.stat().st_size > 10_000:
        print(f"  atla (var): {ad}")
        return 0
    body = {"text": metin, "model_id": "eleven_multilingual_v2", "voice_settings": ayar,
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


# ---- Gazali test metni (muderris + kesme yogun) ----
T4 = ("Esselamü aleyküm evlatlarım, hoş geldiniz. Ben Gazâlî. Tus'ta doğdum, Bağdat'ta "
      "Nizâmiye Medresesi'nde baş müderris oldum. Bana Hüccetü'l-İslâm derler. "
      "Sorun bakalım, gönlünüzde ne varsa konuşalım.")
T4_NEXT = "Ah, beni talebelik günlerime götürdün evladım. Nişabur'da okurken sabah ezanıyla kalkardık."

# ---- Adaylar: bilge/anlatici profili, orta yas, Istanbul aksani ----
ADAYLAR = [
    ("G1_BARAN_wise",      "teHLF0hAua8Ry47noJ1t", "BARAN — 'wise' etiketli, sicak anlatim"),
    ("G2_Ahmet_derin",     "75SIZa3vvET95PHhf1yD", "Ahmet — derin, guven veren, sakin (30bin klon)"),
    ("G3_Adam_sakin",      "RXCCWbOxP7Hisa63Xsv5", "Adam — sakin, dinlendirici anlatici (90bin klon)"),
    ("G4_MustafaSilici",   "fg8pljYEn5ahwjyOQaro", "Mustafa Silici — profesyonel seslendirmen"),
]

print("=== FAZ 0B: GAZALI ICIN ALTERNATIF SESLER (stability 0.60) ===")
harcanan = 0
for ad, vid, aciklama in ADAYLAR:
    print(f"  -> {aciklama}")
    harcanan += uret(ad, vid, T4, nxt=T4_NEXT)

# ---- Kullanicinin hipotezi: stability 0.60 sayi klibindeki duraklamayi da duzeltir mi? ----
print("\n=== DOGRULAMA: sayi klibi @ stability 0.60 ===")
SEYFULLAH = "mF7tIc9VLrznhGooGjaT"
T2_DUZ = ("Sana bunun hikayesini anlatayım evladım. Bin altmış yedide Bağdat'ta baş medreseyi açtık; "
          "Nişabur'dan Basra'ya bir ilim ağı kurduk.")
T2_NEXT = "Hocaya maaş, talebeye burs, kütüphaneye kitap verdik. Devlet ilk kez okulu kendi işi saydı."
harcanan += uret("T2D_sayi_stab060", SEYFULLAH, T2_DUZ, nxt=T2_NEXT)

sub = req("/v1/user/subscription")
print("\n===== OZET =====")
print(f"bu testte harcanan: ~{harcanan:,} karakter")
print(f"kredi: {sub.get('character_count'):,}/{sub.get('character_limit'):,} "
      f"(kalan {sub.get('character_limit') - sub.get('character_count'):,})")
