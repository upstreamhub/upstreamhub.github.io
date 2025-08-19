#!/usr/bin/env python3
"""
Update two Spotify playlists from a CSV file.

Behavior:
- Reads a CSV (path from CSV_PATH env var or ./tracks.csv). If CSV_PATH is an HTTP(S) URL, fetches it.
- Tries to resolve each row to a Spotify track URI (accepts spotify URL/URI/id or searches by title+artist)
- Builds two filtered lists:
    * Playlist 1 (PLAYLIST_ONE_ID) - max 3 songs per artist
    * Playlist 2 (PLAYLIST_TWO_ID) - max 1 song per artist
- Replaces each playlist contents with the filtered tracks (keeps order from CSV)
- Uses one of:
    * SPOTIFY_ACCESS_TOKEN (directly provided, used as-is)
    * OR SPOTIFY_REFRESH_TOKEN + SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET (refresh token flow)
- Loads .env if present (python-dotenv supported)

Important notes:
- You cannot modify user playlists using only Client ID + Client Secret (Client Credentials flow).
  Modifying playlists requires a user-authorized access token (from Authorization Code flow) or a refresh token.
  If you only have client id/secret, create a refresh token by completing the Spotify Authorization Code flow
  and provide SPOTIFY_REFRESH_TOKEN (or supply a currently valid SPOTIFY_ACCESS_TOKEN).
"""

import os
import re
import sys
import time
import logging
import requests
import csv
import random
from urllib.parse import quote_plus
from typing import Optional, List, Dict
from dotenv import load_dotenv
from io import StringIO

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("update_playlists")

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"

# Environment/config
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")
# Optional: user can supply a ready-made short-lived access token (must have playlist-modify scopes)
ACCESS_TOKEN_ENV = os.getenv("SPOTIFY_ACCESS_TOKEN")

CSV_PATH = os.getenv("CSV_PATH", "tracks.csv")
PLAYLIST_ONE_ID = os.getenv("PLAYLIST_ONE_ID", "5TGXCfKbeG0emEZjm6hMRJ")  # max 3 per artist
PLAYLIST_TWO_ID = os.getenv("PLAYLIST_TWO_ID", "5vQfOSbBgybQkWSSrILyr9")  # max 1 per artist

HEADERS = {"User-Agent": "update-playlists-script/1.0"}


