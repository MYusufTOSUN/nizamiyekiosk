# -*- coding: utf-8 -*-
"""
Karakter basina atmosfer katmanlari.

OLCULEN ISIK YONU (yuz + govde iki bagimsiz olcumle dogrulandi)
  Gazali        sagdan   (+26.8% / +25.5%)
  Nizamulmulk   soldan   ( -8.6% / -22.5%)
  Meliksah      soldan   (-25.8% / -41.3%)
  ucu de yukaridan       (+17 .. +35%)
Huzme bu yonle ayni taraftan gelmek zorunda; ters gelirse goze batar.

Uretilen dosyalar (karakter basina):
  atmf_<k>_arka.png   figurun ARKASINA   — hale + huzme
  atmf_<k>_on.png     figurun ONUNE      — cok zayif pus (izleyici-figur arasi hava)
  atmf_<k>_gain.png   partikul kazanci   — huzmenin icindeki toz daha parlak parlar
"""
import numpy as np
from PIL import Image
from scipy import ndimage

PW, PH = 3840, 2160
FX, FY = 1920, 1000
SICAK = np.array([1.00, 0.86, 0.66])
D2R = np.pi / 180


def ss(t):
    t = np.clip(t, 0, 1)
    return t * t * t * (t * (t * 6 - 15) + 10)


def kenar_maskesi(pay=0.16):
    y, x = np.mgrid[0:PH, 0:PW].astype(np.float32)
    return (np.minimum(ss(x / (PW * pay)), ss((PW - 1 - x) / (PW * pay))) *
            np.minimum(ss(y / (PH * pay)), ss((PH - 1 - y) / (PH * pay))))


KM = kenar_maskesi()


def gauss2(cx, cy, sx, sy):
    y, x = np.mgrid[0:PH, 0:PW].astype(np.float32)
    return np.exp(-0.5 * (((x - cx) / sx) ** 2 + ((y - cy) / sy) ** 2))


def huzme(sx, sy, aci, yayilim, uzunluk):
    y, x = np.mgrid[0:PH, 0:PW].astype(np.float32)
    dx, dy = x - sx, y - sy
    r = np.hypot(dx, dy) + 1e-6
    da = np.arctan2(np.sin(np.arctan2(dy, dx) - aci), np.cos(np.arctan2(dy, dx) - aci))
    return (np.exp(-0.5 * (da / yayilim) ** 2) ** 1.6 *
            np.exp(-(r / uzunluk) ** 1.7)).astype(np.float32)


def yaz(ad, k, tepe, dosya, renkli=True, taban=0.0):
    k = k / (k.max() + 1e-9) * KM
    if renkli:
        v = np.clip(k[..., None] * SICAK[None, None, :] * tepe, 0, 255)
        v[v < 1.6] = 0
    else:
        v = np.clip((taban + (1 - taban) * k) * tepe, 0, 255)
        v = np.repeat(v[..., None], 3, axis=2)
    Image.fromarray(v.astype(np.uint8)).save(dosya)
    return int(v.max())


def uret(kod, sagdan):
    """sagdan=True ise huzme sag ustten gelir."""
    hale = gauss2(FX, FY - 120, 1150, 780)
    if sagdan:
        h = huzme(PW * 1.02, -PH * 0.55, (180 - 52) * D2R, 13 * D2R, PH * 1.9)
        h2 = huzme(PW * 0.86, -PH * 0.5, (180 - 40) * D2R, 6 * D2R, PH * 1.8)
    else:
        h = huzme(-PW * 0.02, -PH * 0.55, 52 * D2R, 13 * D2R, PH * 1.9)
        h2 = huzme(PW * 0.14, -PH * 0.5, 40 * D2R, 6 * D2R, PH * 1.8)

    hb = ndimage.gaussian_filter(h + 0.55 * h2, 26)
    arka = 0.55 * hale + hb
    on = ndimage.gaussian_filter(h + 0.55 * h2, 46) * 0.9
    gain = ndimage.gaussian_filter(h + 0.55 * h2, 34)

    a = yaz(kod, arka, 28, f"atmf_{kod}_arka.png")
    o = yaz(kod, on, 7, f"atmf_{kod}_on.png")
    # partikul kazanci: huzme disinda %45, icinde %100 -> tozun isikta parlamasi
    g = yaz(kod, gain, 255, f"atmf_{kod}_gain.png", renkli=False, taban=0.45)
    return dict(kod=kod, yon="sag" if sagdan else "sol", arka_max=a, on_max=o, gain_max=g)


if __name__ == "__main__":
    import json
    for kod, sagdan in (("gazali", True), ("nizamulmulk", False), ("meliksah", False)):
        print(json.dumps(uret(kod, sagdan), ensure_ascii=False))
