"""One-time setup for EMAIL_MODE=gmail_api (needed on Render's free plan).

Before running:
  1. https://console.cloud.google.com → create a project → "APIs & Services" → enable "Gmail API".
  2. "OAuth consent screen" → External → add the sender Gmail as a test user → then press "Publish app"
     (while it stays in "Testing", Google expires the token after 7 days).
  3. "Credentials" → Create credentials → OAuth client ID → type "Desktop app".
  4. Put GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET in .env.

Run:  .venv/bin/python scripts/gmail_auth.py
A browser opens: log in with the SENDER Gmail and allow. The refresh token is printed; put it in .env
(and in Render) as GMAIL_REFRESH_TOKEN.
"""
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_settings  # noqa: E402

PORT = 8765
REDIRECT = f"http://127.0.0.1:{PORT}"
SCOPE = "https://www.googleapis.com/auth/gmail.send"
result: dict = {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        result.update({k: v[0] for k, v in qs.items()})
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write("<h2>Done. You can close this tab and go back to the terminal.</h2>".encode())
        threading.Thread(target=self.server.shutdown).start()

    def log_message(self, *args):
        pass


def main():
    s = get_settings()
    if not (s.gmail_client_id and s.gmail_client_secret):
        sys.exit("Set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET in .env first (see the steps at the top of this file).")
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": s.gmail_client_id, "redirect_uri": REDIRECT, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "login_hint": s.gmail_sender,
    })
    print("Opening your browser. If it doesn't open, visit:\n", url)
    webbrowser.open(url)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    if "code" not in result:
        sys.exit(f"Google did not return a code: {result}")
    r = httpx.post("https://oauth2.googleapis.com/token", data={
        "code": result["code"], "client_id": s.gmail_client_id, "client_secret": s.gmail_client_secret,
        "redirect_uri": REDIRECT, "grant_type": "authorization_code",
    })
    data = r.json()
    if "refresh_token" not in data:
        sys.exit(f"No refresh token returned: {data}")
    print("\nAdd this to .env and to Render:\n")
    print(f"GMAIL_REFRESH_TOKEN={data['refresh_token']}")
    print("EMAIL_MODE=gmail_api")


if __name__ == "__main__":
    main()
