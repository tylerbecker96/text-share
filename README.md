# TextShare

Ephemeral shared text editor / notepad server written in pure Python 3 (stdlib only).

## Features

- Clean full-page text editor UI in the browser
- Auto-save + polling so multiple clients stay roughly in sync
- Single temporary file (`/tmp/textshare-<pid>.txt`) that is **deleted when the server exits**
- `/raw` and `/download` endpoints
- Optional native window via `pywebview`

## Run

```bash
python3 textshare.py                  # http://0.0.0.0:8765
python3 textshare.py --port 9000
python3 textshare.py --host 127.0.0.1
python3 textshare.py --webview        # needs: pip install pywebview
```

Open the printed URL on any device on the same network. Type. Share the link. When you stop the server the temp file disappears.
