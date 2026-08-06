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
import base64
import hashlib
import json
import mimetypes
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
SES = KOK / "kiosk" / "sesler"
VIDEO = KOK / "video"
# Kadrajli surumler: figur cercevenin %90'ini kapliyor (modele maksimum piksel),
# nihai kadraj post-prodüksiyonda kuruluyor. Ham surumler bir ust klasorde.
MASTER = VIDEO / "00_master" / "kadrajli"
HAM = VIDEO / "02_ham"
LOG = VIDEO / "_log"
MANIFEST = LOG / "manifest.jsonl"

# --------------------------------------------------------------------- AYARLAR
UC = "fal-ai/kling-video/ai-avatar/v2/standard"
SANIYE_UCRET = 0.0562                      # 6 Agustos 2026, fal model sayfasi
ESZAMANLI = 29                             # hesap limiti 30, bir tanesi pay
BAKIYE_ALT_SINIR = 80.0                    # bunun altinda parti baslatma
YOKLAMA_SN = 15                            # kuyruk durumu kac saniyede bir sorulacak
AZAMI_BEKLEME_SN = 60 * 45                 # tek klip icin ust sinir

# Model prompt alani varsayilan "." — pilotta once varsayilanla kosulacak.
PROMPT = "."

MASTERLAR = {
    "gazali": MASTER / "gazali_master.png",
    "nizamulmulk": MASTER / "nizamulmulk_master.png",
    "meliksah": MASTER / "meliksah_master.png",
}

# Kademeli taahhut. Sira: en uzun klipler once — cakacaksa orada cakar.
PARTILER = {"deneme": 3, "ilk": 30, "kalan": None}

_kilit = threading.Lock()
_dur = threading.Event()          # 4xx gelince tum parti durur


# ------------------------------------------------------------------ yardimcilar
def anahtar() -> str:
    k = os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY")
    if k:
        return k
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
            for ad in ("FAL_KEY", "FAL_API_KEY"):
                try:
                    return winreg.QueryValueEx(h, ad)[0]
                except FileNotFoundError:
                    continue
    except ImportError:
        pass
    sys.exit("HATA: FAL_KEY bulunamadi. Ortam degiskeni olarak ekleyin "
             "(setx FAL_KEY \"...\") ve terminali yeniden acin.")


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


def veri_uri(p: Path) -> str:
    tur = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return f"data:{tur};base64,{base64.b64encode(p.read_bytes()).decode()}"


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
    govde = {
        "image_url": veri_uri(MASTERLAR[kar]),
        "audio_url": veri_uri(SES / f"{kod}.mp3"),
        "prompt": PROMPT,
    }
    try:
        k = istek(f"https://queue.fal.run/{UC}", veri=govde, api=api, zaman_asimi=180)
    except urllib.error.HTTPError as e:
        govde_hata = e.read()[:400].decode(errors="replace")
        if 400 <= e.code < 500:
            _dur.set()          # 422 ucretlendirilebilir -> tum partiyi durdur
            return {"kod": kod, "durum": "hata_4xx", "kod_no": e.code, "mesaj": govde_hata}
        return {"kod": kod, "durum": "hata", "kod_no": e.code, "mesaj": govde_hata}

    istek_id = k.get("request_id")
    durum_url = k.get("status_url") or f"https://queue.fal.run/{UC}/requests/{istek_id}/status"
    cevap_url = k.get("response_url") or f"https://queue.fal.run/{UC}/requests/{istek_id}"

    # request_id'yi HEMEN yaz. Betik cokerse odenmis sonucu kaybetmeyelim:
    # kuyruk sonucu bir sure saklaniyor, id ile elle indirilebilir.
    manifest_yaz({"kod": kod, "durum": "gonderildi", "istek_id": istek_id,
                  "endpoint_id": UC, "cevap_url": cevap_url, "ses_sn": round(sn, 3)})
    print(f"       {kod:<24} gonderildi  id={istek_id}", flush=True)

    while True:
        if _dur.is_set():
            return {"kod": kod, "durum": "atlandi", "istek_id": istek_id}
        if time.time() - t0 > AZAMI_BEKLEME_SN:
            return {"kod": kod, "durum": "zaman_asimi", "istek_id": istek_id}
        time.sleep(YOKLAMA_SN)
        try:
            d = istek(durum_url, api=api, zaman_asimi=60)
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500 and e.code != 404:
                _dur.set()
                return {"kod": kod, "durum": "hata_4xx", "kod_no": e.code}
            continue
        except (urllib.error.URLError, TimeoutError):
            continue
        if d.get("status") == "COMPLETED":
            break

    sonuc = istek(cevap_url, api=api, zaman_asimi=120)
    url = (sonuc.get("video") or {}).get("url") if isinstance(sonuc.get("video"), dict) else None
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
        "endpoint_id": UC, "istek_id": istek_id, "prompt": PROMPT,
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
    a = ap.parse_args()

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
