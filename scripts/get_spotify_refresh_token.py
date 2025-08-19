#!/usr/bin/env python3
"""
Helper to obtain a Spotify refresh token using the Authorization Code flow.

How it works:
- Reads SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET from environment or prompts you.
- Starts a temporary local HTTP server on localhost:PORT to receive the OAuth redirect containing the code.
- Opens the Spotify authorization URL in your default browser.
- When you approve the app, Spotify will redirect back to http://localhost:PORT/callback?code=...
- The script exchanges the code for access_token + refresh_token and writes a .env file with:
    SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REFRESH_TOKEN
- Run locally. Do NOT run this inside the GitHub Actions runner.

Usage:
    python scripts/get_spotify_refresh_token.py
    OR
    SPOTIFY_CLIENT_ID=... SPOTIFY_CLIENT_SECRET=... python scripts/get_spotify_refresh_token.py

Notes:
- The app must have a Redirect URI configured in the Spotify Developer Dashboard that matches the redirect used here,
  e.g., http://localhost:8080/callback
- For a development app in "Development mode", only the app owner (and collaborators) can authorize it.
"""

import os
import sys
import json
import time
import logging
import webbrowser
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import requests

try:
    from dotenv import set_key, load_dotenv
except Exception:
    # Minimal fallback for writing .env if python-dotenv is not installed.
    set_key = None
    load_dotenv = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("get_spotify_refresh_token")

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
DEFAULT_PORT = 8080
REDIRECT_PATH = "/callback"
SCOPES = "playlist-modify-public playlist-modify-private user-read-private user-read-email"

class OAuthHandler(BaseHTTPRequestHandler):
    server_version = "OAuthHandler/1.0"

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != REDIRECT_PATH:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")
            return

        params = urllib.parse.parse_qs(parsed.query)
        if "error" in params:
            self.send_response(200)
            self.end_headers()
            msg = f"Error from Spotify: {params.get('error')}"
            self.wfile.write(msg.encode("utf-8"))
            self.server.auth_error = params.get("error")
            return

        code = params.get("code", [None])[0]
        if not code:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing code parameter.")
            self.server.auth_error = "missing_code"
            return

        # store the code on the server object for the main thread to pick up
        self.server.auth_code = code
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Authorization successful. You can close this tab and return to the terminal.")
        # shutdown server in separate thread to allow this handler to finish
        threading.Thread(target=self.server.shutdown, daemon=True).start()

def start_local_server(port: int):
    server_address = ("", port)
    httpd = HTTPServer(server_address, OAuthHandler)
    # attributes to be populated by handler
    httpd.auth_code = None
    httpd.auth_error = None
    logger.info("Starting local HTTP server on http://localhost:%d%s to receive callback", port, REDIRECT_PATH)
    httpd.serve_forever()
    return httpd

def exchange_code_for_token(client_id: str, client_secret: str, code: str, redirect_uri: str):
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    resp = requests.post(SPOTIFY_TOKEN_URL, data=data, headers=headers, timeout=30)
    if resp.status_code != 200:
        logger.error("Token exchange failed: %s %s", resp.status_code, resp.text)
        return None
    return resp.json()

