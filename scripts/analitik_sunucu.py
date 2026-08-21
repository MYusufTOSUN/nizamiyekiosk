# -*- coding: utf-8 -*-
"""
Analitik toplayici + kiosk statik sunucusu.  Tek surec, sifir ek bagimlilik.

  python scripts\\analitik_sunucu.py                 # 8777, kiosk/ servis eder
  python scripts\\analitik_sunucu.py --port 8777 --kok kiosk
  python scripts\\analitik_sunucu.py --sadece-toplayici   # statik servis yok

NEDEN AYRI BIR SUNUCU
  http.server yalnizca GET yapar; olay yazmak icin POST gerekiyor. Bu sunucu
  kiosk dosyalarini AYNI adresten servis etmeye devam eder, ustune /a/* altinda
  toplayici uclarini acar. kiosk_baslat.ps1 http.server yerine bunu cagirir.

VERI  analitik/olaylar/YYYY-MM-DD.jsonl   (satir basina bir olay, EKLEME-TABANLI)
  Dosya asla ustune yazilmaz, silinmez. Oturum/tarayici profili degisse de kalir.
  Gun bazli dosya: rapor filtreleri gun gun calisabilsin ve dosya buyumesin diye.

UCLAR
  POST /a/olay      olay yaz (tek ya da toplu). CORS acik - web sitesi de yazar.
  GET  /a/ozet      filtreli toplam (JSON)
  GET  /a/ham       filtreli ham olaylar (JSON, en fazla 5000)
  GET  /a/panel     canli pano (HTML)
  GET  /a/saglik    ayakta mi
  digerleri         --kok altindan statik dosya
"""
import argparse
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

KOK = Path(__file__).resolve().parent.parent
VERI = KOK / "analitik" / "olaylar"
KILIT = threading.Lock()

# Olay adi ve alan adlari serbest metin degil; yanlis yazim raporu bozar.
# Bilinmeyen olay yine de YAZILIR (veri kaybetmeyelim) ama panoda "diger"e duser.
BILINEN = {
    # --- kiosk ---
    "kiosk_acildi", "oturum_basladi", "oturum_bitti",
    "karakter_secildi", "soru_goruldu", "soru_secildi",
    "klip_basladi", "klip_bitti", "klip_atlandi",
    "qr_gosterildi", "tesekkur_goruldu",
    "terk", "bosta_donus", "admin_acildi",
    # --- web ---
    "sayfa_acildi", "bolum_goruldu", "bolum_suresi",
    "duzenek_kullanildi", "dil_degisti", "bag_tiklandi",
    "kaydirma_derinligi", "sayfa_kapandi",
    # --- QR koprusu ---
    "qr_ile_geldi",
}

MAX_GOVDE = 512 * 1024      # tek istekte en fazla 512 KB
MAX_OLAY = 200              # tek istekte en fazla 200 olay


def simdi_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def gun_dosyasi(t: str) -> Path:
    """Olayin KENDI zaman damgasina gore gun dosyasi (gec gelen tamponlar dogru gune duser)."""
    try:
        g = datetime.fromisoformat(t).astimezone().strftime("%Y-%m-%d")
    except Exception:
        g = datetime.now().astimezone().strftime("%Y-%m-%d")
    return VERI / f"{g}.jsonl"


def temizle(s, uzunluk=200):
    if s is None:
        return None
    s = str(s)
    return s[:uzunluk]


