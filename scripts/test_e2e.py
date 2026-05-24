"""End-to-end smoke test.

Çalışan bir BilimFest sunucusuna (``make run``) bağlanır, sahte audio
chunk'ları gönderir, pipeline çıktısını yazdırır. Tüm mock provider'larla
IDLE → WELCOME → LISTENING → SELECTION → THINKING → SPEAKING → LISTENING
geçişlerini gözlemler.

Kullanım:
    python scripts/test_e2e.py [--host http://localhost:8000]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import httpx
import websockets


async def run(host: str) -> int:
    base_http = host.rstrip("/")
    base_ws = base_http.replace("http://", "ws://").replace("https://", "wss://")

    async with httpx.AsyncClient() as client:
        health = await client.get(f"{base_http}/health")
        print(f"[health] {health.status_code} {health.json()}")
        if health.status_code != 200:
            return 1

        chars = await client.get(f"{base_http}/api/v1/characters")
        print(f"[characters] {[c['id'] for c in chars.json()]}")

    events_task = asyncio.create_task(_subscribe_events(base_ws))
    await asyncio.sleep(0.2)  # event WS bağlansın

    try:
        async with websockets.connect(f"{base_ws}/ws/audio") as ws:
            for _ in range(8):
                await ws.send(b"\x00" * 640)
            await ws.send("__end__")
            response = await ws.recv()
            payload = json.loads(response)
            print("[turn_completed]")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            assert payload["type"] == "turn_completed", payload
            assert payload["transcription"], "Transcription boş"
            assert payload["response"], "Response boş"
            assert payload["audio_bytes"] > 0
    finally:
        events_task.cancel()
        try:
            await events_task
        except asyncio.CancelledError:
            pass

    print("[ok] Tüm mock pipeline çalıştı.")
    return 0


async def _subscribe_events(base_ws: str) -> None:
    try:
        async with websockets.connect(f"{base_ws}/ws/events") as ws:
            while True:
                msg = await ws.recv()
                event = json.loads(msg)
                print(f"[event] {event['type']:<25} {event.get('data')}")
    except (asyncio.CancelledError, websockets.ConnectionClosed):
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="BilimFest smoke test")
    parser.add_argument("--host", default="http://localhost:8000")
    args = parser.parse_args()
    return asyncio.run(run(args.host))


if __name__ == "__main__":
    sys.exit(main())
