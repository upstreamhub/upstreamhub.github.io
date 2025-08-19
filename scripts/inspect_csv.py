import csv
import re
import sys

path = "tracks.csv"
pattern = re.compile(r"open\.spotify\.com/track/([A-Za-z0-9]+)")

print("Checking", path)
try:
    with open(path, "r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for idx, row in enumerate(reader, start=1):
            vals = list(row.values())
            print(f"Row {idx}:")
            for v in vals:
                display = (v or "")[:300]
                print("  Value:", display)
                if v:
                    m = pattern.search(v)
                    print("   regex match:", m.group(1) if m else None)
            print("---")
except FileNotFoundError:
    print("File not found:", path)
    sys.exit(1)