def olay_yaz(olaylar, uzak_ip: str) -> int:
    """Olaylari gun dosyalarina ekler. Donus: yazilan sayisi."""
    gruplar = {}
    for o in olaylar:
        if not isinstance(o, dict):
            continue
        t = temizle(o.get("t")) or simdi_iso()
        kayit = {
            "t": t,
            "kaynak": temizle(o.get("kaynak"), 16) or "bilinmiyor",
            "olay": temizle(o.get("olay"), 48) or "bilinmiyor",
            "ziyaretci": temizle(o.get("ziyaretci"), 40),
            "oturum": temizle(o.get("oturum"), 40),
            "dil": temizle(o.get("dil"), 8),
            "karakter": temizle(o.get("karakter"), 24),
            "kod": temizle(o.get("kod"), 48),
            "baslik": temizle(o.get("baslik"), 80),
            "sure_ms": o.get("sure_ms") if isinstance(o.get("sure_ms"), (int, float)) else None,
            "ek": o.get("ek") if isinstance(o.get("ek"), dict) else None,
        }
        # Gizlilik: IP saklamiyoruz, yalnizca yerel mi disaridan mi geldigi.
        kayit["yerel"] = uzak_ip.startswith("127.") or uzak_ip == "::1"
        gruplar.setdefault(gun_dosyasi(t), []).append(kayit)

    n = 0
    with KILIT:
        VERI.mkdir(parents=True, exist_ok=True)
        for dosya, liste in gruplar.items():
            with dosya.open("a", encoding="utf-8") as f:
                for k in liste:
                    f.write(json.dumps(k, ensure_ascii=False) + "\n")
                    n += 1
    return n


def olaylari_oku(bas=None, bit=None):
    """Gun dosyalarindan olaylari okur. bas/bit: 'YYYY-MM-DD'."""
    if not VERI.exists():
        return []
    cikti = []
    for p in sorted(VERI.glob("*.jsonl")):
        gun = p.stem
        if bas and gun < bas:
            continue
        if bit and gun > bit:
            continue
        try:
            for satir in p.read_text(encoding="utf-8").splitlines():
                if not satir.strip():
                    continue
                try:
                    cikti.append(json.loads(satir))
                except json.JSONDecodeError:
                    pass          # bozuk satir tum gunu dusurmesin
        except OSError:
            pass
    return cikti


def suz(olaylar, q):
    """Sorgu parametreleriyle filtreler. Bos parametre = filtre yok."""
    def al(ad):
        v = q.get(ad, [None])[0]
        return v or None

    kaynak, karakter, dil = al("kaynak"), al("karakter"), al("dil")
    olay, kod, baslik = al("olay"), al("kod"), al("baslik")
    out = []
    for o in olaylar:
        if kaynak and o.get("kaynak") != kaynak:
            continue
        if karakter and o.get("karakter") != karakter:
            continue
        if dil and o.get("dil") != dil:
            continue
        if olay and o.get("olay") != olay:
            continue
        if kod and o.get("kod") != kod:
            continue
        if baslik and o.get("baslik") != baslik:
            continue
        out.append(o)
    return out


def ozet(olaylar):
    """Panonun ve raporun ortak toplama mantigi."""
    from collections import Counter, defaultdict

    say = Counter(o["olay"] for o in olaylar)
    gun = Counter(o["t"][:10] for o in olaylar if o.get("t"))
    kaynak = Counter(o.get("kaynak") or "?" for o in olaylar)
    karakter = Counter(o["karakter"] for o in olaylar if o.get("karakter"))
    dil = Counter(o["dil"] for o in olaylar if o.get("dil"))

    sorular = Counter(o["kod"] for o in olaylar
                      if o["olay"] == "soru_secildi" and o.get("kod"))
    bolumler = Counter(o["baslik"] for o in olaylar
                       if o["olay"] == "bolum_goruldu" and o.get("baslik"))
    duzenekler = Counter(o["kod"] for o in olaylar
                         if o["olay"] == "duzenek_kullanildi" and o.get("kod"))

    # Bolum basina toplam okuma suresi
    bolum_sure = defaultdict(float)
    for o in olaylar:
        if o["olay"] == "bolum_suresi" and o.get("baslik") and o.get("sure_ms"):
            bolum_sure[o["baslik"]] += o["sure_ms"] / 1000.0

    # Klip tamamlanma orani
    basladi = Counter(o["kod"] for o in olaylar if o["olay"] == "klip_basladi" and o.get("kod"))
    bitti = Counter(o["kod"] for o in olaylar if o["olay"] == "klip_bitti" and o.get("kod"))
    tamamlanma = {}
    for k, b in basladi.items():
        if b:
            tamamlanma[k] = round(bitti.get(k, 0) / b * 100, 1)

    ziyaretci = len({o["ziyaretci"] for o in olaylar if o.get("ziyaretci")})
    oturum = len({o["oturum"] for o in olaylar if o.get("oturum")})

    # QR hunisi: kioskta gosterildi -> sitede acildi
    qr_gosterildi = say.get("qr_gosterildi", 0)
    qr_geldi = say.get("qr_ile_geldi", 0)

    return {
        "toplam_olay": len(olaylar),
        "tekil_ziyaretci": ziyaretci,
        "oturum": oturum,
        "olay_sayilari": dict(say.most_common()),
        "gun": dict(sorted(gun.items())),
        "kaynak": dict(kaynak),
        "karakter": dict(karakter.most_common()),
        "dil": dict(dil.most_common()),
        "sorular": dict(sorular.most_common()),
        "bolumler": dict(bolumler.most_common()),
        "duzenekler": dict(duzenekler.most_common()),
        "bolum_sure_sn": {k: round(v, 1) for k, v in
                          sorted(bolum_sure.items(), key=lambda x: -x[1])},
        "klip_tamamlanma_yuzde": dict(sorted(tamamlanma.items(), key=lambda x: -x[1])),
        "qr": {
            "gosterildi": qr_gosterildi,
            "siteye_geldi": qr_geldi,
            "donusum_yuzde": round(qr_geldi / qr_gosterildi * 100, 1) if qr_gosterildi else None,
        },
    }


