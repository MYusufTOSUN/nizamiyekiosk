# -*- coding: utf-8 -*-
"""
FAZ 5 — Nizamulmulk ve Gazali icin ses adayi testi.
Recete: stability 0.60 · speed 0.95 · normalize on · previous_text YOK (taze acilis).
Test metni: her karakterin GERCEK intro'su + bir cevabin acilisi (~330 kr).
"""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent
OUT = ROOT / "kiosk" / "sesler" / "_ses_test"
OUT.mkdir(parents=True, exist_ok=True)


def api_key() -> str:
    k = os.environ.get("ELEVEN_API_KEY")
    if k:
        return k
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
        return winreg.QueryValueEx(h, "ELEVEN_API_KEY")[0]


KEY = api_key()
SEED = 424242
AYAR = {"stability": 0.60, "similarity_boost": 0.75, "style": 0.0,
        "use_speaker_boost": True, "speed": 0.95}


def req(path, data=None, raw=False, tries=3):
    for i in range(tries):
        try:
            r = urllib.request.Request(
                "https://api.elevenlabs.io" + path,
                data=json.dumps(data).encode() if data else None,
                headers={"xi-api-key": KEY, "Content-Type": "application/json"},
                method="POST" if data else "GET")
            with urllib.request.urlopen(r, timeout=180) as resp:
                b = resp.read()
                return b if raw else json.loads(b)
        except urllib.error.HTTPError as e:
            d = e.read().decode("utf-8", "replace")[:200]
            if e.code in (429, 500, 502, 503) and i < tries - 1:
                time.sleep(15)
                continue
            raise RuntimeError(f"HTTP {e.code}: {d}")
    raise RuntimeError("bitti")


def uret(ad, vid, metin):
    hedef = OUT / f"{ad}.mp3"
    if hedef.exists() and hedef.stat().st_size > 10_000:
        print(f"  atla: {ad}")
        return 0
    body = {"text": metin, "model_id": "eleven_multilingual_v2", "voice_settings": AYAR,
            "apply_text_normalization": "on", "seed": SEED}
    try:
        ses = req(f"/v1/text-to-speech/{vid}?output_format=mp3_44100_192", body, raw=True)
        hedef.write_bytes(ses)
        print(f"  OK: {ad}")
        return len(metin)
    except Exception as e:
        print(f"  HATA: {ad} -> {e}")
        return 0


# ---------- NIZAMULMULK: bilge yasli vezir ----------
# int_2 (kullanicinin 'en berbat' dedigi) + adalet cevabinin acilisi
N_TEXT = ("Sana bir sorum var evladım. Bir devleti ayakta tutan nedir dersin, ordu mu, hazine mi? "
          "Ben ikisini de yönettim ama cevabım başkadır: adalet ve ilim. "
          "Bir gün bir âlim bana dedi ki, Allah seni kullarının başına geçirdi, "
          "yarın hesap günü onları senden sorarsa ne cevap vereceksin? "
          "O sözün ağırlığı altında ağladım. Çünkü makam bir emanettir.")

NIZ = [
    ("N0_MEVCUT_Seyfullah", "mF7tIc9VLrznhGooGjaT", "MEVCUT — Seyfullah Kartal ('Deep and Muffled')"),
    ("N1_ErgunKalbak",      "0DihkedLJYKoWg7H1u4d", "Ergun Kalbak — derin, sakin, NET · YAŞLI (8bin)"),
    ("N2_Emin_tarihi",      "j9K9HnBcmgA6xNWqjlX0", "Emin — 'Epic Historical Narrative' · YAŞLI (27bin)"),
    ("N3_Ahmet_saglam",     "ZsYcqahfiS2dy4J6XYC5", "Ahmet — sağlam, kendinden emin, derin (29bin)"),
    ("N4_Erman_hikayeci",   "cnssGWqE7kJax1MvsjA8", "Erman — profesyonel hikâyeci · YAŞLI"),
    ("N5_Sinan_net",        "Rn6ATQ4HFbyhBC6mze4Z", "Sinan — derin ve NET Türkçe anlatıcı"),
]

# ---------- GAZALI: sicak ogretmen, temiz kayit ----------
G_TEXT = ("Esselamü aleyküm evlatlarım, hoş geldiniz. Ben Gazâlî. Tusta doğdum, Bağdatta "
          "Nizâmiye Medresesinde baş müderris oldum. Bana Hüccetül İslâm derler. "
          "Ah, beni talebelik günlerime götürdün evladım. Nişaburda okurken sabah ezanıyla kalkardık. "
          "Avluda abdest alır, ilk derse otururduk.")

GAZ = [
    ("G0_MEVCUT_MuratAlbayrak", "krLzmW3By9JzaVy294Ux", "MEVCUT — Murat Albayrak (use_case: social_media)"),
    ("G1_OzanKaraca",           "5nr6ATQepuidiLb6OT3B", "Ozan Karaca — TOK, sıcak, sakin (23bin)"),
    ("G2_Erkan_bilge",          "xGTDz7OJgnhpupime6Pt", "Erkan — 'Calm, WISE and Intellectual' · yumuşak"),
    ("G3_Ahmet_guven",          "75SIZa3vvET95PHhf1yD", "Ahmet — derin, güven veren (senin beğendiğin, 31bin)"),
    ("G4_Harun_sicak",          "52ak3VZKnZ6itUyXd6P9", "Harun — sıcak, davetkâr · Anadolu aksanı"),
    ("G5_Serkan_diplomatik",    "gHbwCuxXgsw2gfwo7GSs", "Serkan — diplomatik, tok, güven veren"),
]

print("=== FAZ 5 — SES ADAYLARI ===\n")
harcanan = 0
print("--- NIZAMULMULK (bilge yaşlı vezir) ---")
for ad, vid, aciklama in NIZ:
    print(f"  -> {aciklama}")
    harcanan += uret(ad, vid, N_TEXT)
print("\n--- GAZALI (sıcak öğretmen) ---")
for ad, vid, aciklama in GAZ:
    print(f"  -> {aciklama}")
    harcanan += uret(ad, vid, G_TEXT)

sub = req("/v1/user/subscription")
print(f"\n===== OZET =====")
print(f"harcanan: ~{harcanan:,} kr")
print(f"kalan   : {sub['character_limit'] - sub['character_count']:,}")
