# sesler/ klasoru icin dinleme/kontrol sayfasi uretir (manifest etiketleriyle).
import csv
from pathlib import Path

ROOT = Path(__file__).parent.parent
rows = list(csv.DictReader(open(ROOT / "senaryolar" / "manifest.csv", encoding="utf-8-sig")))
by_char: dict[str, list[dict]] = {}
for r in rows:
    by_char.setdefault(r["kod"].split("_")[0], []).append(r)

TITLES = {
    "gazali": "İmam Gazâlî (Murat Albayrak)",
    "nizamulmulk": "Nizamülmülk (Seyfullah Kartal)",
    "meliksah": "Sultan Melikşah (Aykut Akkaşoğlu)",
}
secs = []
for ch in ("gazali", "nizamulmulk", "meliksah"):
    items = "".join(
        "<div class='p'><b>{kod}</b> <span>· {tip} · ~{sn}sn</span><br>"
        "<i>{soru}</i><br><audio controls preload='none' src='{kod}.mp3'></audio></div>".format(
            kod=r["kod"], tip=r["tip"], sn=r["tahmini_sn"], soru=r["soru"] or "-")
        for r in by_char[ch]
    )
    secs.append(f"<section><h2>{TITLES[ch]}</h2>{items}</section>")

html = (
    "<!doctype html><html lang='tr'><head><meta charset='utf-8'><title>63 Ses — Kontrol</title>"
    "<style>body{background:#0b1020;color:#f3ead4;font-family:system-ui;max-width:820px;margin:2rem auto;padding:0 1rem}"
    "h1,h2{color:#e3b552} h2{border-bottom:1px solid #333;padding-bottom:4px;margin-top:2rem}"
    ".p{margin:10px 0;padding:10px;background:#131a2e;border-radius:10px} .p span{color:#8a93ac;font-size:.85em}"
    ".p i{color:#c7ccdb;font-size:.9em} audio{width:100%;margin-top:6px}</style></head>"
    "<body><h1>63 Ses — Dinleme Kontrolü</h1>"
    "<p>Beğenmediklerinin kodunu not et, bana söyle; sadece onları yeniden üretirim.</p>"
    + "".join(secs) + "</body></html>"
)
(ROOT / "sesler" / "dinle.html").write_text(html, encoding="utf-8")
print("yazildi:", ROOT / "sesler" / "dinle.html")