class Isleyici(BaseHTTPRequestHandler):
    server_version = "NizamiyeAnalitik/1.0"
    kok_dizin = None
    sadece_toplayici = False

    # --- yardimcilar -------------------------------------------------------
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")

    def _json(self, veri, kod=200):
        govde = json.dumps(veri, ensure_ascii=False).encode("utf-8")
        self.send_response(kod)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(govde)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(govde)

    def _metin(self, s, tur="text/html; charset=utf-8", kod=200):
        govde = s.encode("utf-8")
        self.send_response(kod)
        self.send_header("Content-Type", tur)
        self.send_header("Content-Length", str(len(govde)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(govde)

    def log_message(self, bicim, *args):
        # Olay POST'lari her saniye gelir; konsolu bogmasin. Hatalar yine gorunur.
        if "/a/olay" in (args[0] if args else ""):
            return
        super().log_message(bicim, *args)

    # --- yollar ------------------------------------------------------------
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        yol = urlparse(self.path).path
        if yol != "/a/olay":
            self._json({"hata": "bilinmeyen uc"}, 404)
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_GOVDE:
                self._json({"hata": "govde boyutu"}, 413)
                return
            veri = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception as e:
            self._json({"hata": f"cozumlenemedi: {e}"}, 400)
            return

        olaylar = veri.get("olaylar") if isinstance(veri, dict) else veri
        if isinstance(olaylar, dict):
            olaylar = [olaylar]
        if not isinstance(olaylar, list):
            self._json({"hata": "olaylar listesi bekleniyor"}, 400)
            return
        olaylar = olaylar[:MAX_OLAY]

        # Ust duzey alanlar her olaya miras gecer (istemci tekrar gondermesin)
        if isinstance(veri, dict):
            for anahtar in ("kaynak", "ziyaretci", "oturum", "dil"):
                if veri.get(anahtar):
                    for o in olaylar:
                        o.setdefault(anahtar, veri[anahtar])

        try:
            yazilan = olay_yaz(olaylar, self.client_address[0])
        except Exception as e:
            self._json({"hata": f"yazilamadi: {e}"}, 500)
            return
        self._json({"tamam": True, "yazilan": yazilan})

    def do_GET(self):
        p = urlparse(self.path)
        yol, q = p.path, parse_qs(p.query)

        if yol == "/a/saglik":
            self._json({"tamam": True, "zaman": simdi_iso(),
                        "gun_dosyasi": len(list(VERI.glob("*.jsonl"))) if VERI.exists() else 0})
            return

        if yol in ("/a/ozet", "/a/ham"):
            bas = (q.get("bas") or [None])[0]
            bit = (q.get("bit") or [None])[0]
            olaylar = suz(olaylari_oku(bas, bit), q)
            if yol == "/a/ozet":
                self._json(ozet(olaylar))
            else:
                self._json({"sayi": len(olaylar), "olaylar": olaylar[-5000:]})
            return

        if yol == "/a/panel":
            pano = KOK / "scripts" / "analitik_panel.html"
            if pano.exists():
                self._metin(pano.read_text(encoding="utf-8"))
            else:
                self._metin("<h1>analitik_panel.html bulunamadi</h1>", kod=500)
            return

        if self.sadece_toplayici:
            self._json({"hata": "statik servis kapali"}, 404)
            return

        self._statik(yol)

    def _statik(self, yol):
        if yol.endswith("/"):
            yol += "index.html"
        # Dizin disina cikma denemelerini engelle
        hedef = (self.kok_dizin / yol.lstrip("/")).resolve()
        try:
            hedef.relative_to(self.kok_dizin)
        except ValueError:
            self._metin("403", tur="text/plain; charset=utf-8", kod=403)
            return
        if not hedef.is_file():
            self._metin("404 - " + yol, tur="text/plain; charset=utf-8", kod=404)
            return

        tur = {
            ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
            ".mp4": "video/mp4", ".mp3": "audio/mpeg", ".svg": "image/svg+xml",
            ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp",
            ".woff2": "font/woff2", ".ico": "image/x-icon",
        }.get(hedef.suffix.lower(), "application/octet-stream")

        boyut = hedef.stat().st_size
        aralik = self.headers.get("Range")
        # Video icin Range sart: tarayici atlama/aramada parca ister.
        if aralik and aralik.startswith("bytes="):
            try:
                bas_s, _, bit_s = aralik[6:].partition("-")
                bas = int(bas_s) if bas_s else 0
                bit = int(bit_s) if bit_s else boyut - 1
                bit = min(bit, boyut - 1)
                uzunluk = bit - bas + 1
                self.send_response(206)
                self.send_header("Content-Type", tur)
                self.send_header("Content-Range", f"bytes {bas}-{bit}/{boyut}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(uzunluk))
                self.end_headers()
                with hedef.open("rb") as f:
                    f.seek(bas)
                    kalan = uzunluk
                    while kalan > 0:
                        parca = f.read(min(1 << 20, kalan))
                        if not parca:
                            break
                        self.wfile.write(parca)
                        kalan -= len(parca)
                return
            except (ValueError, BrokenPipeError, ConnectionAbortedError):
                return

        self.send_response(200)
        self.send_header("Content-Type", tur)
        self.send_header("Content-Length", str(boyut))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        try:
            with hedef.open("rb") as f:
                while True:
                    parca = f.read(1 << 20)
                    if not parca:
                        break
                    self.wfile.write(parca)
        except (BrokenPipeError, ConnectionAbortedError):
            pass          # tarayici videoyu birakti; normal


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--kok", default="kiosk", help="statik servis edilecek dizin")
    ap.add_argument("--sadece-toplayici", action="store_true")
    # Sergi aginda ziyaretcilerin TELEFONU da yazabilsin diye 0.0.0.0 gerekir.
    # Varsayilan 127.0.0.1: kiosk tek basina calisirken disariya acilmasin.
    ap.add_argument("--dinle", default="127.0.0.1",
                    help="baglanti adresi (ag icin 0.0.0.0)")
    a = ap.parse_args()

    Isleyici.kok_dizin = (KOK / a.kok).resolve()
    Isleyici.sadece_toplayici = a.sadece_toplayici
    VERI.mkdir(parents=True, exist_ok=True)

    sunucu = ThreadingHTTPServer((a.dinle, a.port), Isleyici)
    sunucu.daemon_threads = True
    print(f"analitik + kiosk sunucusu : http://{a.dinle}:{a.port}")
    print(f"  statik kok  : {Isleyici.kok_dizin}")
    print(f"  olay deposu : {VERI}")
    print(f"  canli pano  : http://127.0.0.1:{a.port}/a/panel")
    try:
        sunucu.serve_forever()
    except KeyboardInterrupt:
        print("\nkapatiliyor")


if __name__ == "__main__":
    main()
