"""Authenticated HTTP ranges. Never log credentials or signed redirect URLs."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import requests

REPO = "KIT-MRT/KITScenes-Multimodal"


def token():
    p = os.environ.get("HF_TOKEN_FILE")
    return Path(p).read_text().strip() if p else os.environ.get("HF_TOKEN", "")


def session():
    s = requests.Session()
    if t := token():
        s.headers["Authorization"] = "Bearer " + t
    s.headers["Accept-Encoding"] = "identity"
    # requests removes Authorization on cross-host redirects.
    return s


def resolve_url(revision, path):
    return f"https://huggingface.co/datasets/{REPO}/resolve/{revision}/{path}"


class RemoteTar:
    def __init__(self, url, size, method="python", on_bytes=lambda n: None):
        self.session = session()
        self.url, self.size, self.method = url, size, method
        self.on_bytes = on_bytes
        self.direct = None
        self.data_session = requests.Session()
        self.buffer, self.start = b"", 0

    def close(self):
        self.session.close()
        self.data_session.close()

    def resolve(self):
        # Follow HF's authenticated redirect once; never send HF token to CDN.
        with self.session.get(self.url, headers={"Range": "bytes=0-511"}, stream=True, timeout=(20, 90)) as r:
            r.raise_for_status()
            self._validate(r.status_code, r.headers, 0, 511)
            self.direct = r.url
            b = r.raw.read(512)
            if len(b) != 512:
                raise IOError("Truncated initial tar header")
            self.on_bytes(len(b))
            self.start, self.buffer = 0, b

    def _validate(self, status, headers, lo, hi):
        expected = f"bytes {lo}-{hi}/{self.size}"
        if status != 206 or headers.get("Content-Range") != expected:
            raise IOError(f"Server did not honor exact HTTP range ({status}); refusing full transfer")

    def get(self, lo, length, method=None):
        if not length:
            return b""
        hi = lo + length - 1
        if lo < 0 or hi >= self.size:
            raise ValueError("Range outside archive")
        for attempt in range(6):
            try:
                if self.direct is None:
                    self.resolve()
                chosen = method or self.method
                if chosen == "python":
                    # The direct URL has a short-lived signature. No bearer header.
                    with self.data_session.get(self.direct, headers={"Range": f"bytes={lo}-{hi}", "Accept-Encoding": "identity"}, stream=True, timeout=(20, 120)) as r:
                        self._validate(r.status_code, r.headers, lo, hi)
                        b = r.content
                else:
                    b = self._external(chosen, lo, hi)
                if len(b) != length:
                    raise IOError("Truncated HTTP range")
                self.on_bytes(len(b))
                return b
            except (requests.RequestException, IOError, subprocess.SubprocessError):
                self.direct = None
                if attempt == 5:
                    raise IOError(f"Range transfer failed after 6 attempts at offset {lo}") from None
                time.sleep(min(30, 2 ** attempt))
        raise AssertionError

    def _external(self, method, lo, hi):
        # Signed URLs pass via private stdin/config, never argv or logs.
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "body"
            if method == "curl":
                cfg = f'url = "{self.direct}"\nrange = "{lo}-{hi}"\noutput = "{out}"\n'
                p = subprocess.run(["curl", "--config", "-", "--silent", "--fail", "--max-time", "140", "--write-out", "%{http_code}"], input=cfg.encode(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=150)
                if p.returncode or p.stdout != b"206":
                    raise IOError("curl range failed")
            elif method == "wget":
                p = subprocess.run(["wget", "--no-verbose", "--server-response", "--timeout=120", "--tries=1", f"--start-pos={lo}", f"--header=Range: bytes={lo}-{hi}", "-O", str(out), "-i", "-"], input=(self.direct + "\n").encode(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=150)
                # wget exit 0 is insufficient: a server can ignore Range.
                hdr = p.stderr.decode(errors="replace")
                if p.returncode or f"bytes {lo}-{hi}/{self.size}" not in hdr:
                    raise IOError("wget range failed")
            else:
                raise ValueError(f"Unknown transport {method}")
            if out.stat().st_size != hi - lo + 1:
                raise IOError("Invalid range body length")
            return out.read_bytes()

    def read_at(self, offset, length):
        if self.start <= offset and offset + length <= self.start + len(self.buffer):
            return self.buffer[offset-self.start:offset-self.start+length]
        # Headers only: never read speculative blocks containing excluded payloads.
        fetch_length = 513 if length == 1 and offset % 512 == 511 and offset + 513 <= self.size else length
        b = self.get(offset, fetch_length, method="python")
        self.start, self.buffer = offset, b
        return b[:length]
