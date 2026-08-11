# -*- coding: utf-8 -*-
"""
Nizamiye hologram videolarini fal.ai uzerinden uretir.

TASARIM ILKELERI (hepsi arastirmadan cikan somut gerekcelere dayaniyor):

  1. VARSAYILAN KURU CALISMA. Betik parayi ancak `--uret` bayragiyla harcar.
     Bayraksiz calistirildiginda ne yapacagini ve kaca mal olacagini yazar, hicbir
     istek atmaz.

  2. MANIFEST TABANLI, IDEMPOTENT. Her klip `video/_log/manifest.jsonl` icine
     yazilir. Durumu "tamam" olan klip icin ASLA yeni istek atilmaz. Kazara ikinci
     kosu = 162 dolar ikinci kez.

  3. KUYRUK API'SI, `run()` DEGIL. fal yalniz kuyruktaki isleri otomatik tekrar
     deniyor (10 denemeye kadar). Senkron `run()` hic tekrar denenmiyor.

  4. HERHANGI BIR 4xx'TE TUM PARTI DURUR. fal'in FAQ'su HTTP 422'nin
     ucretlendirilebilecegini soyluyor ("may still be charged if a runner spent GPU
     time"). Yanlis parametreyle 69 istek atmak hem basarisiz hem kismen faturali
     bir partidir.

  5. SONUC GELIR GELMEZ INDIRILIR + SHA256 MANIFESTE YAZILIR. fal'da uretilen
     medyanin varsayilan saklama suresi BELGELENMEMIS. Yaygin dolasan "7 gun"
     rakami kaynakta yok. Beklemeyiz.

  6. PARTI ONCESI BAKIYE KONTROLU. fal'da yerlesik butce tavani/alarm YOK.
     Istemci tarafi muhafiz zorunlu.

  7. KADEMELI TAAHHUT. 3 klip -> onay -> 30 -> onay -> kalan. Amac maliyet degil,
     yanlis parametreyi 3 klipte yakalamak.

Kullanim:
    python scripts/video_uret.py                       # kuru calisma, hepsi
    python scripts/video_uret.py --parti deneme        # yalniz 3 klip
    python scripts/video_uret.py --parti deneme --uret # GERCEKTEN URETIR
    python scripts/video_uret.py --durum               # manifest ozeti
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import fal_client

KOK = Path(__file__).resolve().parent.parent
SES = KOK / "kiosk" / "sesler"
VIDEO = KOK / "video"
# Kadrajli surumler: figur cercevenin %90'ini kapliyor (modele maksimum piksel),
# nihai kadraj post-prodüksiyonda kuruluyor. Ham surumler bir ust klasorde.
MASTER = VIDEO / "00_master" / "kadrajli" / "_v7"
HAM = VIDEO / "02_ham"
LOG = VIDEO / "_log"
MANIFEST = LOG / "manifest.jsonl"

# --------------------------------------------------------------------- AYARLAR
UC = "fal-ai/heygen/avatar4/image-to-video"
SANIYE_UCRET = 0.10                        # 7 Agustos 2026, fal fiyat API teyitli
ESZAMANLI = 29                             # hesap limiti 30, bir tanesi pay
BAKIYE_ALT_SINIR = 80.0                    # bunun altinda parti baslatma
YOKLAMA_SN = 15                            # kuyruk durumu kac saniyede bir sorulacak
AZAMI_BEKLEME_SN = 60 * 45                 # tek klip icin ust sinir

# ------------------------------------------------------------------ HEYGEN AYAR
# MODEL SECIMI: kullanici uc modeli ayni ses+gorselle yan yana dinledi ve HeyGen'i
# secti ("dudak senkronizasyonu mukemmel"). Olculen ustunlukler:
#   1440x1080 (Kling 1104x816 — %32 daha fazla dikey cozunurluk)
#   1,5 dk/klip (Kling 23,3 dk — 15 kat hizli)
#   sure farki +0,03 sn (Kling +1,07 sn)
#   oran tam 1,333 — master'in 4:3'unu korudu
#
# ALAN KARARLARI (semadan, her biri bilincli):
#   resolution   "1080p"  — secenekler 360p/480p/540p/720p/1080p, varsayilan 720p
#   aspect_ratio "auto"   — listede 4:3 YOK. 'auto' kaynak orani koruyor.
#   background   #000000  ** KRITIK ** varsayilan #FFFFFF yani BEYAZ. Bos birakilirsa
#                siyah zemin beyaza doner, Pepper's Ghost'ta felaket.
#   talking_style "stable" — "minimal movement". Vakur tarihi sahsiyet icin dogru;
#                'expressive' asiri jest uretip elleri kadraj disina tasiyabilir.
#   caption      False    — altyazi GOMULMESIN
#   prompt       GONDERILMIYOR — HeyGen'de prompt = avatarin SOYLEYECEGI METIN,
#                jest promptu degil (Kling'den yapisal fark). audio_url onu geciyor.
#   expression   GONDERILMIYOR — semada const "happy", tek gecerli deger o.
#                Vakur bir alim icin yanlis; bos birakmak notr veriyor.
#   voice        GONDERILMIYOR — audio_url geciyor.
# Klip bazli anlatim stili — KULLANICI TALIMATI: her klip kendi ses icerigine gore
# ayarlanir, tek tip ayar KULLANILMAZ. Siniflandirma senaryolar/eleven/*.txt
# metinlerinden: anlati ve soru sinyalleri hareketi artirir, liste ve nasihat
# sinyalleri sakinlik ister. Intro/outro daima expressive — ziyaretciyle
# dogrudan temas kurulan yerler.
KLIP_STILI = {
    "gazali_int_1": "expressive",
    "gazali_int_2": "expressive",
    "gazali_int_3": "expressive",
    "gazali_k1_s1_v1": "stable",
    "gazali_k1_s1_v2": "expressive",
    "gazali_k1_s1_v3": "expressive",
    "gazali_k1_s2": "stable",
    "gazali_k1_s3": "stable",
    "gazali_k1_s4": "stable",
    "gazali_k2_s1_v1": "stable",
    "gazali_k2_s1_v2": "expressive",
    "gazali_k2_s1_v3": "stable",
    "gazali_k2_s2": "expressive",
    "gazali_k2_s3": "expressive",
    "gazali_k2_s4": "stable",
    "gazali_k3_s1_v1": "stable",
    "gazali_k3_s1_v2": "expressive",
    "gazali_k3_s1_v3": "stable",
    "gazali_k3_s2": "stable",
    "gazali_k3_s3": "stable",
    "gazali_k3_s4": "stable",
    "gazali_out_1": "expressive",
    "gazali_out_2": "expressive",
    "meliksah_int_1": "expressive",
    "meliksah_int_2": "expressive",
    "meliksah_int_3": "expressive",
    "meliksah_k1_s1_v1": "expressive",
    "meliksah_k1_s1_v2": "stable",
    "meliksah_k1_s1_v3": "expressive",
    "meliksah_k1_s2": "stable",
    "meliksah_k1_s3": "expressive",
    "meliksah_k1_s4": "stable",
    "meliksah_k2_s1_v1": "expressive",
    "meliksah_k2_s1_v2": "expressive",
    "meliksah_k2_s1_v3": "stable",
    "meliksah_k2_s2": "stable",
    "meliksah_k2_s3": "stable",
    "meliksah_k2_s4": "stable",
    "meliksah_k3_s1_v1": "expressive",
    "meliksah_k3_s1_v2": "stable",
    "meliksah_k3_s1_v3": "expressive",
    "meliksah_k3_s2": "stable",
    "meliksah_k3_s3": "expressive",
    "meliksah_k3_s4": "stable",
    "meliksah_out_1": "expressive",
    "meliksah_out_2": "expressive",
    "nizamulmulk_int_1": "expressive",
    "nizamulmulk_int_2": "expressive",
    "nizamulmulk_int_3": "expressive",
    "nizamulmulk_k1_s1_v1": "expressive",
    "nizamulmulk_k1_s1_v2": "stable",
    "nizamulmulk_k1_s1_v3": "stable",
    "nizamulmulk_k1_s2": "stable",
    "nizamulmulk_k1_s3": "stable",
    "nizamulmulk_k1_s4": "expressive",
    "nizamulmulk_k2_s1_v1": "expressive",
    "nizamulmulk_k2_s1_v2": "stable",
    "nizamulmulk_k2_s1_v3": "stable",
    "nizamulmulk_k2_s2": "stable",
    "nizamulmulk_k2_s3": "stable",
    "nizamulmulk_k2_s4": "stable",
    "nizamulmulk_k3_s1_v1": "stable",
    "nizamulmulk_k3_s1_v2": "stable",
    "nizamulmulk_k3_s1_v3": "stable",
    "nizamulmulk_k3_s2": "stable",
    "nizamulmulk_k3_s3": "stable",
    "nizamulmulk_k3_s4": "stable",
    "nizamulmulk_out_1": "expressive",
    "nizamulmulk_out_2": "expressive",
}

# Klipten BAGIMSIZ sabitler
HEYGEN_AYAR = {
    "resolution": "1080p",
    "aspect_ratio": "auto",
    "background": {"type": "color", "value": "#000000"},
    "caption": False,
}     # olcum klipleri icin; karsilastirma tabani

MASTERLAR = {
    "gazali": MASTER / "gazali_master.png",
    "nizamulmulk": MASTER / "nizamulmulk_master.png",
    "meliksah": MASTER / "meliksah_master.png",
}

# Kademeli taahhut. Sira: en uzun klipler once — cakacaksa orada cakar.
PARTILER = {"deneme": 3, "ilk": 30, "kalan": None}

_duz_prompt = False          # --duz-prompt ile "." kullanilir (karsilastirma icin)
_kilit = threading.Lock()
_dur = threading.Event()          # 4xx gelince tum parti durur


# ------------------------------------------------------------------ yardimcilar
def anahtar() -> str:
    """Anahtari bulur ve os.environ'a KOYAR — fal_client oradan okuyor."""
    k = os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY")
    if not k:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
                for ad in ("FAL_KEY", "FAL_API_KEY"):
                    try:
                        k = winreg.QueryValueEx(h, ad)[0]
                        break
                    except FileNotFoundError:
                        continue
        except ImportError:
            pass
    if not k:
        sys.exit("HATA: FAL_KEY bulunamadi. Ortam degiskeni olarak ekleyin "
                 "(setx FAL_KEY \"...\") ve terminali yeniden acin.")
    os.environ["FAL_KEY"] = k
    return k


