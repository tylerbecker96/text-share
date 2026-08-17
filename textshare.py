#!/usr/bin/env python3
"""
TextShare — ephemeral shared text editor server.

- Runs a local HTTP server with a clean text-editor UI.
- All content is stored in a single temporary file that is deleted when the
  server exits.
- Anyone on the same network can open the URL and edit/share the text.
- Optional: open a native window with pywebview (if installed).

Usage:
    python3 textshare.py                  # http://0.0.0.0:8765
    python3 textshare.py --port 9000
    python3 textshare.py --host 127.0.0.1 --webview
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import signal
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------------------
# Temporary file (lives only while the process is alive)
# ---------------------------------------------------------------------------

TMP_DIR = Path(tempfile.gettempdir())
TMP_FILE = TMP_DIR / f"textshare-{os.getpid()}.txt"
TMP_FILE.touch(exist_ok=True)


def _cleanup() -> None:
    try:
        if TMP_FILE.exists():
            TMP_FILE.unlink()
            print(f"\n[cleanup] removed {TMP_FILE}", flush=True)
    except OSError:
        pass


atexit.register(_cleanup)


def _handle_signal(signum, frame):
    _cleanup()
    sys.exit(0)


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

# ---------------------------------------------------------------------------
# HTML UI (single-page text editor)
# ---------------------------------------------------------------------------

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TextShare</title>
<style>
  :root {
    --bg: #0f1115;
    --panel: #1a1d24;
    --border: #2a2f3a;
    --text: #e6e9ef;
    --muted: #8b93a7;
    --accent: #6c9eff;
    --accent-dim: #3d5a99;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body {
    height: 100%;
    background: var(--bg);
    color: var(--text);
    font-family: "SF Mono", "JetBrains Mono", "Fira Code", "Cascadia Code",
                 ui-monospace, Menlo, Consolas, monospace;
    font-size: 15px;
    line-height: 1.5;
  }
  body {
    display: flex;
    flex-direction: column;
    height: 100vh;
  }
  header {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 16px;
    background: var(--panel);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  header h1 {
    font-size: 14px;
    font-weight: 600;
    letter-spacing: 0.02em;
    color: var(--muted);
  }
  header h1 span { color: var(--accent); }
  .status {
    margin-left: auto;
    font-size: 12px;
    color: var(--muted);
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--muted);
  }
  .dot.ok { background: #3ecf8e; }
  .dot.err { background: #ff6b6b; }
  .actions {
    display: flex;
    gap: 8px;
  }
  button, .btn {
    background: var(--border);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 5px 12px;
    font-size: 12px;
    font-family: inherit;
    cursor: pointer;
    text-decoration: none;
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  button:hover, .btn:hover {
    background: var(--accent-dim);
    border-color: var(--accent);
  }
  button:active { transform: scale(0.97); }
  main {
    flex: 1;
    display: flex;
    min-height: 0;
  }
  #editor {
    width: 100%;
    height: 100%;
    background: var(--bg);
    color: var(--text);
    border: none;
    outline: none;
    resize: none;
    padding: 20px 24px;
    font-family: inherit;
    font-size: 14.5px;
    line-height: 1.55;
    tab-size: 4;
  }
  #editor::placeholder { color: var(--muted); }
  footer {
    padding: 6px 16px;
    font-size: 11px;
    color: var(--muted);
    background: var(--panel);
    border-top: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    flex-shrink: 0;
  }
  @media (max-width: 600px) {
    header { flex-wrap: wrap; }
    .status { margin-left: 0; width: 100%; }
  }
</style>
</head>
<body>
  <header>
    <h1><span>TextShare</span> · ephemeral notepad</h1>
    <div class="status">
      <span class="dot" id="dot"></span>
      <span id="status">connecting…</span>
    </div>
    <div class="actions">
      <button id="btn-save" title="Force save (Ctrl+S)">Save</button>
      <a class="btn" id="btn-raw" href="/raw" target="_blank" title="View raw text">Raw</a>
      <a class="btn" id="btn-dl" href="/download" download="shared.txt" title="Download as file">Download</a>
    </div>
  </header>
  <main>
    <textarea id="editor" placeholder="Start typing… content is shared and saved automatically." spellcheck="false"></textarea>
  </main>
  <footer>
    <span id="meta">chars: 0 · lines: 1</span>
    <span>tmp file lives only while server is up</span>
  </footer>

<script>
(() => {
  const editor = document.getElementById('editor');
  const statusEl = document.getElementById('status');
  const dot = document.getElementById('dot');
  const meta = document.getElementById('meta');
  let lastSaved = '';
  let dirty = false;
  let saveTimer = null;
  const POLL_MS = 1500;
  const DEBOUNCE_MS = 400;

  function setStatus(msg, ok) {
    statusEl.textContent = msg;
    dot.className = 'dot ' + (ok === true ? 'ok' : ok === false ? 'err' : '');
  }

  function updateMeta() {
    const t = editor.value;
    const lines = t ? t.split('\n').length : 1;
    meta.textContent = `chars: ${t.length} · lines: ${lines}`;
  }

  async function load() {
    try {
      const r = await fetch('/api/content');
      if (!r.ok) throw new Error(r.status);
      const data = await r.json();
      if (data.content !== lastSaved && !dirty) {
        const pos = editor.selectionStart;
        editor.value = data.content;
        editor.setSelectionRange(pos, pos);
        lastSaved = data.content;
      }
      setStatus('synced', true);
    } catch (e) {
      setStatus('offline', false);
    }
    updateMeta();
  }

  async function save(force = false) {
    const content = editor.value;
    if (!force && content === lastSaved) return;
    try {
      const r = await fetch('/api/content', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content })
      });
      if (!r.ok) throw new Error(r.status);
      lastSaved = content;
      dirty = false;
      setStatus('saved ' + new Date().toLocaleTimeString(), true);
    } catch (e) {
      setStatus('save failed', false);
    }
  }

  function scheduleSave() {
    dirty = true;
    setStatus('editing…', null);
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => save(), DEBOUNCE_MS);
  }

  editor.addEventListener('input', () => {
    updateMeta();
    scheduleSave();
  });

  document.getElementById('btn-save').addEventListener('click', () => save(true));

  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
      e.preventDefault();
      save(true);
    }
  });

  // Initial load + poll for remote changes
  load();
  setInterval(load, POLL_MS);

  // Keep focus
  editor.focus();
})();
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "TextShare/1.0"

    def log_message(self, fmt, *args):
        # quieter logs
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, content_type: str = "text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path

        if path in ("/", "/index.html"):
            self._send(200, HTML.encode("utf-8"))
            return

        if path == "/api/content":
            try:
                content = TMP_FILE.read_text(encoding="utf-8")
            except OSError:
                content = ""
            payload = json.dumps({"content": content}).encode("utf-8")
            self._send(200, payload, "application/json; charset=utf-8")
            return

        if path == "/raw":
            try:
                content = TMP_FILE.read_text(encoding="utf-8")
            except OSError:
                content = ""
            self._send(200, content.encode("utf-8"), "text/plain; charset=utf-8")
            return

        if path == "/download":
            try:
                content = TMP_FILE.read_text(encoding="utf-8")
            except OSError:
                content = ""
            body = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="shared.txt"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self._send(404, b"Not Found")

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/content":
            self._send(404, b"Not Found")
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            content = data.get("content", "")
            if not isinstance(content, str):
                raise ValueError("content must be string")
        except (json.JSONDecodeError, ValueError) as e:
            self._send(400, str(e).encode("utf-8"), "text/plain")
            return

        try:
            TMP_FILE.write_text(content, encoding="utf-8")
        except OSError as e:
            self._send(500, str(e).encode("utf-8"), "text/plain")
            return

        self._send(200, b'{"ok":true}', "application/json")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Ephemeral text-sharing server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8765, help="Port (default 8765)")
    parser.add_argument(
        "--webview",
        action="store_true",
        help="Open a native window with pywebview (optional dependency)",
    )
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host if args.host != '0.0.0.0' else '127.0.0.1'}:{args.port}"

    print(f"TextShare running at {url}")
    print(f"Temp file: {TMP_FILE}")
    print("Press Ctrl+C to stop (file will be deleted)\n")

    if args.webview:
        try:
            import webview  # type: ignore
        except ImportError:
            print("pywebview not installed. Run: pip install pywebview", file=sys.stderr)
            print("Falling back to browser-only mode.")
        else:
            def _serve():
                server.serve_forever()

            t = threading.Thread(target=_serve, daemon=True)
            t.start()
            webview.create_window("TextShare", url, width=900, height=700)
            webview.start()
            server.shutdown()
            return

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        _cleanup()


if __name__ == "__main__":
    main()
