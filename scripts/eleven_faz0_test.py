# -*- coding: utf-8 -*-
"""
FAZ 0 — ElevenLabs A/B kalite testi.
Mevcut 69 sese DOKUNMAZ; ciktilar kiosk/sesler/_test/ altina yazilir.

Test edilen degiskenler:
  1) Prozodi: stability + speed + request stitching (previous_text/next_text)
  2) Sayi telaffuzu: apply_text_normalization vs metni yaziyla yazma
  3) Ozel isim: duz yazim vs diakritik vs harf tekrari
  4) Ses secimi: Gazali icin mevcut ses vs narrator ses
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
SEED = 424242  # deterministik A/B


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
            detay = e.read().decode("utf-8", "replace")[:300]
            if e.code in (429, 500, 502, 503) and i < tries - 1:
                print(f"    {e.code} -> 12sn bekle, tekrar...")
                time.sleep(12)
                continue
            raise RuntimeError(f"HTTP {e.code}: {detay}")
    raise RuntimeError("tries bitti")


# --- sesler ---
voices = req("/v1/voices")["voices"]


def vid(anahtar):
    hit = [v for v in voices if anahtar in v["name"].lower()]
    if not hit:
        sys.exit(f"HATA: ses bulunamadi: {anahtar}")
    return hit[0]["voice_id"]


V_NIZAM = vid("seyfullah")        # Nizamulmulk
V_MELIK = vid("aykut")            # Meliksah
V_GAZALI = vid("murat albayrak")  # Gazali (mevcut, use_case=social_media)
V_ALT = vid("bilgehan")           # alternatif: narrator etiketli

# --- ayar profilleri ---
BASELINE = {"stability": 0.65, "similarity_boost": 0.80, "style": 0.0, "use_speaker_boost": True}


def recete(stab=0.50):
    return {"stability": stab, "similarity_boost": 0.75, "style": 0.0,
            "use_speaker_boost": True, "speed": 0.95}


def uret(ad, voice_id, metin, ayar, model="eleven_multilingual_v2",
         normalize=None, prev=None, nxt=None, seed=None):
    hedef = OUT / f"{ad}.mp3"
    if hedef.exists() and hedef.stat().st_size > 10_000:
        print(f"  atla (var): {ad}")
        return len(metin)
    body = {"text": metin, "model_id": model, "voice_settings": ayar}
    if normalize:
        body["apply_text_normalization"] = normalize
    if prev:
        body["previous_text"] = prev
    if nxt:
        body["next_text"] = nxt
    if seed is not None:
        body["seed"] = seed
    try:
        ses = req(f"/v1/text-to-speech/{voice_id}?output_format=mp3_44100_192", body, raw=True)
        hedef.write_bytes(ses)
        print(f"  OK: {ad}  ({len(ses)//1024} KB, {len(metin)} kr)")
        return len(metin)
    except Exception as e:
        print(f"  HATA: {ad} -> {e}")
        return 0


# ============================================================
# TEST 1 — PROZODI (Nizamulmulk int_2: 181 kr, sifir kesme/sayi)
# Kullanicinin "en berbat" dedigi klip; saf ses+ayar vakasi.
# ============================================================
T1 = ("Sana bir sorum var evladım. Bir devleti ayakta tutan nedir dersin, ordu mu, hazine mi? "
      "Ben ikisini de yönettim ama cevabım başkadır: adalet ve ilim. Sor bakalım, anlatayım.")
T1_NEXT = ("Sana bunun hikayesini anlatayım evladım. Bir gün Sultan Alparslan ile Nişabur'da "
           "bir caminin önünden geçiyorduk.")

print("\n=== TEST 1: PROZODI (Nizamulmulk) ===")
harcanan = 0
harcanan += uret("T1A_baseline", V_NIZAM, T1, BASELINE)
harcanan += uret("T1B_recete_stab050", V_NIZAM, T1, recete(0.50),
                 normalize="on", nxt=T1_NEXT, seed=SEED)
harcanan += uret("T1C_stab045", V_NIZAM, T1, recete(0.45),
                 normalize="on", nxt=T1_NEXT, seed=SEED)
harcanan += uret("T1D_stab060", V_NIZAM, T1, recete(0.60),
                 normalize="on", nxt=T1_NEXT, seed=SEED)
# v3: speed/stitching desteklemiyor; stability=1.0 => "Robust" mod
harcanan += uret("T1E_v3_robust", V_NIZAM, T1,
                 {"stability": 1.0, "similarity_boost": 0.75, "style": 0.0, "use_speaker_boost": True},
                 model="eleven_v3", normalize="on", seed=SEED)

# ============================================================
# TEST 2 — SAYI (1067)  : normalizasyon mu, metni yaziyla yazmak mi?
# ============================================================
T2_HAM = ("Sana bunun hikayesini anlatayım evladım. 1067'de Bağdat'ta baş medreseyi açtık; "
          "Nişabur'dan Basra'ya bir ilim ağı kurduk.")
T2_DUZ = ("Sana bunun hikayesini anlatayım evladım. Bin altmış yedide Bağdat'ta baş medreseyi açtık; "
          "Nişabur'dan Basra'ya bir ilim ağı kurduk.")

print("\n=== TEST 2: SAYI TELAFFUZU (Nizamulmulk) ===")
harcanan += uret("T2A_sayi_ham_baseline", V_NIZAM, T2_HAM, BASELINE)
harcanan += uret("T2B_sayi_ham_normalize", V_NIZAM, T2_HAM, recete(0.50),
                 normalize="on", seed=SEED)
harcanan += uret("T2C_sayi_yaziyla", V_NIZAM, T2_DUZ, recete(0.50),
                 normalize="on", seed=SEED)

# ============================================================
# TEST 3 — OZEL ISIM (Celali) : duz / diakritik / harf tekrari
# ============================================================
T3_A = "Sonunda Celali takvimi çıktı ortaya; öyle hassastı ki bugün bile hayranlık uyandırır."
T3_B = "Sonunda Celâlî takvimi çıktı ortaya; öyle hassastı ki bugün bile hayranlık uyandırır."
T3_C = "Sonunda Celaalii takvimi çıktı ortaya; öyle hassastı ki bugün bile hayranlık uyandırır."

print("\n=== TEST 3: OZEL ISIM TELAFFUZU (Meliksah) ===")
harcanan += uret("T3A_celali_duz", V_MELIK, T3_A, BASELINE)
harcanan += uret("T3B_celali_diakritik", V_MELIK, T3_B, recete(0.50), normalize="on", seed=SEED)
harcanan += uret("T3C_celali_harftekrari", V_MELIK, T3_C, recete(0.50), normalize="on", seed=SEED)

# ============================================================
# TEST 4 — GAZALI SESI (mevcut ses use_case=social_media)
# ============================================================
T4 = ("Esselamü aleyküm evlatlarım, hoş geldiniz. Ben Gazali. Tus'ta doğdum, Bağdat'ta "
      "Nizamiye Medresesi'nde baş müderris oldum. Bana Hüccetü'l-İslam derler. "
      "Sorun bakalım, gönlünüzde ne varsa konuşalım.")
T4_NEXT = "Ah, beni talebelik günlerime götürdün evladım. Nişabur'da okurken sabah ezanıyla kalkardık."

print("\n=== TEST 4: GAZALI SESI ===")
harcanan += uret("T4A_gazali_baseline", V_GAZALI, T4, BASELINE)
harcanan += uret("T4B_gazali_recete", V_GAZALI, T4, recete(0.50),
                 normalize="on", nxt=T4_NEXT, seed=SEED)
harcanan += uret("T4C_altses_bilgehan", V_ALT, T4, recete(0.50),
                 normalize="on", nxt=T4_NEXT, seed=SEED)

# --- ozet ---
sub = req("/v1/user/subscription")
print("\n===== OZET =====")
print(f"bu testte harcanan: ~{harcanan:,} karakter")
print(f"kredi: {sub.get('character_count'):,}/{sub.get('character_limit'):,} "
      f"(kalan {sub.get('character_limit') - sub.get('character_count'):,})")
print(f"cikti: {OUT}")