def get_access_token_from_refresh(client_id: str, client_secret: str, refresh_token: str) -> str:
    """Exchange a refresh token for an access token.

    IMPORTANT: This function no longer exits the process directly on failure. Instead it raises
    a RuntimeError so callers can attempt a fallback (for example running the interactive helper
    to obtain a new refresh token).
    """
    logger.info("Requesting access token using refresh token.")
    auth = (client_id, client_secret)
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    resp = requests.post(SPOTIFY_TOKEN_URL, data=data, auth=auth, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        # Bubble up the error to the caller instead of exiting the process.
        msg = f"Failed to refresh token: {resp.status_code} {resp.text}"
        logger.error(msg)
        raise RuntimeError(msg)
    token = resp.json().get("access_token")
    if not token:
        msg = f"No access_token in response: {resp.text}"
        logger.error(msg)
        raise RuntimeError(msg)
    return token


def get_access_token() -> str:
    """
    Determine access token to use:
    - If SPOTIFY_ACCESS_TOKEN is provided in env, use it (no refresh).
    - Else if SPOTIFY_REFRESH_TOKEN + client credentials provided, exchange refresh for access token.
    - Else, try to run the bundled interactive helper (if client id/secret present) to obtain a refresh token,
      then exchange it. If that is not possible, exit with an explanatory error.
    """
    if ACCESS_TOKEN_ENV:
        logger.info("Using SPOTIFY_ACCESS_TOKEN provided in environment.")
        return ACCESS_TOKEN_ENV.strip()

    # If we already have a refresh token, try exchanging it but handle failures gracefully.
    if REFRESH_TOKEN and CLIENT_ID and CLIENT_SECRET:
        try:
            return get_access_token_from_refresh(CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN)
        except RuntimeError as e:
            logger.warning("Refresh token exchange failed: %s", e)
            # fall through to attempt the interactive helper if possible

    # If we have client id/secret but no valid refresh token, offer an HTTPS-friendly interactive flow:
    # Many apps register an HTTPS redirect (for example https://upstreamhub.github.io/). Instead of starting a
    # local HTTP server (which requires an http:// redirect), open the authorization URL pointing at a registered
    # HTTPS redirect and ask the user to paste the "code" parameter from the browser's redirected URL. This works
    # when you have control of the app's Redirect URIs (you showed https://upstreamhub.github.io/ is registered).
    if CLIENT_ID and CLIENT_SECRET:
        # In CI environments (for example GitHub Actions) interactive authorization is not available.
        # Detect CI and abort early with a clear error instead of attempting to open a browser or read stdin,
        # which would cause the workflow to hang.
        if os.getenv("GITHUB_ACTIONS") or os.getenv("CI"):
            logger.error(
                "Running in CI (GITHUB_ACTIONS/CI detected) and no valid SPOTIFY_REFRESH_TOKEN or SPOTIFY_ACCESS_TOKEN is available."
            )
            logger.error(
                "Set SPOTIFY_REFRESH_TOKEN or SPOTIFY_ACCESS_TOKEN in your repository's Secrets and re-run the workflow."
            )
            raise SystemExit(2)
        logger.info("No valid refresh token; initiating interactive authorization using a registered HTTPS redirect.")
        try:
            import webbrowser
            redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "https://upstreamhub.github.io/")
            scopes = "playlist-modify-public playlist-modify-private user-read-private user-read-email"
            params = (
                f"client_id={CLIENT_ID}"
                f"&response_type=code"
                f"&redirect_uri={quote_plus(redirect_uri)}"
                f"&scope={quote_plus(scopes)}"
                f"&show_dialog=true"
            )
            auth_url = f"https://accounts.spotify.com/authorize?{params}"
            logger.info("Opening browser to authorize application. If it doesn't open, visit this URL manually:\n\n%s", auth_url)
            try:
                webbrowser.open(auth_url, new=2)
            except Exception:
                logger.warning("Could not open browser automatically. Please open the URL above manually in your browser.")

            # Ask user to paste the code from the redirect URL.
            prompt = (
                "After approving the app you'll be redirected to a URL starting with\n"
                f"{redirect_uri}\n\n"
                "Copy the value of the 'code' query parameter from that URL and paste it here (or press Enter to abort): "
            )
            code = input(prompt).strip()
            if not code:
                logger.error("No authorization code provided. Aborting interactive authorization.")
            else:
                # Exchange the code for tokens
                data = {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                }
                headers = {"Content-Type": "application/x-www-form-urlencoded"}
                resp = requests.post(SPOTIFY_TOKEN_URL, data=data, headers=headers, timeout=30)
                if resp.status_code != 200:
                    logger.error("Token exchange failed: %s %s", resp.status_code, resp.text)
                else:
                    token_resp = resp.json()
                    refresh = token_resp.get("refresh_token")
                    if not refresh:
                        logger.error("No refresh token returned. Response: %s", token_resp)
                    else:
                        # Write refresh token (and client creds) to .env, preserving unrelated keys.
                        env_path = ".env"
                        contents = []
                        if os.path.exists(env_path):
                            with open(env_path, "r", encoding="utf-8") as fh:
                                contents = fh.readlines()
                            contents = [l for l in contents if not l.strip().startswith("SPOTIFY_CLIENT_ID") and not l.strip().startswith("SPOTIFY_CLIENT_SECRET") and not l.strip().startswith("SPOTIFY_REFRESH_TOKEN")]
                        contents.append(f"SPOTIFY_CLIENT_ID={CLIENT_ID}\n")
                        contents.append(f"SPOTIFY_CLIENT_SECRET={CLIENT_SECRET}\n")
                        contents.append(f"SPOTIFY_REFRESH_TOKEN={refresh}\n")
                        with open(env_path, "w", encoding="utf-8") as fh:
                            fh.writelines(contents)
                        logger.info("Saved refresh token to %s. Exchanging for access token now.", env_path)
                        # reload env and exchange refresh for access token
                        load_dotenv()
                        try:
                            return get_access_token_from_refresh(CLIENT_ID, CLIENT_SECRET, refresh)
                        except RuntimeError as e:
                            logger.error("Exchange after interactive auth failed: %s", e)
        except Exception as e:
            logger.exception("Unexpected error during interactive authorization: %s", e)

    logger.error(
        "No SPOTIFY_ACCESS_TOKEN or SPOTIFY_REFRESH_TOKEN available. "
        "Client ID & Secret alone are insufficient to modify playlists.\n\n"
        "Options:\n"
        " 1) Run the interactive helper to obtain SPOTIFY_REFRESH_TOKEN: python scripts/get_spotify_refresh_token.py\n"
        " 2) Provide a valid SPOTIFY_ACCESS_TOKEN (short-lived) via SPOTIFY_ACCESS_TOKEN env var.\n\n"
        "If you prefer, run the helper locally (it will open a browser tab to ask you to sign in and authorize the app),\n"
        "or paste a refresh token into your .env as SPOTIFY_REFRESH_TOKEN.\n"
    )
    raise SystemExit(2)


