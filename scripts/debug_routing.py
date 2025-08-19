#!/usr/bin/env python3
"""
Debugging helper: fetch the published Google Sheets CSV and inspect each row.

For each row this script prints:
- row index
- all headers with values
- chosen title field (title/name or best heuristic)
- whether the chosen title contains Chinese characters
- any detected Spotify track URI/id found in the row values

This is read-only and safe.
"""
import os
import re
import csv
import requests
from io import StringIO
from typing import List, Dict, Optional

# Default to the published Google Sheets CSV as requested by the user.
CSV_PATH = os.getenv(
    "CSV_PATH",
    "https://docs.google.com/spreadsheets/d/e/2PACX-1vTpQ_UJBna7NvW6D6_gUk5DPOUIv5oIhYKhBt1xgqI_PBexAd-W8xYctWB0UwYiEM7crxcv8oqjK9yx/pub?gid=189691109&single=true&output=csv",
)

HEADERS = {"User-Agent": "update-playlists-debug/1.0"}


def contains_chinese(text: Optional[str]) -> bool:
    if not text:
        return False
    return bool(re.search(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]", text))


def extract_track_id_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    m = re.match(r"spotify:track:([A-Za-z0-9]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"open.spotify.com/track/([A-Za-z0-9]+)", url)
    if m:
        return m.group(1)
    if re.match(r"^[A-Za-z0-9]{22}$", url):
        return url
    return None


def normalize_to_uri(maybe_uri: str) -> Optional[str]:
    tid = extract_track_id_from_url(maybe_uri)
    if tid:
        return f"spotify:track:{tid}"
    m = re.match(r"spotify:track:([A-Za-z0-9]+)", maybe_uri)
    if m:
        return maybe_uri
    return None


def read_csv_from_url(url: str) -> List[Dict[str, str]]:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    fh = StringIO(resp.text)
    reader = csv.DictReader(fh)
    rows = []
    for r in reader:
        low = {k.strip().lower(): (v.strip() if v is not None else "") for k, v in r.items() if k is not None}
        rows.append(low)
    return rows


def choose_title(row: Dict[str, str]) -> str:
    # Prefer common keys
    for k in ("title", "name"):
        v = row.get(k)
        if v and v.strip():
            return v.strip()
    # Heuristic: look for header names that contain title-related words (including Chinese labels)
    for k, v in row.items():
        if not v:
            continue
        lk = k.lower()
        if any(term in lk for term in ("title", "name", "標題", "歌名", "歌曲", "song")):
            return v.strip()
    # Fallback: first non-empty value that isn't a spotify url/uri or an obviously long description
    for k, v in row.items():
        if not v:
            continue
        if normalize_to_uri(v):
            continue
        val = v.strip()
        if not val:
            continue
        return val
    return ""


def inspect_rows(rows: List[Dict[str, str]]):
    total = len(rows)
    chinese_count = 0
    non_chinese_count = 0
    resolved_uris = 0
    unresolved = []
    print(f"Read {total} rows\n")
    for i, row in enumerate(rows, start=1):
        print("-" * 70)
        print(f"Row {i}:")
        # Print each column with flags
        for k, v in row.items():
            v_display = v or ""
            flags = []
            if contains_chinese(v_display):
                flags.append("HAS_CJK")
            uri = normalize_to_uri(v_display)
            if uri:
                flags.append(f"URI:{uri}")
            flagstr = (" [" + ",".join(flags) + "]") if flags else ""
            print(f"  {k!r}: {v_display!r}{flagstr}")
        title = choose_title(row)
        is_ch = contains_chinese(title)
        # detect any URIs in row
        uris = []
        for v in row.values():
            if v:
                u = normalize_to_uri(v)
                if u:
                    uris.append(u)
        uris = list(dict.fromkeys(uris))  # unique preserve order
        print(f"\n  Chosen title -> {title!r}")
        print(f"  contains_chinese(title) -> {is_ch}")
        print(f"  Detected URIs in row -> {uris if uris else 'None'}")
        if uris:
            resolved_uris += 1
        if is_ch:
            chinese_count += 1
        else:
            non_chinese_count += 1
        if not uris:
            unresolved.append((i, title, row))
        print()

    print("=" * 70)
    print("Summary:")
    print(f"  Total rows: {total}")
    print(f"  Rows with chosen title containing CJK/Chinese characters: {chinese_count}")
    print(f"  Rows with chosen title NOT containing CJK: {non_chinese_count}")
    print(f"  Rows with at least one detected Spotify URI in values: {resolved_uris}")
    print(f"  Rows with no detected URI: {len(unresolved)}")
    if unresolved:
        print("\nFirst unresolved rows (index, chosen title):")
        for idx, t, _ in unresolved[:10]:
            print(f"  {idx}: {t!r}")


def main():
    print(f"Fetching CSV from: {CSV_PATH}\n")
    try:
        rows = read_csv_from_url(CSV_PATH)
    except Exception as e:
        print("Failed to fetch CSV:", e)
        return
    inspect_rows(rows)


if __name__ == "__main__":
    main()
