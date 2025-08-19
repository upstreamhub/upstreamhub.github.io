# Spotify Playlist Updater — Setup & Run

This repository contains a GitHub Action + Python scripts to update two Spotify playlists from a CSV (Google Sheets CSV by default). I completed the implementation and added a helper to obtain a Spotify refresh token. Below are clear, step-by-step instructions so you can run everything locally or via GitHub Actions.

Repository files of interest
- .github/workflows/update_spotify_playlists.yml — GitHub Action that runs the updater (CSV default set to the provided Google Sheets CSV URL)
- scripts/update_playlists.py — Main script that reads CSV, resolves tracks, and updates playlists
- scripts/get_spotify_refresh_token.py — Helper to obtain a SPOTIFY_REFRESH_TOKEN via the Authorization Code flow (run locally)
- requirements.txt — Python dependencies
- .env.example — env template

Status (what's already done for you)
- Workflow created and configured to use the Google Sheets CSV URL by default.
- scripts/update_playlists.py implemented and supports:
  - reading CSV from a local file or HTTP(S) URL
  - resolving spotify URIs/URLs/ids or searching by title + artist
  - enforcing per-artist caps (Playlist 1: max 3; Playlist 2: max 1)
  - using either SPOTIFY_ACCESS_TOKEN (short-lived) or SPOTIFY_REFRESH_TOKEN + CLIENT_ID + CLIENT_SECRET
- Helper script to obtain SPOTIFY_REFRESH_TOKEN added (scripts/get_spotify_refresh_token.py)

Checklist
- [x] Analyze requirements
- [x] Create workflow file
- [x] Implement Python script
- [x] Create requirements.txt and .env.example
- [x] Create helper to obtain refresh token
- [x] Update workflow CSV_PATH to Google Sheets CSV URL
- [x] Obtain refresh token locally (you indicated this is done)
- [ ] Test locally: run update script and confirm playlists updated
- [ ] Run workflow in GitHub Actions / Verify results

Quick decisions you already made
- CSV source: https://docs.google.com/spreadsheets/d/e/2PACX-1vTpQ_UJBna7NvW6D6_gUk5DPOUIv5oIhYKhBt1xgqI_PBexAd-W8xYctWB0UwYiEM7crxcv8oqjK9yx/pub?gid=189691109&single=true&output=csv
- Playlists:
  - Playlist 1 (max 3 per artist): 5TGXCfKbeG0emEZjm6hMRJ
  - Playlist 2 (max 1 per artist): 5vQfOSbBgybQkWSSrILyr9

Local run — recommended (step-by-step)
1. Install Python dependencies:
   - Windows PowerShell:
     python -m pip install --upgrade pip
     python -m pip install -r requirements.txt
   - macOS / Linux (bash/zsh):
     python3 -m pip install --upgrade pip
     python3 -m pip install -r requirements.txt

2. Obtain refresh token (only needed once; you said you already ran this helper and wrote to .env)
   - Ensure your Spotify app has a Redirect URI set to: http://localhost:8080/callback
   - Run locally (do NOT run in GitHub Actions):
     python scripts/get_spotify_refresh_token.py
   - This will open a browser to authorize the app, then save SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET and SPOTIFY_REFRESH_TOKEN to `.env`

   If helper doesn't work, you can follow the manual flow in Spotify Developer docs (but helper automates it).

3. Confirm `.env` or environment variables:
   - The updater script supports:
     - SPOTIFY_REFRESH_TOKEN + SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET (recommended for non-interactive runs)
     - OR SPOTIFY_ACCESS_TOKEN (a short-lived token — useful for testing)
   - Example .env (do NOT commit real secrets):
     SPOTIFY_CLIENT_ID=...
     SPOTIFY_CLIENT_SECRET=...
     SPOTIFY_REFRESH_TOKEN=...

4. Run the updater locally:
   - Windows PowerShell:
     python scripts/update_playlists.py
   - macOS / Linux:
     python3 scripts/update_playlists.py

   The script will:
   - fetch the CSV (from the Google Sheets URL by default)
   - resolve tracks (URI or search)
   - enforce artist caps
   - clear then replace the two playlists with the selected tracks

5. Check output: the script logs how many tracks were resolved and shows final counts for each playlist. Verify the playlists on Spotify.

Setup for GitHub Actions (if you want to run in CI)
1. Add repository secrets (Settings → Secrets → Actions):
   - SPOTIFY_CLIENT_ID
   - SPOTIFY_CLIENT_SECRET
   - SPOTIFY_REFRESH_TOKEN
   (Optionally SPOTIFY_ACCESS_TOKEN, CSV_PATH if you want to override)

2. The workflow is manual-triggerable (Actions → Update Spotify Playlists → Run workflow). It will default to the Google Sheets CSV URL but allows you to pass a csv_path input when running manually.

Troubleshooting & notes
- The Client Credentials flow (client id + secret only) cannot modify playlists — you must use a user-authorized token (refresh token or access token).
- If you get INVALID_CLIENT on auth, make sure the Redirect URI registered in your Spotify app exactly matches the redirect the helper uses (http://localhost:8080/callback). After adding that redirect URI in the Spotify Developer Dashboard, re-run the helper.
- Rate limiting: the script does basic handling for HTTP 429 (wait then retry one time). For heavy usage add exponential backoff.
- CSV format: The script accepts columns (case-insensitive):
  - spotify_uri / uri / track_uri
  - spotify_url / url / track_url
  - id / track_id (22-char Spotify id)
  - title or name and artist (will search Spotify)
- Do NOT commit `.env` with real tokens. Use GitHub Secrets for Actions.

If you want, I can:
- Run the updater in the GitHub Actions workflow for you (requires you to add the secrets to the repo).
- Run the updater locally in this environment (I cannot run interactive browser flows here; the helper must be run on your machine to authorize the app).
- Add a small script that prints the exact Authorization URL and a curl example for manual token exchange (no browser automation).

Next step (pick one)
- [ ] I will add my refresh token as a GitHub secret and you run the workflow
- [ ] I will run the updater locally (I have everything set) — tell me the exact command to run
- [ ] Add a README with manual token-exchange curl example instead of the helper (I will do exchange myself)

If you want me to act now, tell me which of the three items above to perform and I will proceed.
