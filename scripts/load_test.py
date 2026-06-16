"""M14: yük/dayanıklılık testi — çalışan orchestrator'a ardışık WS turu gönderir.

İki mod:
- **load**: N turu olabildiğince hızlı (concurrency=1, single-active tasarım) → P50/P95
- **soak**: belirtilen süre boyunca tur tekrarla, bellek/latency drift izle

Çalışan sunucu gerekir: `make run` (veya systemd). Mock provider'larla da
çalışır (gerçek modelsiz pipeline davranışı + bellek davranışı görülür).

Kullanım:
    python scripts/load_test.py --mode load --turns 50
    python scripts/load_test.py --mode soak --minutes 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time

import httpx
import websockets


async def _one_turn(base_ws: str, chunks: int = 8) -> dict:
    t0 = time.perf_counter()
    async with websockets.connect(f"{base_ws}/ws/audio") as ws:
        for _ in range(chunks):
            await ws.send(b"\x00" * 640)
        await ws.send("__end__")
        raw = await ws.recv()
    payload = json.loads(raw)
    payload["_wall_ms"] = int((time.perf_counter() - t0) * 1000)
    return payload


async def run_load(base_http: str, base_ws: str, turns: int) -> int:
    async with httpx.AsyncClient() as c:
        h = await c.get(f"{base_http}/health")
        if h.status_code != 200:
            print("Sunucu ayakta değil.")
            return 1

    latencies: list[int] = []
    errors = 0
    for i in range(turns):
        try:
            p = await _one_turn(base_ws)
            latencies.append(p.get("latency_ms", p["_wall_ms"]))
            if (i + 1) % 10 == 0:
                print(f"  {i + 1}/{turns} tur, son latency={latencies[-1]}ms")
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"  [HATA] tur {i}: {exc}")
    if latencies:
        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
        print(f"\nTur: {len(latencies)}, hata: {errors}")
        print(f"P50={p50}ms P95={p95}ms max={max(latencies)}ms mean={statistics.mean(latencies):.0f}ms")
    return 0 if errors == 0 else 1


async def run_soak(base_http: str, base_ws: str, minutes: float) -> int:
    deadline = time.perf_counter() + minutes * 60
    turn = 0
    errors = 0
    first_lat: int | None = None
    last_lat: int | None = None
    print(f"Soak {minutes} dk başladı...")
    while time.perf_counter() < deadline:
        try:
            p = await _one_turn(base_ws)
            lat = p.get("latency_ms", p["_wall_ms"])
            first_lat = first_lat if first_lat is not None else lat
            last_lat = lat
            turn += 1
            if turn % 20 == 0:
                drift = (last_lat - first_lat) if first_lat else 0
                print(f"  tur {turn}, latency={lat}ms, drift={drift:+d}ms (ilk {first_lat}ms)")
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"  [HATA] tur {turn}: {exc}")
        await asyncio.sleep(1.0)
    print(f"\nSoak bitti: {turn} tur, {errors} hata.")
    if first_lat and last_lat:
        drift = last_lat - first_lat
        print(f"Latency drift: {drift:+d}ms ({first_lat} → {last_lat}). >%50 ise leak şüphesi.")
    # Bellek için: ayrı terminalde `watch nvidia-smi` + `htop` izlenmeli
    return 0 if errors == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="BilimFest yük/soak testi")
    ap.add_argument("--mode", choices=["load", "soak"], default="load")
    ap.add_argument("--turns", type=int, default=50)
    ap.add_argument("--minutes", type=float, default=30.0)
    ap.add_argument("--host", default="http://localhost:8000")
    args = ap.parse_args()
    base_http = args.host.rstrip("/")
    base_ws = base_http.replace("http://", "ws://").replace("https://", "wss://")
    if args.mode == "load":
        return asyncio.run(run_load(base_http, base_ws, args.turns))
    return asyncio.run(run_soak(base_http, base_ws, args.minutes))


if __name__ == "__main__":
    sys.exit(main())