def extract_track_id_from_url(url: str) -> Optional[str]:
    """Extract Spotify track id from various formats."""
    if not url:
        return None
    # spotify:track:{id}
    m = re.match(r"spotify:track:([A-Za-z0-9]+)", url)
    if m:
        return m.group(1)
    # https://open.spotify.com/track/{id}
    m = re.search(r"open.spotify.com/track/([A-Za-z0-9]+)", url)
    if m:
        return m.group(1)
    # possible plain id (22 chars)
    if re.match(r"^[A-Za-z0-9]{22}$", url):
        return url
    return None


def normalize_to_uri(maybe_uri: Optional[str]) -> Optional[str]:
    """Return spotify:track:{id} style URI given input."""
    if not maybe_uri:
        return None
    tid = extract_track_id_from_url(maybe_uri)
    if tid:
        return f"spotify:track:{tid}"
    # spotify:track:{id}
    m = re.match(r"spotify:track:([A-Za-z0-9]+)", maybe_uri)
    if m:
        return maybe_uri
    return None


def search_track(access_token: str, title: str, artist: Optional[str] = None) -> Optional[str]:
    """Search Spotify for a track and return its uri (first best match)."""
    q = ""
    if title:
        q += f'track:{title}'
    if artist:
        q += f' artist:{artist}'
    q = quote_plus(q)
    url = f"{SPOTIFY_API_BASE}/search?q={q}&type=track&limit=1"
    headers = {"Authorization": f"Bearer {access_token}", **HEADERS}
    resp = requests.get(url, headers=headers, timeout=30)
    if resp.status_code == 200:
        items = resp.json().get("tracks", {}).get("items", [])
        if items:
            return items[0]["uri"]
    else:
        logger.warning("Search failed (%s): %s", resp.status_code, resp.text)
    return None


