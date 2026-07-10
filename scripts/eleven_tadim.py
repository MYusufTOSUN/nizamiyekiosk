# ElevenLabs tadim: 3 ses x 3 ayar profili = 9 kisa ornek + dinleme sayfasi.
# API anahtari: ELEVEN_API_KEY (ortam ya da HKCU\Environment kayit defteri).
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent
OUT = ROOT / "senaryolar" / "tadim"
OUT.mkdir(parents=True, exist_ok=True)

def api_key() -> str:
    k = os.environ.get("ELEVEN_API_KEY")
    if k:
        return k
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
            return winreg.QueryValueEx(h, "ELEVEN_API_KEY")[0]
    except OSError:
        sys.exit("HATA: ELEVEN_API_KEY bulunamadi (setx sonrasi bu betik kayit defterinden de okur).")

KEY = api_key()
BASE = "https://api.elevenlabs.io"

def req(path: str, data: dict | None = None, raw: bool = False):
    r = urllib.request.Request(
        BASE + path,
        data=json.dumps(data).encode() if data else None,
        headers={"xi-api-key": KEY, "Content-Type": "application/json"},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(r, timeout=120) as resp:
        body = resp.read()
        return body if raw else json.loads(body)

# 1) Abonelik/kredi durumu
sub = req("/v1/user/subscription")
print(f"Plan: {sub.get('tier')} | kredi: {sub.get('character_count'):,}/{sub.get('character_limit'):,}")

# 2) Sesleri bul
ISTENEN = {
    "meliksah": "aykut",       # Aykut Akkasoglu
    "gazali": "murat",         # Murat Albayrak
    "nizamulmulk": "seyfullah" # Seyfullah Kartal
}
voices = req("/v1/voices")["voices"]
print("Kayitli sesler:", ", ".join(v["name"] for v in voices))
secim = {}
for karakter, anahtar in ISTENEN.items():
    hit = [v for v in voices if anahtar in v["name"].lower()]
    if not hit:
        sys.exit(f"HATA: '{anahtar}' adina uyan ses bulunamadi — My Voices'a eklendi mi?")
    secim[karakter] = (hit[0]["name"], hit[0]["voice_id"])
    print(f"  {karakter} -> {hit[0]['name']} ({hit[0]['voice_id'][:8]}...)")

# 3) Ornek metinler (her karakterin kendi int_1'i)
def intro(karakter: str) -> str:
    return (ROOT / "senaryolar" / "eleven" / f"{karakter}_int_1.txt").read_text(encoding="utf-8").strip()

PROFIL = {
    "A": {"stability": 0.50, "similarity_boost": 0.75, "style": 0.0,  "use_speaker_boost": True},
    "B": {"stability": 0.35, "similarity_boost": 0.75, "style": 0.15, "use_speaker_boost": True},
    "C": {"stability": 0.65, "similarity_boost": 0.80, "style": 0.0,  "use_speaker_boost": True},
}
ACIKLAMA = {"A": "Dengeli (standart)", "B": "Duygulu / hikaye anlatici", "C": "Agirbasli / sakin-tutarli"}

toplam = 0
for karakter, (ad, vid) in secim.items():
    metin = intro(karakter)
    for p, ayar in PROFIL.items():
        hedef = OUT / f"{karakter}_{p}.mp3"
        if hedef.exists():
            print(f"  atla (var): {hedef.name}")
            continue
        ses = req(
            f"/v1/text-to-speech/{vid}?output_format=mp3_44100_192",
            {"text": metin, "model_id": "eleven_multilingual_v2", "voice_settings": ayar},
            raw=True,
        )
        hedef.write_bytes(ses)
        toplam += len(metin)
        print(f"  uretildi: {hedef.name} ({len(ses)//1024} KB)")

# 4) Dinleme sayfasi
rows = []
for karakter, (ad, _) in secim.items():
    players = "".join(
        f"<div class='p'><b>{p}</b> — {ACIKLAMA[p]}<br><audio controls preload='none' src='{karakter}_{p}.mp3'></audio></div>"
        for p in PROFIL
    )
    rows.append(f"<section><h2>{karakter.upper()} <span>({ad})</span></h2>{players}</section>")
html = f"""<!doctype html><html lang='tr'><head><meta charset='utf-8'><title>Ses Tadimi</title>
<style>body{{background:#0b1020;color:#f3ead4;font-family:system-ui;max-width:760px;margin:2rem auto;padding:0 1rem}}
h1{{color:#e3b552}} h2{{color:#e3b552;border-bottom:1px solid #333;padding-bottom:4px}} h2 span{{color:#8a93ac;font-size:.8em}}
.p{{margin:10px 0;padding:10px;background:#131a2e;border-radius:10px}} audio{{width:100%;margin-top:6px}}</style></head>
<body><h1>ElevenLabs Ses Tadimi — 3 karakter x 3 profil</h1>
<p>Her karakter icin A/B/C dinle, begendigini soyle (or. "Gazali=B, Meliksah=A, Nizamulmulk=C").</p>
{''.join(rows)}</body></html>"""
(OUT / "tadim.html").write_text(html, encoding="utf-8")
print(f"\nBitti. Harcanan: ~{toplam:,} kredi | Dinleme sayfasi: {OUT / 'tadim.html'}")
