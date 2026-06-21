"""Stall-dayanikli HF model indirici.

hf_xet / curl bu agda burst sonrasi stall'a giriyor. Bu script her dosyayi
Range istekleriyle parca parca ceker; baglanti stall ederse (read timeout)
keser, diskten kaldigi yerden DEVAM eder. Tek-akis (yavas bandi bolmez).
"""
from __future__ import annotations

import os
import sys
import time
from fnmatch import fnmatch

import requests

HF = "https://huggingface.co"
EXCLUDE = ["*.bin", "onnx/*", "*.onnx", "tf_model.h5", "rust_model.ot",
           "*.ckpt", "coreml/*", "openvino/*", "*.msgpack", "*.h5"]

MODELS = [
    ("Systran/faster-whisper-large-v3", "data/models/whisper/faster-whisper-large-v3", [], None),
    ("intfloat/multilingual-e5-large", "data/models/embeddings/multilingual-e5-large", EXCLUDE, None),
    ("BAAI/bge-reranker-v2-m3", "data/models/reranker/bge-reranker-v2-m3", EXCLUDE, None),
    ("bartowski/Meta-Llama-3.1-8B-Instruct-GGUF", "data/models/llama", [], "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"),
]

CHUNK = 1024 * 1024
session = requests.Session()


def tree(repo: str):
    r = session.get(f"{HF}/api/models/{repo}/tree/main?recursive=true", timeout=60)
    r.raise_for_status()
    return [(i["path"], int(i.get("size", 0))) for i in r.json() if i.get("type") == "file"]


def fetch(url: str, path: str, size: int) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    attempt = 0
    while True:
        cur = os.path.getsize(path) if os.path.exists(path) else 0
        if size and cur >= size:
            return
        attempt += 1
        headers = {"Range": f"bytes={cur}-"} if cur else {}
        try:
            with session.get(url, headers=headers, stream=True, timeout=(15, 40), allow_redirects=True) as r:
                if r.status_code in (416,):  # already complete
                    return
                r.raise_for_status()
                mode = "ab" if cur else "wb"
                last = time.time()
                with open(path, mode) as f:
                    for chunk in r.iter_content(CHUNK):
                        if chunk:
                            f.write(chunk)
                            cur += len(chunk)
                            if time.time() - last > 10:
                                pct = (100.0 * cur / size) if size else 0
                                print(f"    {cur/1e6:8.1f}/{size/1e6:8.1f} MB ({pct:4.1f}%)", flush=True)
                                last = time.time()
            # loop re-checks size; if not complete, resumes
        except (requests.exceptions.RequestException, OSError) as e:
            got = os.path.getsize(path) if os.path.exists(path) else 0
            print(f"    [retry {attempt}] @ {got/1e6:.1f} MB after: {str(e)[:80]}", flush=True)
            time.sleep(3)


def main() -> int:
    for repo, dest, excl, only in MODELS:
        print(f"==== {repo} -> {dest} ====", flush=True)
        try:
            files = tree(repo)
        except Exception as e:  # noqa: BLE001
            print(f"  API ERROR: {e}", flush=True)
            return 1
        for path, size in files:
            if only and path != only:
                continue
            if any(fnmatch(path, p) for p in excl):
                continue
            out = os.path.join(dest, path)
            if os.path.exists(out) and size and os.path.getsize(out) == size:
                print(f"  OK(skip) {path} [{size/1e6:.1f} MB]", flush=True)
                continue
            print(f"  GET {path} [{size/1e6:.1f} MB]", flush=True)
            fetch(f"{HF}/{repo}/resolve/main/{path}", out, size)
            print(f"  done {path}", flush=True)
        print(f"REPO_DONE {repo}", flush=True)
    print("ALL_DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
