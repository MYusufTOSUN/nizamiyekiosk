# -*- coding: utf-8 -*-
"""
Karton golge figuru icin A4 kalip.

Master gorselden figurun DIS KONTURUNU cikarir, A4'e (300 DPI) oturtur,
beyaz zemin uzerine siyah cizgi olarak yazar. Ekrana koyup kopya kagidiyla
gecilebilir; yazdirilirsa %100 olcekte gercek A4 olur (DPI gomulu).

Golge yalniz DIS HATTI gosterir — ic detay kartonda islevsiz, o yuzden yok.
"""
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

A4W, A4H, DPI = 2480, 3508, 300          # 210 x 297 mm @ 300 dpi
KENAR = int(10 / 25.4 * DPI)             # 10 mm kenar boslugu
CIZGI_MM = 0.8                           # kontur kalinligi


def siluet(yol, esik=18):
    im = Image.open(yol).convert("L")
    a = np.asarray(im).astype(np.uint8)
    m = a > esik
    m = ndimage.binary_opening(m, np.ones((5, 5)))
    m = ndimage.binary_closing(m, np.ones((9, 9)))

    lbl, n = ndimage.label(m)
    if n > 1:
        boy = ndimage.sum(m, lbl, range(1, n + 1))
        m = lbl == (np.argmax(boy) + 1)

    # --- SILUET KURALI: dis hattin ALTINDA kalan her sey dolu ---
    # binary_fill_holes yetmiyor: parmak aralarindaki ve el altindaki koyu
    # boşluklar master'in alt siyah seridine baglaniyor, "delik" sayilmiyor ve
    # siluete centik aciyor. Karton zaten tek parca kesilecegi icin dogru kural
    # bu: her sutunda figurun en ust pikselinden govde tabanina kadar doldur.
    ys, xs = np.where(m)
    taban = ys.max()
    once = m.sum()
    dolu = np.zeros_like(m)
    for x in range(m.shape[1]):
        s = np.where(m[:, x])[0]
        if len(s):
            dolu[s.min():taban + 1, x] = True
    m = dolu
    print(f"      dolgu ile eklenen alan: %{(m.sum()-once)/once*100:.2f}")

    lbl, n = ndimage.label(m)
    if n > 1:                             # en buyuk parcayi tut
        boy = ndimage.sum(m, lbl, range(1, n + 1))
        m = lbl == (np.argmax(boy) + 1)
    # koseleri yuvarla: karton bicakla kesilecek, ince tarak dislerini sil
    f = ndimage.gaussian_filter(m.astype(float), 3.5) > 0.5
    f = ndimage.binary_closing(f, np.ones((7, 7)))
    return f


def kontur(m, kalinlik_px):
    ic = ndimage.binary_erosion(m, np.ones((3, 3)))
    k = m & ~ic
    r = max(1, int(round(kalinlik_px)))
    if r > 1:
        k = ndimage.binary_dilation(k, np.ones((r, r)))
    return k


def a4(yol_png, ad, cikti):
    m = siluet(yol_png)
    ys, xs = np.where(m)
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    kes = m[y0:y1 + 1, x0:x1 + 1]
    h, w = kes.shape

    kul_w, kul_h = A4W - 2 * KENAR, A4H - 2 * KENAR - int(12 / 25.4 * DPI)
    s = min(kul_w / w, kul_h / h)
    nw, nh = int(w * s), int(h * s)

    buyuk = np.asarray(
        Image.fromarray((kes * 255).astype(np.uint8)).resize((nw, nh), Image.LANCZOS)
    ) > 127
    k = kontur(buyuk, CIZGI_MM / 25.4 * DPI)

    sayfa = Image.new("L", (A4W, A4H), 255)
    kat = Image.fromarray(np.where(k, 0, 255).astype(np.uint8))
    ox, oy = (A4W - nw) // 2, KENAR
    sayfa.paste(kat, (ox, oy), Image.fromarray((k * 255).astype(np.uint8)))

    d = ImageDraw.Draw(sayfa)
    # kose isaretleri (kartonu hizalamak icin)
    t = int(6 / 25.4 * DPI)
    for cx, cy in ((KENAR, KENAR), (A4W - KENAR, KENAR),
                   (KENAR, A4H - KENAR), (A4W - KENAR, A4H - KENAR)):
        d.line([(cx - t, cy), (cx + t, cy)], fill=170, width=3)
        d.line([(cx, cy - t), (cx, cy + t)], fill=170, width=3)
    # 100 mm olcek cubugu — yazdirma dogrulamasi
    bx, by = KENAR, A4H - KENAR - int(14 / 25.4 * DPI)
    d.line([(bx, by), (bx + 100 / 25.4 * DPI, by)], fill=90, width=5)
    for i in range(11):
        x = bx + i * 10 / 25.4 * DPI
        d.line([(x, by - 12), (x, by + 12)], fill=90, width=3)
    try:
        f1 = ImageFont.truetype("arial.ttf", 46)
        f2 = ImageFont.truetype("arial.ttf", 34)
    except Exception:
        f1 = f2 = ImageFont.load_default()
    d.text((bx, by + 26), "100 mm  (yazdirirken %100 olcek — bu cubugu cetvelle dogrula)",
           fill=90, font=f2)
    d.text((bx, A4H - KENAR - int(4 / 25.4 * DPI)), ad, fill=60, font=f1)

    sayfa.save(cikti, dpi=(DPI, DPI))
    return dict(ad=ad, kontur_px=int(k.sum()),
                figur_mm=(round(nw / DPI * 25.4, 1), round(nh / DPI * 25.4, 1)),
                oran=round(w / h, 3))


if __name__ == "__main__":
    K = r"c:\Users\tosun\Desktop\bilimfest\video\00_master\kadrajli\_v7"
    for dosya, ad in (("gazali_master.png", "Imam Gazali"),
                      ("nizamulmulk_master.png", "Nizamulmulk"),
                      ("meliksah_master.png", "Sultan Meliksah")):
        r = a4(f"{K}\\{dosya}", ad, f"golge_kalip_{dosya.split('_')[0]}_A4.png")
        print(f"{r['ad']:18s} figur {r['figur_mm'][0]:6.1f} x {r['figur_mm'][1]:6.1f} mm   "
              f"en/boy {r['oran']:.3f}   kontur {r['kontur_px']} px")