def read_csv(path: str) -> List[Dict[str, str]]:
    """
    Read CSV from local path or an http(s) URL.
    Returns list of rows as dicts with lowercase keys.
    """
    rows = []
    if path.lower().startswith("http://") or path.lower().startswith("https://"):
        logger.info("Fetching CSV from URL: %s", path)
        resp = requests.get(path, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            logger.error("Failed to fetch CSV URL %s: %s", path, resp.status_code)
            raise SystemExit(5)
        text = resp.text
        fh = StringIO(text)
        reader = csv.DictReader(fh)
        for r in reader:
            low = {k.strip().lower(): (v.strip() if v is not None else "") for k, v in r.items() if k is not None}
            rows.append(low)
    else:
        if not os.path.exists(path):
            logger.error("CSV file not found at: %s", path)
            raise SystemExit(5)
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for r in reader:
                low = {k.strip().lower(): (v.strip() if v is not None else "") for k, v in r.items() if k is not None}
                rows.append(low)
    logger.info("Read %d rows from CSV", len(rows))
    return rows


def resolve_row_to_uri(access_token: str, row: Dict[str, str]) -> Optional[str]:
    # First, try scanning all values in the row for a Spotify track URL/URI/id.
    # This makes the function tolerant to CSVs with localized or unexpected header names.
    for v in row.values():
        if v:
            uri = normalize_to_uri(v)
            if uri:
                return uri

    # look for well-known field names (backwards compatible)
    for key in ("spotify_uri", "uri", "track_uri"):
        if key in row and row[key]:
            uri = normalize_to_uri(row[key])
            if uri:
                return uri
    for key in ("spotify_url", "url", "track_url"):
        if key in row and row[key]:
            uri = normalize_to_uri(row[key])
            if uri:
                return uri
    for key in ("id", "track_id"):
        if key in row and row[key]:
            maybe = row[key]
            uri = normalize_to_uri(maybe)
            if uri:
                return uri
            # if it's just id, convert
            if re.match(r"^[A-Za-z0-9]{22}$", maybe):
                return f"spotify:track:{maybe}"

    # fallback to search if title (or name) present
    title = row.get("title") or row.get("name")
    artist = row.get("artist")
    if title:
        found = search_track(access_token, title, artist)
        if found:
            return found

    return None


def partition_by_artist_limit(uris: List[str], access_token: str, max_per_artist: int) -> List[str]:
    """
    Given list of track URIs in order, enforce max_per_artist by resolving track->artist (first artist)
    and including at most max_per_artist per artist.
    """
    result = []
    counts = {}
    headers = {"Authorization": f"Bearer {access_token}", **HEADERS}
    # We can batch-get track objects (max 50 per request)
    def chunks(lst, n):
        for i in range(0, len(lst), n):
            yield lst[i:i+n]

    for chunk in chunks(uris, 50):
        ids = ",".join([u.split(":")[-1] for u in chunk])
        url = f"{SPOTIFY_API_BASE}/tracks?ids={ids}"
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            logger.warning("Failed to fetch track metadata: %s %s", resp.status_code, resp.text)
            # if failed, be conservative and skip metadata for this chunk
            continue
        items = resp.json().get("tracks", [])
        for item in items:
            if not item:
                continue
            uri = item["uri"]
            artists = item.get("artists", [])
            artist_name = artists[0]["name"] if artists else "Unknown"
            cur = counts.get(artist_name, 0)
            if cur < max_per_artist:
                result.append(uri)
                counts[artist_name] = cur + 1
            else:
                logger.debug("Skipping %s by %s due to artist limit (%d)", item.get("name"), artist_name, max_per_artist)
    return result


def clear_playlist(access_token: str, playlist_id: str):
    """Clear playlist by replacing with empty list."""
    url = f"{SPOTIFY_API_BASE}/playlists/{playlist_id}/tracks"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json", **HEADERS}
    resp = requests.put(url, headers=headers, json={"uris": []}, timeout=30)
    if resp.status_code not in (200, 201, 204):
        logger.warning("Failed to clear playlist %s: %s %s", playlist_id, resp.status_code, resp.text)


def add_tracks_in_batches(access_token: str, playlist_id: str, uris: List[str]):
    """Add tracks to playlist in batches of 100 (Spotify API limit)."""
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json", **HEADERS}
    for i in range(0, len(uris), 100):
        batch = uris[i:i+100]
        url = f"{SPOTIFY_API_BASE}/playlists/{playlist_id}/tracks"
        resp = requests.post(url, headers=headers, json={"uris": batch}, timeout=30)
        if resp.status_code == 429:
            retry = int(resp.headers.get("Retry-After", "5"))
            logger.warning("Rate limited. Sleeping %s seconds", retry)
            time.sleep(retry + 1)
            # retry once
            resp = requests.post(url, headers=headers, json={"uris": batch}, timeout=30)
        if resp.status_code not in (200, 201):
            logger.error("Failed to add tracks to %s: %s %s", playlist_id, resp.status_code, resp.text)
            raise SystemExit(6)


def main():
    access_token = get_access_token()
    rows = read_csv(CSV_PATH)

    # Resolve all rows to URIs preserving order
    resolved_uris = []
    unresolved = []
    for idx, row in enumerate(rows):
        uri = resolve_row_to_uri(access_token, row)
        if uri:
            resolved_uris.append(uri)
        else:
            unresolved.append((idx + 1, row))
    logger.info("Resolved %d tracks, %d unresolved", len(resolved_uris), len(unresolved))
    if unresolved:
        logger.info("Unresolved rows (first 10 shown): %s", unresolved[:10])

    # Remove duplicates while preserving order
    seen = set()
    unique_uris = []
    for u in resolved_uris:
        if u not in seen:
            unique_uris.append(u)
            seen.add(u)

    # Build per-playlist lists with artist caps
    playlist1_uris = partition_by_artist_limit(unique_uris, access_token, max_per_artist=3)
    playlist2_uris = partition_by_artist_limit(unique_uris, access_token, max_per_artist=1)

    # Randomize order in each playlist as requested
    try:
        random.shuffle(playlist1_uris)
        random.shuffle(playlist2_uris)
    except Exception:
        # If shuffle fails for any reason, fall back to original ordering
        logger.warning("Randomization failed; proceeding with original ordering.")

    logger.info("Final counts -> Playlist1: %d tracks, Playlist2: %d tracks", len(playlist1_uris), len(playlist2_uris))

    # Replace contents: clear then add in batches
    logger.info("Updating playlist %s", PLAYLIST_ONE_ID)
    clear_playlist(access_token, PLAYLIST_ONE_ID)
    if playlist1_uris:
        add_tracks_in_batches(access_token, PLAYLIST_ONE_ID, playlist1_uris)

    logger.info("Updating playlist %s", PLAYLIST_TWO_ID)
    clear_playlist(access_token, PLAYLIST_TWO_ID)
    if playlist2_uris:
        add_tracks_in_batches(access_token, PLAYLIST_TWO_ID, playlist2_uris)

    logger.info("Playlists updated successfully.")


if __name__ == "__main__":
    main()
