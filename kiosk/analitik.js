/* Nizamiye analitik istemcisi — kiosk ve web sitesi AYNI dosyayi kullanir.
 *
 * NEDEN BOYLE
 *  - Ziyaretci kimligi localStorage'da KALICI: oturuma ozel degil, tarayici
 *    silinmedikce ayni kisi ayni sayilir.
 *  - Olaylar once yerel KUYRUGA yazilir, sonra toplu gonderilir. Toplayici
 *    kapaliysa kuyrukta bekler ve acilinca gider; boylece festival sirasinda
 *    sunucu bir an duse bile veri kaybolmaz.
 *  - Sayfa kapanirken sendBeacon kullanilir; fetch iptal edilirdi.
 *
 * KULLANIM
 *    <script src="analitik.js"></script>
 *    Analitik.baslat({ kaynak: "kiosk", dil: "tr" });
 *    Analitik.olay("soru_secildi", { kod: "meliksah_k1_s2", karakter: "meliksah" });
 */
(function (global) {
  "use strict";

  var ANAHTAR_ZIYARETCI = "nizamiye_analitik_ziyaretci";
  var ANAHTAR_KUYRUK = "nizamiye_analitik_kuyruk";
  var KUYRUK_TAVAN = 2000;      // tasarsa en ESKI olaylar dusurulur
  var GONDER_ARALIK = 4000;     // ms

  var A = {
    hazir: false,
    kaynak: "bilinmiyor",
    uc: "/a/olay",
    dil: null,
    ziyaretci: null,
    oturum: null,
    _kuyruk: [],
    _zaman: null,
    _gonderiyor: false,
  };

  function rastgele(on) {
    try {
      var d = new Uint8Array(8);
      (global.crypto || global.msCrypto).getRandomValues(d);
      return on + Array.from(d).map(function (x) {
        return x.toString(16).padStart(2, "0");
      }).join("");
    } catch (e) {
      return on + Math.random().toString(16).slice(2) + Date.now().toString(16);
    }
  }

  function oku(anahtar, varsayilan) {
    try {
      var s = localStorage.getItem(anahtar);
      return s ? JSON.parse(s) : varsayilan;
    } catch (e) { return varsayilan; }
  }

  function yaz(anahtar, deger) {
    try { localStorage.setItem(anahtar, JSON.stringify(deger)); } catch (e) {}
  }

  function ziyaretciAl() {
    var v = null;
    try { v = localStorage.getItem(ANAHTAR_ZIYARETCI); } catch (e) {}
    if (!v) {
      v = rastgele("z_");
      try { localStorage.setItem(ANAHTAR_ZIYARETCI, v); } catch (e) {}
    }
    return v;
  }

  /* --- kuyruk ---------------------------------------------------------- */
  function kuyrugaEkle(o) {
    A._kuyruk.push(o);
    if (A._kuyruk.length > KUYRUK_TAVAN) {
      A._kuyruk = A._kuyruk.slice(-KUYRUK_TAVAN);
    }
    yaz(ANAHTAR_KUYRUK, A._kuyruk);
  }

  function govde(olaylar) {
    return JSON.stringify({
      kaynak: A.kaynak,
      ziyaretci: A.ziyaretci,
      oturum: A.oturum,
      dil: A.dil,
      olaylar: olaylar,
    });
  }

  function gonder(zorla) {
    if (A._gonderiyor || !A._kuyruk.length) return;
    if (!zorla && A._kuyruk.length < 1) return;
    A._gonderiyor = true;
    var parca = A._kuyruk.slice(0, 200);

    fetch(A.uc, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: govde(parca),
      keepalive: true,
      // Toplayici baska kokende olabilir (web sitesi); CORS acik.
      mode: "cors",
      credentials: "omit",
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      // Yalniz GERCEKTEN yazilanlari kuyruktan dus.
      A._kuyruk = A._kuyruk.slice(parca.length);
      yaz(ANAHTAR_KUYRUK, A._kuyruk);
    }).catch(function () {
      // Sessiz: toplayici kapali olabilir. Kuyrukta kalir, sonra denenir.
    }).finally(function () {
      A._gonderiyor = false;
    });
  }

  function beaconGonder() {
    if (!A._kuyruk.length) return;
    try {
      var b = new Blob([govde(A._kuyruk.slice(0, 200))], { type: "application/json" });
      if (navigator.sendBeacon(A.uc, b)) {
        A._kuyruk = A._kuyruk.slice(200);
        yaz(ANAHTAR_KUYRUK, A._kuyruk);
      }
    } catch (e) {}
  }

  /* --- genel arayuz ---------------------------------------------------- */
  /* Uc adresi: verilmediyse SAYFANIN KENDI sunucusundan turetilir.
   * Kiosk 8777'de zaten toplayicidan servis edilir -> ayni koken, /a/olay.
   * Web sitesi sergi aginda 4321'de kosarsa -> ayni IP'nin 8777 portu; boylece
   * ziyaretcilerin telefonu da toplayiciya yazabilir. Disariya bagimlilik yok.
   * Ulasilamazsa istemci sessizce kuyrukta bekletir, siteyi yavaslatmaz. */
  function ucTuret() {
    try {
      var l = global.location;
      if (l.port === "8777" || l.pathname.indexOf("/a/") === 0) return "/a/olay";
      if (l.protocol === "file:") return "http://127.0.0.1:8777/a/olay";
      return l.protocol + "//" + l.hostname + ":8777/a/olay";
    } catch (e) { return "http://127.0.0.1:8777/a/olay"; }
  }

  A.baslat = function (ayar) {
    ayar = ayar || {};
    A.kaynak = ayar.kaynak || "bilinmiyor";
    A.uc = ayar.uc || ucTuret();
    A.dil = ayar.dil || (document.documentElement.lang || null);
    A.ziyaretci = ziyaretciAl();
    A.oturum = rastgele("o_");
    A._kuyruk = oku(ANAHTAR_KUYRUK, []) || [];   // onceki oturumdan kalanlar
    A.hazir = true;

    if (A._zaman) clearInterval(A._zaman);
    A._zaman = setInterval(function () { gonder(false); }, GONDER_ARALIK);

    // Sayfa kapanirken/gizlenirken son parti
    global.addEventListener("pagehide", beaconGonder);
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") beaconGonder();
    });

    // Bekleyen varsa hemen dene (onceki oturumun kuyrugu)
    gonder(true);
    return A;
  };

  A.olay = function (ad, veri) {
    if (!A.hazir) return;
    veri = veri || {};
    kuyrugaEkle({
      t: new Date().toISOString(),
      olay: String(ad),
      karakter: veri.karakter || null,
      kod: veri.kod || null,
      baslik: veri.baslik || null,
      sure_ms: typeof veri.sure_ms === "number" ? Math.round(veri.sure_ms) : null,
      ek: veri.ek || null,
    });
    // Onemli olaylari bekletme
    if (veri.acil) gonder(true);
  };

  A.yeniOturum = function () {
    A.oturum = rastgele("o_");
    return A.oturum;
  };

  A.bekleyen = function () { return A._kuyruk.length; };
  A.simdiGonder = function () { gonder(true); };

  global.Analitik = A;
})(window);
