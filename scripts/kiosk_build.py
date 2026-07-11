# manifest.csv -> kiosk/data.js  (karakterler, kategoriler, sorular, sureler, intro/outrolar)
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
KIOSK = ROOT / "kiosk"
KIOSK.mkdir(exist_ok=True)

META = {
    "gazali": {"name": "İmam Gazâlî", "title": "Hüccetü'l-İslâm · Baş Müderris"},
    "nizamulmulk": {"name": "Nizamülmülk", "title": "Büyük Selçuklu Veziri"},
    "meliksah": {"name": "Sultan Melikşah", "title": "Büyük Selçuklu Hükümdarı"},
}
CATS = {
    "gazali": {1: "Hayat & Medrese", 2: "İlim & Öğrenme", 3: "Kalp & Nasihat"},
    "nizamulmulk": {1: "Medreseler & Eğitim", 2: "Devlet & Adalet", 3: "Sultan & Dönem"},
    "meliksah": {1: "Saltanat & Devlet", 2: "İlmin Himayesi", 3: "İnsan Melikşah"},
}
ORDER = ["gazali", "nizamulmulk", "meliksah"]

rows = list(csv.DictReader(open(ROOT / "senaryolar" / "manifest.csv", encoding="utf-8-sig")))
chars: dict[str, dict] = {
    c: {"id": c, **META[c], "img": f"img/{c}.svg", "intros": [], "outros": [],
        "categories": [{"name": CATS[c][k], "questions": []} for k in (1, 2, 3)]}
    for c in ORDER
}
qmap: dict[tuple, dict] = {}
for r in rows:
    kod = r["kod"]
    dur = float(r["tahmini_sn"])
    ch = kod.split("_")[0]
    item = {"code": kod, "dur": dur}
    if "_int_" in kod:
        chars[ch]["intros"].append(item)
    elif "_out_" in kod:
        chars[ch]["outros"].append(item)
    else:
        m = re.match(r"^[a-z]+_k(\d)_s(\d)(?:_v(\d))?$", kod)
        k, s = int(m.group(1)), int(m.group(2))
        key = (ch, k, s)
        if key not in qmap:
            qmap[key] = {"q": r["soru"], "variants": []}
            chars[ch]["categories"][k - 1]["questions"].append(qmap[key])
        qmap[key]["variants"].append(item)

data = {
    "config": {
        "pin": "1206", "rights": 3, "idleMs": 75000, "thanksMs": 20000,
        "skipAfterMs": 6000, "mediaBase": "../sesler/", "mediaExt": ".mp3",
        "videoBase": "../videolar/", "videoExt": ".mp4",
        # Canli site adresi (QR bununla gosterilir). Ozel durum icin kiosk_qr.py override eder.
        "webUrl": "https://nizamiyeweb.vercel.app/",
    },
    "characters": [chars[c] for c in ORDER],
}
out = KIOSK / "data.js"
out.write_text("window.KIOSK_DATA = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n", encoding="utf-8")
nq = sum(len(cat["questions"]) for c in data["characters"] for cat in c["categories"])
nv = sum(len(q["variants"]) for c in data["characters"] for cat in c["categories"] for q in cat["questions"])
print(f"yazildi: {out} | {len(data['characters'])} karakter, {nq} soru, {nv} cevap klibi")
