# -*- coding: utf-8 -*-
"""
Analitik raporu — dosyadan okur, sunucuya IHTIYAC DUYMAZ.

  python scripts\\analitik_rapor.py                      # tum zamanlar
  python scripts\\analitik_rapor.py --bugun
  python scripts\\analitik_rapor.py --bas 2026-08-20 --bit 2026-08-20
  python scripts\\analitik_rapor.py --kaynak kiosk --karakter meliksah
  python scripts\\analitik_rapor.py --html rapor.html    # tek dosya HTML cikti
  python scripts\\analitik_rapor.py --json rapor.json

Toplayici kapali olsa bile calisir: veri diskte, JSONL olarak durur.
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK / "scripts"))
from analitik_sunucu import olaylari_oku, ozet          # noqa: E402  (ayni mantik, tek kaynak)


def cizgi(baslik):
    print("\n" + baslik)
    print("-" * max(30, len(baslik)))


def tablo(baslik, nesne, birim="", ust=15):
    if not nesne:
        return
    cizgi(baslik)
    g = list(nesne.items())[:ust]
    enb = max((v for _, v in g if isinstance(v, (int, float))), default=1) or 1
    for k, v in g:
        if isinstance(v, (int, float)):
            bar = "#" * int(v / enb * 28)
            print(f"  {str(k)[:38]:<38} {v:>9,.0f}{birim}  {bar}")
        else:
            print(f"  {str(k)[:38]:<38} {v}")


def ek_olcumler(olaylar):
    """Ozette olmayan, rapora ozel derinlestirmeler."""
    o = {}

    # Terk noktalari
    terk = Counter()
    for e in olaylar:
        if e["olay"] == "terk" and isinstance(e.get("ek"), dict):
            terk[e["ek"].get("ekran") or "?"] += 1
    o["terk_ekrani"] = dict(terk.most_common())

    # Oturum basina sorulan soru
    soru_oturum = Counter()
    for e in olaylar:
        if e["olay"] == "soru_secildi" and e.get("oturum"):
            soru_oturum[e["oturum"]] += 1
    if soru_oturum:
        dag = Counter(soru_oturum.values())
        o["oturumda_soru_sayisi"] = {f"{k} soru": v for k, v in sorted(dag.items())}
        o["oturum_basina_ortalama_soru"] = round(
            sum(soru_oturum.values()) / len(soru_oturum), 2)

    # Tamamlanan / yarim kalan oturum
    bitti = [e for e in olaylar if e["olay"] == "oturum_bitti"]
    tam = sum(1 for e in bitti if isinstance(e.get("ek"), dict) and e["ek"].get("tamamlandi"))
    if bitti:
        o["oturum_tamamlanma"] = {
            "tamamlanan": tam, "yarim": len(bitti) - tam,
            "tamamlanma_yuzde": round(tam / len(bitti) * 100, 1),
        }

    # Ortalama oturum suresi
    sureler = [e["sure_ms"] for e in bitti if e.get("sure_ms")]
    if sureler:
        sureler.sort()
        o["oturum_suresi_sn"] = {
            "ortalama": round(sum(sureler) / len(sureler) / 1000, 1),
            "medyan": round(sureler[len(sureler) // 2] / 1000, 1),
            "en_uzun": round(sureler[-1] / 1000, 1),
        }

    # Bekci ile zorla bitirilen klipler (dosya suresi asilmis = sorun isareti)
    bekci = Counter()
    for e in olaylar:
        if e["olay"] in ("klip_bitti", "klip_atlandi") and isinstance(e.get("ek"), dict):
            if e["ek"].get("bekci"):
                bekci[e.get("kod") or "?"] += 1
    o["bekciyle_biten_klip"] = dict(bekci.most_common())

    # Saat dagilimi (yogunluk)
    saat = Counter()
    for e in olaylar:
        try:
            saat[datetime.fromisoformat(e["t"]).strftime("%H")] += 1
        except Exception:
            pass
    o["saat_dagilimi"] = {f"{k}:00": v for k, v in sorted(saat.items())}
    return o


def html_yaz(yol, oz, ek, filtre):
    p = Path(yol)

    def blok(baslik, nesne, birim=""):
        if not nesne:
            return ""
        satir = "".join(
            f"<tr><td>{k}</td><td class='s'>{v}{birim}</td></tr>"
            for k, v in list(nesne.items())[:40])
        return f"<div class='k'><h2>{baslik}</h2><table>{satir}</table></div>"

    p.write_text(f"""<!doctype html><html lang="tr"><head><meta charset="utf-8">