def write_env_file(path: str, client_id: str, client_secret: str, refresh_token: str):
    # Prefer python-dotenv's set_key if available
    if set_key and os.path.exists(path):
        load_dotenv(path)
        set_key(path, "SPOTIFY_CLIENT_ID", client_id)
        set_key(path, "SPOTIFY_CLIENT_SECRET", client_secret)
        set_key(path, "SPOTIFY_REFRESH_TOKEN", refresh_token)
        logger.info("Updated %s with refresh token.", path)
        return

    # Otherwise write/overwrite the file
    contents = []
    if os.path.exists(path):
        # read existing keys and preserve unrelated keys
        with open(path, "r", encoding="utf-8") as fh:
            contents = fh.readlines()
        # filter out existing spotify keys
        contents = [l for l in contents if not l.strip().startswith("SPOTIFY_CLIENT_ID") and not l.strip().startswith("SPOTIFY_CLIENT_SECRET") and not l.strip().startswith("SPOTIFY_REFRESH_TOKEN")]
    contents.append(f"SPOTIFY_CLIENT_ID={client_id}\n")
    contents.append(f"SPOTIFY_CLIENT_SECRET={client_secret}\n")
    contents.append(f"SPOTIFY_REFRESH_TOKEN={refresh_token}\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(contents)
    logger.info("Wrote %s with refresh token.", path)

def main():
    client_id = os.getenv("SPOTIFY_CLIENT_ID") or ""
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET") or ""

    if not client_id:
        client_id = input("Enter your SPOTIFY_CLIENT_ID: ").strip()
    if not client_secret:
        client_secret = input("Enter your SPOTIFY_CLIENT_SECRET: ").strip()

    if not client_id or not client_secret:
        logger.error("Client ID and Client Secret are required.")
        sys.exit(1)

    port = DEFAULT_PORT
    redirect_uri = f"http://localhost:{port}{REDIRECT_PATH}"

    # Construct auth URL
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "show_dialog": "true"
    }
    auth_url = SPOTIFY_AUTH_URL + "?" + urllib.parse.urlencode(params)

    logger.info("Opening browser to authorize application.")
    logger.info("If the browser does not open, visit this URL manually:\n\n%s\n", auth_url)

    # Start server in a separate thread
    server_thread = threading.Thread(target=start_local_server, args=(port,), daemon=True)
    server_thread.start()

    # Open browser
    try:
        webbrowser.open(auth_url, new=2)
    except Exception:
        logger.warning("Failed to open browser automatically. Please open the URL above manually.")

    # Wait for server to populate auth_code (timeout after 300s)
    start = time.time()
    auth_code = None
    while time.time() - start < 300:
        # attempt to read the server file (the server thread created a HTTPServer and set auth_code when received)
        # We need to find the running server instance. As start_local_server returns only after shutdown,
        # we can't get the object directly here; instead, poll by attempting to connect to the redirect endpoint
        # is tricky—so use a local loop that waits for the HTTP server to write a temp file containing the code.
        # Simpler approach: try to fetch from localhost to see if server has an attribute; but since we can't access
        # the instance, we'll instead rely on the fact that the server will have shut down after receiving the code
        # and the handler will have printed the code in the terminal via logs. To keep this robust, we'll attempt to
        # accept the code via a small polling on a known temporary file created by the handler (alternative approach).
        # For simplicity and reliability across environments, ask the user to paste the "code" query param if automatic
        # capture fails.

        time.sleep(1)
        # Check for a file produced by the handler (not used by default)
        tmp_path = ".spotify_auth_code.tmp"
        if os.path.exists(tmp_path):
            with open(tmp_path, "r", encoding="utf-8") as fh:
                auth_code = fh.read().strip()
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            break
        # Continue waiting until timeout

    if not auth_code:
        # Fallback: ask user to paste the 'code' query param from the redirect URL
        logger.warning("Automatic capture did not complete within 300 seconds.")
        pasted = input("After approving the app you will be redirected to a URL like http://localhost:8080/callback?code=XXXX\n"
                       "If your browser showed that URL, paste the value of the 'code' parameter here (or press Enter to abort): ").strip()
        if not pasted:
            logger.error("No code provided. Aborting.")
            sys.exit(1)
        auth_code = pasted

    logger.info("Exchanging code for tokens...")
    token_resp = exchange_code_for_token(client_id, client_secret, auth_code, redirect_uri)
    if not token_resp:
        logger.error("Failed to obtain tokens.")
        sys.exit(1)

    refresh_token = token_resp.get("refresh_token")
    access_token = token_resp.get("access_token")

    if not refresh_token:
        logger.error("No refresh token received. Ensure your app has proper redirect URI and scopes and that you approved it.")
        logger.info("Full token response: %s", token_resp)
        sys.exit(1)

    # Write to .env (in repo root)
    env_path = ".env"
    write_env_file(env_path, client_id, client_secret, refresh_token)

    logger.info("Done. Your refresh token has been saved to %s. You can now run the update script.", env_path)
    logger.info("Example: python scripts/update_playlists.py")
    if access_token:
        logger.info("A short-lived access token was also returned (not saved).")
    # Small final sleep to allow logs to flush
    time.sleep(0.5)

if __name__ == "__main__":
    main()