def sure(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    return float(r.stdout.strip())


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for blok in iter(lambda: f.read(1 << 20), b""):
            h.update(blok)
    return h.hexdigest()


_yukleme_onbellek: dict[str, str] = {}


def yukle(p: Path) -> str:
    """Dosyayi fal storage'a yukler, URL doner. Yukleme UCRETSIZ.

    NEDEN data URI DEGIL: ilk denemede gorsel+ses base64 olarak govdeye gomulmustu
    (3,78 MB). Istek fal'a hic ulasmadi — TCP `SynSent` durumunda 16 dakika asili
    kaldi, dashboard'da kayit olusmadi, ucret islemedi. Ayni dosyalar fal storage'a
    3,1 ve 2,1 saniyede sorunsuz yuklendi. Belgelenmis yol budur.

    Onbellek onemli: master gorsel bir karakterin 23 klibinde de ayni — 23 kez
    yuklemenin anlami yok.
    """
    a = str(p.resolve())
    if a not in _yukleme_onbellek:
        _yukleme_onbellek[a] = fal_client.upload_file(a)
    return _yukleme_onbellek[a]


def istek(url: str, *, veri: dict | None = None, api: str, zaman_asimi: int = 120):
    govde = json.dumps(veri).encode() if veri is not None else None
    basliklar = {"Authorization": f"Key {api}"}
    if govde:
        basliklar["Content-Type"] = "application/json"
        # Uretilen medya suresiz saklansin — varsayilan sure belgelenmemis.
        basliklar["X-Fal-Object-Lifecycle-Preference"] = json.dumps(
            {"expiration_duration_seconds": None})
    r = urllib.request.Request(url, data=govde, headers=basliklar)
    with urllib.request.urlopen(r, timeout=zaman_asimi) as y:
        return json.loads(y.read())


def iptal(istek_id: str, api: str) -> bool:
    """fal'da istek iptali POST ile oluyor. PUT 405 donuyor — ilk denemede
       Kling isini bu yuzden durduramamistik."""
    u = f"https://queue.fal.run/{UC}/requests/{istek_id}/cancel"
    try:
        r = urllib.request.Request(u, method="POST",
                                   headers={"Authorization": f"Key {api}"})
        with urllib.request.urlopen(r, timeout=30):
            return True
    except Exception:
        return False


def manifest_oku() -> dict[str, dict]:
    """Son kayit kazanir; 'tamam' olan bir kaydi daha eski bir durum EZEMEZ."""
    if not MANIFEST.exists():
        return {}
    kayit: dict[str, dict] = {}
    for satir in MANIFEST.read_text(encoding="utf-8").splitlines():
        if not satir.strip():
            continue
        try:
            k = json.loads(satir)
        except json.JSONDecodeError:
            continue
        onceki = kayit.get(k["kod"])
        if onceki and onceki.get("durum") == "tamam" and k.get("durum") != "tamam":
            continue
        kayit[k["kod"]] = k
    return kayit


def manifest_yaz(kayit: dict) -> None:
    LOG.mkdir(parents=True, exist_ok=True)
    with _kilit, MANIFEST.open("a", encoding="utf-8") as f:
        f.write(json.dumps(kayit, ensure_ascii=False) + "\n")


def bakiye(api: str) -> float | None:
    """Admin anahtari gerektirir; normal anahtarla None doner (olumcul degil)."""
    try:
        d = istek("https://api.fal.ai/v1/account/billing?expand=credits", api=api, zaman_asimi=30)
        for alan in ("credits", "balance", "available_credits"):
            if isinstance(d.get(alan), (int, float)):
                return float(d[alan])
        if isinstance(d.get("credits"), dict):
            for alan in ("balance", "available", "amount"):
                if isinstance(d["credits"].get(alan), (int, float)):
                    return float(d["credits"][alan])
    except Exception:
        return None
    return None


# --------------------------------------------------------------------- is akisi
def klipler() -> list[tuple[str, str, float]]:
    """(kod, karakter, saniye) — uzundan kisaya. Cakacaksa en uzunda cakar."""
    out = []
    for p in sorted(SES.glob("*.mp3")):
        kod = p.stem
        kar = kod.split("_")[0]
        if kar not in MASTERLAR:
            continue
        out.append((kod, kar, sure(p)))
    return sorted(out, key=lambda x: -x[2])


def uret_bir(kod: str, kar: str, sn: float, api: str) -> dict:
    if _dur.is_set():
        return {"kod": kod, "durum": "atlandi", "sebep": "parti durduruldu"}
    t0 = time.time()
    try:
        govde = {
            "image_url": yukle(MASTERLAR[kar]),
            "audio_url": yukle(SES / f"{kod}.mp3"),
            **HEYGEN_AYAR,
            # 7 Agu 2026 OLCUM: 'expressive' legacy-only bir secenek ve
            # aspect_ratio="auto" ile 422 veriyor ("requires the v3-compatible
            # path"). Listede 4:3 olmadigi icin 'auto' bizim tek dogru
            # secenegimiz -> 69 klibin hepsi 'stable'. KLIP_STILI asagida
            # niyet kaydi olarak duruyor, kullanilmiyor.
            # Ayrica olculdu: stable auto(v3) ve 5:4(legacy) yollarinda birebir
            # ayni sonucu veriyor (1.79 / 1.79), expressive ise +%17 hareket.
            "talking_style": "stable",
        }
        tutamak = fal_client.submit(UC, arguments=govde)
        istek_id = tutamak.request_id
    except Exception as e:
        mesaj = f"{type(e).__name__}: {e}"[:400]
        if any(x in mesaj for x in ("422", "400", "401", "403", "404")):
            _dur.set()          # 422 ucretlendirilebilir -> tum partiyi durdur
            return {"kod": kod, "durum": "hata_4xx", "mesaj": mesaj}
        return {"kod": kod, "durum": "gonderilemedi", "mesaj": mesaj}

    # request_id'yi HEMEN yaz. Betik cokerse odenmis sonucu kaybetmeyelim.
    manifest_yaz({"kod": kod, "durum": "gonderildi", "istek_id": istek_id,
                  "endpoint_id": UC, "ses_sn": round(sn, 3)})
    print(f"       {kod:<24} gonderildi  id={istek_id}", flush=True)

    while True:
        if _dur.is_set():
            return {"kod": kod, "durum": "atlandi", "istek_id": istek_id}
        if time.time() - t0 > AZAMI_BEKLEME_SN:
            return {"kod": kod, "durum": "zaman_asimi", "istek_id": istek_id}
        time.sleep(YOKLAMA_SN)
        try:
            d = fal_client.status(UC, istek_id)
        except Exception:
            continue
        if type(d).__name__ == "Completed":
            break

    try:
        sonuc = fal_client.result(UC, istek_id)
    except Exception as e:
        return {"kod": kod, "durum": "sonuc_alinamadi", "istek_id": istek_id,
                "mesaj": f"{type(e).__name__}: {e}"[:300]}
    v = sonuc.get("video")
    url = v.get("url") if isinstance(v, dict) else (v if isinstance(v, str) else None)
    if not url:
        return {"kod": kod, "durum": "cikti_yok", "istek_id": istek_id,
                "ham": json.dumps(sonuc)[:400]}

    HAM.mkdir(parents=True, exist_ok=True)
    hedef = HAM / f"{kod}__ham.mp4"
    with urllib.request.urlopen(url, timeout=900) as y, hedef.open("wb") as f:
        while blok := y.read(1 << 20):
            f.write(blok)

    return {
        "kod": kod, "durum": "tamam", "karakter": kar,
        "endpoint_id": UC, "istek_id": istek_id, "ayar": HEYGEN_AYAR,
        "ses_sn": round(sn, 3), "ses_sha256": sha256(SES / f"{kod}.mp3"),
        "gorsel_sha256": sha256(MASTERLAR[kar]),
        "video_sn": round(sure(hedef), 3), "video_sha256": sha256(hedef),
        "boyut_bayt": hedef.stat().st_size,
        "maliyet_usd": round(sn * SANIYE_UCRET, 4),
        "gecen_sn": round(time.time() - t0, 1),
        "dosya": str(hedef.relative_to(KOK)),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parti", choices=list(PARTILER), default="kalan")
    ap.add_argument("--klip", help="tek klip uret (olcum icin). Ornek: gazali_k2_s1_v3")
    ap.add_argument("--uret", action="store_true", help="GERCEKTEN uretir ve para harcar")
    ap.add_argument("--durum", action="store_true", help="manifest ozeti yazip cikar")
    ap.add_argument("--duz-prompt", action="store_true",
                    help="prompt yerine '.' gonder (A/B karsilastirmasi icin)")
    a = ap.parse_args()
    global _duz_prompt
    _duz_prompt = a.duz_prompt

    kayit = manifest_oku()
    bitmis = {k for k, v in kayit.items() if v.get("durum") == "tamam"}

    if a.durum:
        toplam = sum(v.get("maliyet_usd", 0) for v in kayit.values() if v.get("durum") == "tamam")
        sureler = [v["gecen_sn"] for v in kayit.values() if v.get("durum") == "tamam"]
        print(f"manifest: {MANIFEST}")
        print(f"  tamam        : {len(bitmis)}")
        print(f"  harcanan     : ${toplam:,.2f}")
        if sureler:
            print(f"  klip suresi  : ort {sum(sureler)/len(sureler)/60:.1f} dk · "
                  f"min {min(sureler)/60:.1f} · max {max(sureler)/60:.1f}")
        for k, v in sorted(kayit.items()):
            if v.get("durum") != "tamam":
                print(f"  ! {k:<24} {v.get('durum')} {v.get('mesaj','')[:80]}")
        return

    eksik_master = [f"{k} -> {p}" for k, p in MASTERLAR.items() if not p.exists()]
    if eksik_master:
        sys.exit("HATA: master gorsel yok:\n  " + "\n  ".join(eksik_master))

    hepsi = [(k, c, s) for k, c, s in klipler() if k not in bitmis]
    if a.klip:
        secili = [t for t in klipler() if t[0] == a.klip]
        if not secili:
            sys.exit(f"HATA: '{a.klip}' bulunamadi. kiosk/sesler/ altinda .mp3 olmali.")
    elif a.parti == "deneme":
        # Deneme partisi UC MASTER'I DA sinamali. Karakter basina en uzun klip:
        # hem en zor sure kosulu hem her gorselin ayri ayri kontrolu tek partide.
        secili = [next((t for t in hepsi if t[1] == kar), None) for kar in MASTERLAR]
        secili = [t for t in secili if t]
    else:
        n = PARTILER[a.parti]
        secili = hepsi if n is None else hepsi[:n]

    if not secili:
        print("yapacak is yok — hepsi manifestte 'tamam'.")
        return

    top_sn = sum(s for _, _, s in secili)
    tahmini = top_sn * SANIYE_UCRET
    dalga = -(-len(secili) // ESZAMANLI)

    print(f"uc nokta     : {UC}")
    print(f"parti        : {a.parti}  ({len(secili)} klip, {len(bitmis)} zaten bitmis)")
    print(f"toplam sure  : {top_sn/60:.1f} dk")
    print(f"tahmini bedel: ${tahmini:,.2f}   (${SANIYE_UCRET}/sn)")
    print(f"eszamanlilik : {ESZAMANLI}  ->  {dalga} dalga")
    print(f"cikti        : {HAM}")
    print()
    for k, c, s in secili[:5]:
        print(f"  {k:<24} {c:<12} {s:>5.1f} sn  ${s*SANIYE_UCRET:.2f}")
    if len(secili) > 5:
        print(f"  ... +{len(secili)-5} klip daha")

    if not a.uret:
        print("\nKURU CALISMA — hicbir istek atilmadi, hicbir para harcanmadi.")
        print("Gercekten uretmek icin:  --uret")
        return

    api = anahtar()
    b = bakiye(api)
    if b is None:
        print("\nUYARI: bakiye okunamadi (admin anahtari gerekiyor). Devam ediliyor.")
    elif b < max(BAKIYE_ALT_SINIR, tahmini * 1.2):
        sys.exit(f"HATA: bakiye ${b:,.2f}, bu parti icin yetersiz (${tahmini:,.2f} + pay).")
    else:
        print(f"\nbakiye: ${b:,.2f}")

    print(f"\nURETIM BASLIYOR — {len(secili)} klip\n" + "-" * 62)
    t0 = time.time()
    ok = hata = 0
    with ThreadPoolExecutor(max_workers=ESZAMANLI) as havuz:
        isler = {havuz.submit(uret_bir, k, c, s, api): k for k, c, s in secili}
        for f in as_completed(isler):
            r = f.result()
            manifest_yaz(r)
            if r["durum"] == "tamam":
                ok += 1
                print(f"  [{ok+hata:>2}/{len(secili)}] {r['kod']:<24} "
                      f"{r['video_sn']:>5.1f} sn  {r['gecen_sn']/60:>4.1f} dk  "
                      f"${r['maliyet_usd']:.2f}  OK")
            else:
                hata += 1
                print(f"  [{ok+hata:>2}/{len(secili)}] {r['kod']:<24} !! {r['durum']} "
                      f"{str(r.get('mesaj',''))[:90]}")
            sys.stdout.flush()

    son = manifest_oku()
    harcanan = sum(v.get("maliyet_usd", 0) for v in son.values() if v.get("durum") == "tamam")
    print("-" * 62)
    print(f"basarili {ok} · hatali {hata} · gecen {(time.time()-t0)/60:.1f} dk")
    print(f"harcanan (manifestteki tum tamam klipler): ${harcanan:,.2f}")
    if _dur.is_set():
        print("\n!! PARTI 4xx YUZUNDEN DURDURULDU. Parametreleri kontrol edin — "
              "422 ucretlendirilebilir. Duzeltip yeniden calistirin; biten klipler "
              "manifest sayesinde tekrar uretilmez.")
        sys.exit(1)


if __name__ == "__main__":
    main()
