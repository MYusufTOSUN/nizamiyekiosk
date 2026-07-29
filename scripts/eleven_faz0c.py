# -*- coding: utf-8 -*-
"""
FAZ 0C — iki is:
  A) DURAKLAMA'nin gercek kaynagini izole et (noktalama mi, speaker_boost mu?)
  B) Gazali icin DERIN ses adaylari
Stability 0.60 sabit (kullanici karari).
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


def ayar(stab=0.60, boost=True):
    return {"stability": stab, "similarity_boost": 0.75, "style": 0.0,
            "use_speaker_boost": boost, "speed": 0.95}


def uret(ad, voice_id, metin, a=None, nxt=None):
    hedef = OUT / f"{ad}.mp3"
    if hedef.exists() and hedef.stat().st_size > 10_000:
        print(f"  atla (var): {ad}")
        return 0
    body = {"text": metin, "model_id": "eleven_multilingual_v2",
            "voice_settings": a or ayar(), "apply_text_normalization": "on", "seed": SEED}
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


SEYFULLAH = "mF7tIc9VLrznhGooGjaT"
harcanan = 0

# ============================================================
# A) DURAKLAMA AVI — supheliler: noktali virgul (;) ve kesme (')
#    Referans (T2D, zaten var): "...açtık; Nişabur'dan Basra'ya..."
# ============================================================
print("=== A) DURAKLAMA KAYNAGI IZOLASYONU ===")

# P1: noktali virgul -> virgul  (kesmeler duruyor)
P1 = ("Bin altmış yedide Bağdat'ta baş medreseyi açtık, Nişabur'dan Basra'ya bir ilim ağı kurduk.")
harcanan += uret("P1_virgul", SEYFULLAH, P1)

# P2: kesme isaretleri KALDIRILDI  (noktali virgul duruyor)
P2 = ("Bin altmış yedide Bağdatta baş medreseyi açtık; Nişaburdan Basraya bir ilim ağı kurduk.")
harcanan += uret("P2_kesmesiz", SEYFULLAH, P2)

# P3: IKISI DE duzeltildi
P3 = ("Bin altmış yedide Bağdatta baş medreseyi açtık, Nişaburdan Basraya bir ilim ağı kurduk.")
harcanan += uret("P3_ikisi_de", SEYFULLAH, P3)

# P4: ikisi de + speaker_boost KAPALI  (boost artefakt yapiyor olabilir)
harcanan += uret("P4_boost_kapali", SEYFULLAH, P3, a=ayar(boost=False))

# ============================================================
# B) GAZALI — DERIN SES ADAYLARI
#    Metin kesme isaretlerinden arindirildi (A testinin bulgusunu pesinen uygula)
# ============================================================
G = ("Esselamü aleyküm evlatlarım, hoş geldiniz. Ben Gazâlî. Tusta doğdum, Bağdatta "
     "Nizâmiye Medresesinde baş müderris oldum. Bana Hüccetül İslâm derler. "
     "Sorun bakalım, gönlünüzde ne varsa konuşalım.")
G_NEXT = "Ah, beni talebelik günlerime götürdün evladım. Nişaburda okurken sabah ezanıyla kalkardık."

DERIN = [
    ("D1_Emin_tarihi",   "j9K9HnBcmgA6xNWqjlX0", "Emin — 'Epic Historical Narrative', YASLI, derin (27bin klon)"),
    ("D2_ErgunKalbak",   "0DihkedLJYKoWg7H1u4d", "Ergun Kalbak — derin, sakin, net, YASLI (8bin)"),
    ("D3_Jayden_engin",  "slwjSpcKF6oy5Uc2pyud", "Jayden — 'Profound' engin/derin, bilgilendirici (32bin)"),
    ("D4_Adam_ciddi",    "J17lijyP1BHYcM7ld0Rg", "Adam — derin, profesyonel, ciddi (143bin klon — en populer)"),
    ("D5_EmreTimur",     "7jNcYFFK9Ch5Szj4siVk", "Emre Timur — derin ama YUMUSAK anlatici (25bin)"),
]

print("\n=== B) GAZALI ICIN DERIN SESLER ===")
for ad, vid, aciklama in DERIN:
    print(f"  -> {aciklama}")
    harcanan += uret(ad, vid, G, nxt=G_NEXT)

# Ahmet'i de kesmesiz metinle tekrar uret (kullanici begendi, adil kiyas)
harcanan += uret("D0_Ahmet_kesmesiz", "75SIZa3vvET95PHhf1yD", G, nxt=G_NEXT)

sub = req("/v1/user/subscription")
print("\n===== OZET =====")
print(f"harcanan: ~{harcanan:,} karakter")
print(f"kredi: {sub.get('character_count'):,}/{sub.get('character_limit'):,} "
      f"(kalan {sub.get('character_limit') - sub.get('character_count'):,})")
