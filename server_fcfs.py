#!/usr/bin/env python3
"""Concurrent WAV uploads, FIFO admission, and one shared-model ASR worker."""
import argparse
import csv
import io
import json
import logging
import queue
import socket
import threading
import time
import uuid
import wave
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BYTES = 20 * 1024 * 1024
MAX_SECONDS = 120
CSV_FIELDS = ["sequence", "request_id", "client_id", "status", "received_at_s",
              "started_at_s", "finished_at_s", "queue_wait_seconds", "audio_seconds",
              "asr_seconds", "rtf", "text", "error"]


@dataclass
class Job:
    sequence: int
    request_id: str
    client_id: str
    body: bytes
    received: float
    upload_receive_seconds: float
    done: threading.Event = field(default_factory=threading.Event)
    status: int = 500
    result: dict = field(default_factory=dict)


def decode_wav(body):
    with wave.open(io.BytesIO(body), "rb") as audio:
        rate, frames = audio.getframerate(), audio.getnframes()
        if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getcomptype() != "NONE":
            raise ValueError("WAV must be mono, 16-bit, uncompressed PCM")
        if not 16000 <= rate <= 48000:
            raise ValueError("Supported sample rates: 16000 to 48000 Hz")
        duration = frames / rate
        if not 0 < duration <= MAX_SECONDS:
            raise ValueError("Audio must be greater than 0 and at most 120 seconds")
        pcm = audio.readframes(frames)
        if len(pcm) != frames * 2:
            raise ValueError("WAV PCM data is truncated")
    return rate, duration, pcm


class FCFSServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, model, recognizer_class, csv_path, max_requests=8):
        if max_requests < 1:
            raise ValueError("max_requests must be positive")
        self.model = model
        self.recognizer_class = recognizer_class
        self.origin = time.perf_counter()
        self.admission_lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(max_requests)
        self.jobs = queue.Queue()
        self.sequence = 0
        self.stopping = False
        self.csv_path = Path(csv_path)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.csv_file = self.csv_path.open("a", newline="", encoding="utf-8")
        self.csv_writer = csv.DictWriter(self.csv_file, fieldnames=CSV_FIELDS)
        if self.csv_path.stat().st_size == 0:
            self.csv_writer.writeheader()
            self.csv_file.flush()
        try:
            super().__init__(address, Handler)
        except Exception:
            self.csv_file.close()
            raise
        self.worker = threading.Thread(target=self.work, name="asr-worker", daemon=True)
        self.worker.start()

    def admit(self, body, client_id, upload_started):
        # Linearization point: receipt timestamp, sequence, and FIFO insertion
        # are one locked operation, before WAV validation or ASR.
        with self.admission_lock:
            if self.stopping:
                return None
            received = time.perf_counter()
            self.sequence += 1
            job = Job(self.sequence, uuid.uuid4().hex[:12], client_id, body,
                      received, received - upload_started)
            logging.info("QUEUED seq=%d client=%s id=%s received=%.3fs bytes=%d",
                         job.sequence, client_id, job.request_id,
                         received - self.origin, len(body))
            self.jobs.put(job)
            return job

    def work(self):
        while True:
            job = self.jobs.get()
            if job is None:
                self.jobs.task_done()
                return
            started = time.perf_counter()
            wait = started - job.received
            logging.info("START  seq=%d client=%s wait=%.3fs",
                         job.sequence, job.client_id, wait)
            result = {"ok": False, "sequence": job.sequence,
                      "request_id": job.request_id, "client_id": job.client_id,
                      "received_at_s": round(job.received - self.origin, 6),
                      "started_at_s": round(started - self.origin, 6),
                      "queue_wait_seconds": round(wait, 6),
                      "upload_receive_seconds": round(job.upload_receive_seconds, 6)}
            try:
                try:
                    rate, duration, pcm = decode_wav(job.body)
                except (wave.Error, EOFError, ValueError) as exc:
                    job.status = 400
                    result["error"] = str(exc) or "Invalid WAV"
                else:
                    asr_started = time.perf_counter()
                    recognizer = self.recognizer_class(self.model, rate)
                    parts = []
                    for offset in range(0, len(pcm), 8000):
                        if recognizer.AcceptWaveform(pcm[offset:offset + 8000]):
                            text = json.loads(recognizer.Result()).get("text", "")
                            if text:
                                parts.append(text)
                    text = json.loads(recognizer.FinalResult()).get("text", "")
                    if text:
                        parts.append(text)
                    elapsed = time.perf_counter() - asr_started
                    result.update(ok=True, text=" ".join(parts), sample_rate=rate,
                                  audio_seconds=round(duration, 6),
                                  asr_seconds=round(elapsed, 6),
                                  rtf=round(elapsed / duration, 6))
                    job.status = 200
            except Exception:
                logging.exception("ASR failed seq=%d", job.sequence)
                job.status = 500
                result["error"] = "Recognition failed; check Server terminal"
            finally:
                finished = time.perf_counter()
                result["finished_at_s"] = round(finished - self.origin, 6)
                logging.info("DONE   seq=%d client=%s status=%d wait=%.3fs asr=%.3fs",
                             job.sequence, job.client_id, job.status, wait,
                             result.get("asr_seconds", 0))
                try:
                    row = {key: result.get(key, "") for key in CSV_FIELDS}
                    row["status"] = job.status
                    self.csv_writer.writerow(row)
                    self.csv_file.flush()
                except OSError:
                    logging.exception("CSV write failed seq=%d", job.sequence)
                    result["log_warning"] = "Server CSV write failed"
                job.body = b""
                job.result = result
                self.slots.release()
                job.done.set()
                self.jobs.task_done()

    def stop_worker(self):
        with self.admission_lock:
            self.stopping = True
            self.jobs.put(None)
        logging.info("Finishing accepted jobs before stopping ASR worker")
        self.worker.join()
        self.csv_file.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "EOS-FCFS/1.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def reply(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except (OSError, ConnectionError):
            logging.warning("Client disconnected while replying")

    def do_GET(self):
        if self.path == "/health":
            self.reply(200, {"ok": True, "mode": "fcfs_single_worker",
                             "model_loaded": True,
                             "waiting_approx": self.server.jobs.qsize()})
        else:
            self.reply(404, {"ok": False, "error": "Use POST /transcribe"})

    def do_POST(self):
        if self.path != "/transcribe":
            self.reply(404, {"ok": False, "error": "Use POST /transcribe"})
            return
        if self.headers.get("Transfer-Encoding"):
            self.reply(400, {"ok": False, "error": "Chunked uploads are not supported"})
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.reply(411, {"ok": False, "error": "Valid Content-Length required"})
            return
        if not 0 < length <= MAX_BYTES:
            self.reply(413 if length > MAX_BYTES else 400,
                       {"ok": False, "error": "Body must be 1 byte to 20 MiB"})
            return
        client_id = self.headers.get("X-Client-ID", "client")
        if not client_id or len(client_id) > 32 or not all(
            c.isascii() and (c.isalnum() or c in "_-") for c in client_id
        ):
            self.reply(400, {"ok": False, "error": "Client ID must be 1-32 ASCII letters/digits/_/-"})
            return
        if not self.server.slots.acquire(blocking=False):
            self.reply(503, {"ok": False, "error": "Server busy: request capacity reached; retry later"})
            return

        # Ownership of the capacity slot moves to the worker only after admission.
        admitted = False
        started = time.perf_counter()
        try:
            try:
                body = self.rfile.read(length)
            except (socket.timeout, ConnectionError):
                self.reply(408, {"ok": False, "error": "Upload timed out or disconnected"})
                return
            if len(body) != length:
                self.reply(400, {"ok": False, "error": "Incomplete upload"})
                return
            job = self.server.admit(body, client_id, started)
            if job is None:
                self.reply(503, {"ok": False, "error": "Server is stopping"})
                return
            admitted = True
            del body
            job.done.wait()
            result = dict(job.result)
            result["server_seconds"] = round(time.perf_counter() - started, 6)
            self.reply(job.status, result)
        finally:
            if not admitted:
                self.server.slots.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="192.168.222.222")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--model", type=Path,
                        default=Path(__file__).resolve().parent / "models/vosk-model-small-en-us-0.15")
    parser.add_argument("--csv", type=Path,
                        default=Path(__file__).resolve().parent / "logs/requests_fcfs.csv")
    parser.add_argument("--max-requests", type=int, default=8,
                        help="Maximum uploading, queued, and processing requests combined")
    args = parser.parse_args()
    if not args.model.is_dir():
        parser.error(f"Model folder not found: {args.model}")
    if args.max_requests < 1:
        parser.error("--max-requests must be positive")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    from vosk import Model, KaldiRecognizer, SetLogLevel
    SetLogLevel(-1)
    logging.info("Loading model once: %s", args.model)
    model = Model(str(args.model))
    server = FCFSServer((args.host, args.port), model, KaldiRecognizer,
                        args.csv, args.max_requests)
    logging.info("READY FCFS http://%s:%s/transcribe; CSV=%s", args.host, args.port, args.csv)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Stopping")
    finally:
        server.server_close()
        server.stop_worker()


if __name__ == "__main__":
    main()
