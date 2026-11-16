#!/usr/bin/env python3
"""Upload a WAV to Server Pi and print recognition results. No extra packages."""
import argparse
import json
import socket
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path, help="Local WAV file path on this Client")
    parser.add_argument("--server", default="http://192.168.222.222:8000")
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    try:
        size = args.wav.stat().st_size
        if not 0 < size <= 20 * 1024 * 1024:
            parser.error("WAV file must be nonempty and at most 20 MiB")
        payload = args.wav.read_bytes()
    except OSError as exc:
        parser.error(f"Cannot read the Client's WAV file: {exc}")

    url = args.server.rstrip("/") + "/transcribe"
    request = Request(url, data=payload, method="POST", headers={
        "Content-Type": "audio/wav",
        "Content-Length": str(len(payload)),
    })
    # This is a direct LAN connection, even if the Client has a system web proxy.
    opener = build_opener(ProxyHandler({}))
    print(f"Uploading {args.wav.name} ({len(payload)} bytes) to {url}", flush=True)
    start = time.perf_counter()
    try:
        with opener.open(request, timeout=args.timeout) as response:
            result = json.load(response)
    except HTTPError as exc:
        print(f"Server rejected the request: HTTP {exc.code}", file=sys.stderr)
        print(exc.read().decode("utf-8", errors="replace"), file=sys.stderr)
        return 1
    except (URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
        print(f"Connection failed: {exc}", file=sys.stderr)
        print("Check Server READY, IP/port, and the Client-to-Pi LAN connection.", file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:
        print(f"Could not read the server response: {exc}", file=sys.stderr)
        return 1
    elapsed = time.perf_counter() - start
    if not result.get("ok"):
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print("\nTranscript:")
    print(result["text"] or "(No speech recognized)")
    print(f"\nRequest ID: {result['request_id']}")
    print(f"Audio duration: {result['audio_seconds']:.3f} s")
    print(f"ASR time: {result['asr_seconds']:.3f} s")
    print(f"RTF: {result['rtf']:.4f}")
    print(f"Client round-trip time: {elapsed:.3f} s")
    print("\nServer JSON:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
