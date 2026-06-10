"""Unreal bridge protokol testi — gerçek Unreal olmadan mock server'a paket gönder.

UDP blendshape sender ve TCP scene controller'ı kendi Python mock'larına bağla,
paket roundtrip + latency ölç. Coworker'ın Unreal projesi olmadan da protokol
sağlamlığını doğrularız.

Kullanım:
    python scripts/test_unreal_send.py
    python scripts/test_unreal_send.py --duration 5 --rate 50
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
import time
import uuid
from collections.abc import AsyncIterator

from src.core.interfaces import BlendshapeFrame, SceneCommand
from src.unreal_bridge.live_link import LiveLinkSceneController
from src.unreal_bridge.protocol import (
    ARKIT_BLENDSHAPE_KEYS,
    unpack_blendshape_packet,
)


async def udp_mock_server(port: int, stop: asyncio.Event, stats: dict[str, int]) -> None:
    """UDP server: gelen paketleri parse et + say + latency ölç."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    sock.bind(("127.0.0.1", port))
    loop = asyncio.get_running_loop()
    print(f"[UDP mock] dinleniyor port={port}")
    while not stop.is_set():
        try:
            data, _addr = await asyncio.wait_for(
                loop.sock_recvfrom(sock, 4096), timeout=0.5
            )
        except (TimeoutError, asyncio.TimeoutError):
            continue
        try:
            ts_us, char_id, _values = unpack_blendshape_packet(data)
            stats["udp_received"] += 1
            stats["last_char"] = char_id  # type: ignore[assignment]
        except ValueError as exc:
            print(f"[UDP mock] kötü paket: {exc}")
            stats["udp_errors"] += 1
    sock.close()


async def tcp_mock_server(port: int, stop: asyncio.Event, stats: dict[str, int]) -> None:
    """TCP server: JSON komut al, request_id ile cevap dön."""

    async def handle(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        while not stop.is_set():
            line = await reader.readline()
            if not line:
                break
            try:
                msg = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError as exc:
                print(f"[TCP mock] bozuk JSON: {exc}")
                continue
            stats["tcp_received"] += 1
            response = json.dumps(
                {
                    "type": "command_response",
                    "request_id": msg.get("request_id", ""),
                    "status": "ok",
                    "data": {"echo": msg.get("command", "")},
                    "error_message": None,
                }
            ) + "\n"
            writer.write(response.encode("utf-8"))
            await writer.drain()

    server = await asyncio.start_server(handle, "127.0.0.1", port)
    print(f"[TCP mock] dinleniyor port={port}")
    try:
        while not stop.is_set():
            await asyncio.sleep(0.2)
    finally:
        server.close()
        await server.wait_closed()


async def main(duration: float, rate: int, udp_port: int, tcp_port: int) -> int:
    print(f"\n=== Unreal Bridge Protokol Testi (mock) ===")
    print(f"  UDP port: {udp_port}, TCP port: {tcp_port}")
    print(f"  Süre: {duration}s, blendshape rate: {rate} Hz\n")

    stop = asyncio.Event()
    stats: dict[str, int] = {"udp_received": 0, "udp_errors": 0, "tcp_received": 0}

    # Mock server'ları başlat
    udp_task = asyncio.create_task(udp_mock_server(udp_port, stop, stats))
    tcp_task = asyncio.create_task(tcp_mock_server(tcp_port, stop, stats))
    await asyncio.sleep(0.3)  # bind tamamlansın

    # Gerçek LiveLinkSceneController ile gönderim
    from src.core.config import UnrealBridgeSection

    ctl = LiveLinkSceneController(
        UnrealBridgeSection(
            provider="live_link",
            udp_host="127.0.0.1",
            udp_port=udp_port,
            tcp_host="127.0.0.1",
            tcp_port=tcp_port,
        )
    )

    # TCP test: init + show_character komutları
    t0 = time.perf_counter()
    init_resp = await ctl.send_command(
        SceneCommand(command="init", params={"env": "test"}, request_id=uuid.uuid4().hex)
    )
    tcp_init_ms = int((time.perf_counter() - t0) * 1000)
    print(f"[TCP] init → {init_resp.status} (RTT {tcp_init_ms}ms)")

    show_resp = await ctl.send_command(
        SceneCommand(
            command="show_character",
            params={"character_id": "cezeri", "transition": "fade_in"},
            request_id=uuid.uuid4().hex,
        )
    )
    print(f"[TCP] show_character → {show_resp.status}")

    # UDP test: 50 Hz blendshape stream
    print(f"\n[UDP] {duration}s blendshape stream ({rate} Hz)...")
    total_frames = int(duration * rate)
    interval = 1.0 / rate
    udp_start = time.perf_counter()

    async def frame_gen() -> AsyncIterator[BlendshapeFrame]:
        for i in range(total_frames):
            yield BlendshapeFrame(
                timestamp_ms=int(i * interval * 1000),
                values={k: float(i % 100) / 100.0 for k in ARKIT_BLENDSHAPE_KEYS},
            )
            await asyncio.sleep(interval)

    await ctl.send_blendshapes("cezeri", frame_gen())
    udp_elapsed = time.perf_counter() - udp_start

    # Drain — server'ın son paketleri işlemesi için
    await asyncio.sleep(0.3)

    # Sonuç
    print(f"\n=== Sonuç ===")
    print(f"  TCP komut: gönderildi=2, mock alındı={stats['tcp_received']}")
    print(
        f"  UDP frame: gönderildi={total_frames}, alındı={stats['udp_received']}, "
        f"kayıp={(total_frames - stats['udp_received'])/total_frames*100:.1f}%"
    )
    print(f"  UDP bozuk: {stats['udp_errors']}")
    print(f"  Toplam süre: {udp_elapsed:.1f}s (hedef {duration}s)")
    effective_rate = stats["udp_received"] / udp_elapsed
    print(f"  Effective rate: {effective_rate:.1f} Hz")

    stop.set()
    await ctl.close()
    udp_task.cancel()
    tcp_task.cancel()
    for t in (udp_task, tcp_task):
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass

    # Pass/fail
    udp_loss = (total_frames - stats["udp_received"]) / total_frames
    if udp_loss > 0.05:
        print("\n[FAIL] UDP kayıp >5%")
        return 1
    if stats["tcp_received"] != 2:
        print("\n[FAIL] TCP komut sayısı yanlış")
        return 1
    print("\n[OK] Protokol testi başarılı.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Unreal bridge protokol mock testi")
    parser.add_argument("--duration", type=float, default=3.0)
    parser.add_argument("--rate", type=int, default=50)
    parser.add_argument("--udp-port", type=int, default=22345)
    parser.add_argument("--tcp-port", type=int, default=22346)
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.duration, args.rate, args.udp_port, args.tcp_port)))