<title>Nizamiye analitik raporu</title><style>
body{{margin:0;background:#0a0e1a;color:#e6ebf5;font:14px/1.5 "Segoe UI",system-ui,sans-serif;padding:20px}}
h1{{font-size:19px;margin:0 0 4px}} .f{{color:#8a97ad;font-size:13px;margin-bottom:18px}}
.g{{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));max-width:1500px}}
.k{{background:#121826;border:1px solid #1f2a3d;border-radius:9px;padding:13px 15px}}
h2{{font-size:12px;color:#8a97ad;text-transform:uppercase;letter-spacing:.07em;margin:0 0 8px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
td{{padding:4px 6px;border-bottom:1px solid #18202f}}
td.s{{text-align:end;font-variant-numeric:tabular-nums;color:#c8a24a}}
</style></head><body>
<h1>Nizamiye — analitik raporu</h1>
<div class="f">{filtre} · üretim {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
<div class="g">
{blok("Özet", {"tekil ziyaretçi": oz["tekil_ziyaretci"], "oturum": oz["oturum"],
               "toplam olay": oz["toplam_olay"]})}
{blok("QR hunisi", oz.get("qr"))}
{blok("Gün gün", oz.get("gun"))}
{blok("Sorular", oz.get("sorular"))}
{blok("Karakterler", oz.get("karakter"))}
{blok("Web başlıkları", oz.get("bolumler"))}
{blok("Okuma süresi (sn)", oz.get("bolum_sure_sn"))}
{blok("Düzenekler", oz.get("duzenekler"))}
{blok("Klip tamamlanma (%)", oz.get("klip_tamamlanma_yuzde"))}
{blok("Diller", oz.get("dil"))}
{blok("Terk edilen ekran", ek.get("terk_ekrani"))}
{blok("Oturumda soru sayısı", ek.get("oturumda_soru_sayisi"))}
{blok("Oturum süresi (sn)", ek.get("oturum_suresi_sn"))}
{blok("Saat dağılımı", ek.get("saat_dagilimi"))}
{blok("Tüm olaylar", oz.get("olay_sayilari"))}
</div></body></html>""", encoding="utf-8")
    print(f"\nHTML rapor: {p.resolve()}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bas"), ap.add_argument("--bit")
    ap.add_argument("--bugun", action="store_true")
    ap.add_argument("--kaynak"), ap.add_argument("--karakter")
    ap.add_argument("--dil"), ap.add_argument("--baslik"), ap.add_argument("--olay")
    ap.add_argument("--html"), ap.add_argument("--json")
    a = ap.parse_args()

    if a.bugun:
        a.bas = a.bit = datetime.now().strftime("%Y-%m-%d")

    olaylar = olaylari_oku(a.bas, a.bit)
    for alan, deger in (("kaynak", a.kaynak), ("karakter", a.karakter),
                        ("dil", a.dil), ("baslik", a.baslik), ("olay", a.olay)):
        if deger:
            olaylar = [o for o in olaylar if o.get(alan) == deger]

    filtre = " · ".join(f"{k}={v}" for k, v in
                        (("bas", a.bas), ("bit", a.bit), ("kaynak", a.kaynak),
                         ("karakter", a.karakter), ("dil", a.dil),
                         ("baslik", a.baslik), ("olay", a.olay)) if v) or "filtre yok"

    if not olaylar:
        print(f"Olay bulunamadi ({filtre}).")
        print(f"Veri dizini: {KOK / 'analitik' / 'olaylar'}")
        return

    oz = ozet(olaylar)
    ek = ek_olcumler(olaylar)

    print("=" * 60)
    print(f"NIZAMIYE ANALITIK RAPORU   ({filtre})")
    print("=" * 60)
    print(f"  toplam olay      : {oz['toplam_olay']:,}")
    print(f"  tekil ziyaretci  : {oz['tekil_ziyaretci']:,}")
    print(f"  oturum           : {oz['oturum']:,}")
    if ek.get("oturum_basina_ortalama_soru"):
        print(f"  oturum basi soru : {ek['oturum_basina_ortalama_soru']}")
    if ek.get("oturum_tamamlanma"):
        t = ek["oturum_tamamlanma"]
        print(f"  tamamlanma       : %{t['tamamlanma_yuzde']} "
              f"({t['tamamlanan']} tam / {t['yarim']} yarim)")
    if ek.get("oturum_suresi_sn"):
        s = ek["oturum_suresi_sn"]
        print(f"  oturum suresi    : ort {s['ortalama']} sn · medyan {s['medyan']} sn")

    q = oz["qr"]
    cizgi("QR HUNISI")
    print(f"  kioskta gosterildi : {q['gosterildi']:,}")
    print(f"  siteye geldi       : {q['siteye_geldi']:,}")
    print(f"  donusum            : "
          f"{'%' + str(q['donusum_yuzde']) if q['donusum_yuzde'] is not None else '-'}")

    tablo("GUN GUN", oz["gun"])
    tablo("EN COK SORULAN SORULAR", oz["sorular"])
    tablo("KARAKTERLER", oz["karakter"])
    tablo("WEB BASLIKLARI - GORUNTULENME", oz["bolumler"])
    tablo("WEB BASLIKLARI - OKUMA SURESI", oz["bolum_sure_sn"], " sn")
    tablo("DUZENEKLER", oz["duzenekler"])
    tablo("KLIP TAMAMLANMA", oz["klip_tamamlanma_yuzde"], "%")
    tablo("DILLER", oz["dil"])
    tablo("TERK EDILEN EKRAN", ek["terk_ekrani"])
    tablo("OTURUMDA SORU SAYISI", ek.get("oturumda_soru_sayisi", {}))
    tablo("SAAT DAGILIMI", ek["saat_dagilimi"], ust=24)
    if ek["bekciyle_biten_klip"]:
        tablo("!! BEKCIYLE BITEN KLIPLER (sure asimi - incele)", ek["bekciyle_biten_klip"])
    tablo("TUM OLAYLAR", oz["olay_sayilari"], ust=40)

    if a.json:
        Path(a.json).write_text(json.dumps({"ozet": oz, "ek": ek}, ensure_ascii=False, indent=1),
                                encoding="utf-8")
        print(f"\nJSON rapor: {Path(a.json).resolve()}")
    if a.html:
        html_yaz(a.html, oz, ek, filtre)


if __name__ == "__main__":
    main()
