#!/usr/bin/env python3
"""Single-client offline WAV recognition server (standard library + Vosk)."""
import argparse
import io
import json
import logging
import socket
import time
import uuid
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

MAX_BYTES = 20 * 1024 * 1024
MAX_SECONDS = 120


class RecognitionServer(HTTPServer):
    # Deliberately serial for the first single-client milestone.
    def __init__(self, address, model, recognizer_class):
        self.model = model
        self.recognizer_class = recognizer_class
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "EOS-ASR/1.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def reply(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            logging.warning("Client disconnected before reading the response")

    def do_GET(self):
        if self.path == "/health":
            self.reply(200, {"ok": True, "model_loaded": True,
                             "mode": "single_client_serial"})
        else:
            self.reply(404, {"ok": False, "error": "Use POST /transcribe"})

    def do_POST(self):
        if self.path != "/transcribe":
            self.reply(404, {"ok": False, "error": "Use POST /transcribe"})
            return
        if self.headers.get("Transfer-Encoding"):
            self.reply(400, {"ok": False, "error": "Send Content-Length, not chunked encoding"})
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.reply(411, {"ok": False, "error": "Valid Content-Length required"})
            return
        if not 0 < length <= MAX_BYTES:
            self.reply(413 if length > MAX_BYTES else 400,
                       {"ok": False, "error": "WAV body must be 1 byte to 20 MiB"})
            return

        started = time.perf_counter()
        try:
            data = self.rfile.read(length)
        except (socket.timeout, ConnectionError):
            self.reply(408, {"ok": False, "error": "Upload timed out or disconnected"})
            return
        if len(data) != length:
            self.reply(400, {"ok": False, "error": "Incomplete upload"})
            return
        upload_seconds = time.perf_counter() - started

        try:
            with wave.open(io.BytesIO(data), "rb") as audio:
                channels = audio.getnchannels()
                width = audio.getsampwidth()
                rate = audio.getframerate()
                frames = audio.getnframes()
                if channels != 1 or width != 2 or audio.getcomptype() != "NONE":
                    raise ValueError("WAV must be mono, 16-bit, uncompressed PCM")
                if not 16000 <= rate <= 48000:
                    raise ValueError("Supported sample rates: 16000 to 48000 Hz")
                duration = frames / rate
                if not 0 < duration <= MAX_SECONDS:
                    raise ValueError("Audio duration must be greater than 0 and at most 120 seconds")
                pcm = audio.readframes(frames)
                if len(pcm) != frames * channels * width:
                    raise ValueError("WAV PCM data is truncated")
        except (wave.Error, EOFError, ValueError) as exc:
            self.reply(400, {"ok": False, "error": str(exc) or "Invalid WAV file"})
            return

        request_id = uuid.uuid4().hex[:12]
        logging.info("[%s] Received %.2fs WAV (%s bytes) from %s",
                     request_id, duration, length, self.client_address[0])
        asr_start = time.perf_counter()
        try:
            recognizer = self.server.recognizer_class(self.server.model, rate)
            parts = []
            chunk_bytes = 4000 * width
            for offset in range(0, len(pcm), chunk_bytes):
                if recognizer.AcceptWaveform(pcm[offset:offset + chunk_bytes]):
                    text = json.loads(recognizer.Result()).get("text", "")
                    if text:
                        parts.append(text)
            text = json.loads(recognizer.FinalResult()).get("text", "")
            if text:
                parts.append(text)
            asr_seconds = time.perf_counter() - asr_start
        except Exception:
            logging.exception("[%s] Recognition failed", request_id)
            self.reply(500, {"ok": False, "request_id": request_id,
                             "error": "Recognition failed; check the server terminal"})
            return

        result = {
            "ok": True,
            "request_id": request_id,
            "text": " ".join(parts),
            "audio_seconds": round(duration, 3),
            "sample_rate": rate,
            "upload_receive_seconds": round(upload_seconds, 3),
            "asr_seconds": round(asr_seconds, 3),
            "rtf": round(asr_seconds / duration, 4),
            "server_seconds": round(time.perf_counter() - started, 3),
        }
        logging.info("[%s] Done: ASR %.2fs, RTF %.3f, text=%s",
                     request_id, asr_seconds, asr_seconds / duration, result["text"])
        self.reply(200, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="192.168.222.222")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--model", type=Path,
                        default=Path(__file__).resolve().parent / "models/vosk-model-small-en-us-0.15")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if not args.model.is_dir():
        parser.error(f"Model folder not found: {args.model}")
    from vosk import Model, KaldiRecognizer, SetLogLevel
    SetLogLevel(-1)
    logging.info("Loading model once: %s", args.model)
    started = time.perf_counter()
    model = Model(str(args.model))
    logging.info("Model loaded in %.2fs", time.perf_counter() - started)
    with RecognitionServer((args.host, args.port), model, KaldiRecognizer) as server:
        logging.info("READY http://%s:%s/transcribe (Ctrl+C to stop)", args.host, args.port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logging.info("Stopping server")


if __name__ == "__main__":
    main()
