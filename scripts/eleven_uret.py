# ElevenLabs toplu uretim: senaryolar/eleven/*.txt -> sesler/<kod>.mp3
# Profil C (kullanici secimi): stability 0.65, similarity 0.80, style 0, boost on.
# Kaldigi yerden devam eder (var olan dosyayi atlar); 429/5xx'te bekleyip yeniden dener.
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC = ROOT / "senaryolar" / "eleven"
OUT = ROOT / "sesler"
OUT.mkdir(exist_ok=True)

def api_key() -> str:
    k = os.environ.get("ELEVEN_API_KEY")
    if k:
        return k
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
        return winreg.QueryValueEx(h, "ELEVEN_API_KEY")[0]

KEY = api_key()
BASE = "https://api.elevenlabs.io"

def req(path: str, data: dict | None = None, raw: bool = False, tries: int = 4):
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
            if e.code in (429, 500, 502, 503) and i < tries - 1:
                bekle = 15 * (i + 1)
                print(f"    {e.code} -> {bekle}sn bekle, tekrar...")
                time.sleep(bekle)
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if i < tries - 1:
                time.sleep(10 * (i + 1))
                continue
            raise

# Ses eslesmesi
ISTENEN = {"meliksah": "aykut", "gazali": "murat albayrak", "nizamulmulk": "seyfullah"}
voices = req("/v1/voices")["voices"]
VID = {}
for karakter, anahtar in ISTENEN.items():
    hit = [v for v in voices if anahtar in v["name"].lower()]
    if not hit:
        sys.exit(f"HATA: ses bulunamadi: {anahtar}")
    VID[karakter] = hit[0]["voice_id"]

AYAR = {"stability": 0.65, "similarity_boost": 0.80, "style": 0.0, "use_speaker_boost": True}

files = sorted(SRC.glob("*.txt"))
print(f"{len(files)} blok, profil C, cikti: {OUT}")
harcanan = 0
hata = []
for i, f in enumerate(files, 1):
    kod = f.stem
    karakter = kod.split("_")[0]
    hedef = OUT / f"{kod}.mp3"
    if hedef.exists() and hedef.stat().st_size > 20_000:
        print(f"[{i:2}/{len(files)}] atla (var): {kod}")
        continue
    metin = f.read_text(encoding="utf-8").strip()
    try:
        ses = req(
            f"/v1/text-to-speech/{VID[karakter]}?output_format=mp3_44100_192",
            {"text": metin, "model_id": "eleven_multilingual_v2", "voice_settings": AYAR},
            raw=True,
        )
        hedef.write_bytes(ses)
        harcanan += len(metin)
        print(f"[{i:2}/{len(files)}] OK: {kod} ({len(ses)//1024} KB)")
    except Exception as e:  # tek klip hatasi tum isi durdurmasin
        hata.append(kod)
        print(f"[{i:2}/{len(files)}] HATA: {kod} -> {e}")
    time.sleep(0.6)  # nazik hiz

# Dogrulama
eksik = [f.stem for f in files if not (OUT / f"{f.stem}.mp3").exists()]
kucuk = [f.stem for f in files if (OUT / f"{f.stem}.mp3").exists() and (OUT / f"{f.stem}.mp3").stat().st_size < 20_000]
sub = req("/v1/user/subscription")
print("\n===== OZET =====")
print(f"uretilen bu calismada: ~{harcanan:,} karakter")
print(f"kredi durumu: {sub.get('character_count'):,}/{sub.get('character_limit'):,}")
print(f"eksik: {eksik or 'YOK'}")
print(f"supheli-kucuk: {kucuk or 'YOK'}")
print(f"hatali: {hata or 'YOK'}")
print("TAMAM" if not (eksik or kucuk or hata) else "SORUNLU — yeniden calistir (kaldigi yerden devam eder)")
