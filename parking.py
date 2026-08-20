#!/usr/bin/env python3
"""
SJSU parking garage fullness logger.

    python parking.py collect          # poll every 2 min until you Ctrl-C
    python parking.py collect --once   # single sample (use this from cron)
    python parking.py plot             # render parking.png from parking.csv

Collection is stdlib-only, so it runs with zero installs.
Plotting needs matplotlib (pip install matplotlib) -- or just open the CSV in
Sheets and chart it there.
"""

import argparse
import csv
import os
import re
import sys
import time
import urllib.request
from datetime import datetime

URL = "https://sjsuparkingstatus.sjsu.edu/"
CSV_PATH = "parking.csv"
PNG_PATH = "parking.png"
INTERVAL = 120  # seconds between polls
FIELDS = ["fetched_at", "source_updated", "garage", "percent"]

GARAGES = [
    "North Garage",
    "West Garage",
    "South Garage",
    "South Campus Garage",
]

# Identify yourself. It's a public page and this is polite, low-rate traffic.
HEADERS = {
    "User-Agent": "sjsu-parking-logger/1.0 (student research; contact: you@example.com)"
}


# ---------------------------------------------------------------- fetch/parse

def fetch(url=URL, timeout=20):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def strip_tags(html):
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = html.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", html).strip()


def parse(html):
    """Return (source_updated, {garage: percent}).

    Deliberately parses visible text rather than CSS selectors. The markup on
    this page can change without notice; the words 'North Garage' and a number
    followed by '%' will not.
    """
    text = strip_tags(html)

    m = re.search(r"Last updated\s+([0-9]{4}-[0-9]{1,2}-[0-9]{1,2}[^R]*?)\s*(?:Refresh|$)", text, re.I)
    source_updated = m.group(1).strip() if m else ""

    readings = {}
    for name in GARAGES:
        m = re.search(re.escape(name) + r".{0,300}?(\d{1,3})\s*%", text, re.S | re.I)
        if m:
            readings[name] = int(m.group(1))

    if not readings:
        # Show what actually came back. Nine times out of ten this is a block
        # page, a cookie interstitial or a JS shell -- not a wording change.
        raise ValueError(
            "parsed zero garages. First 500 chars received:\n" + text[:500]
        )
    return source_updated, readings


# ------------------------------------------------------------------- storage

def last_source_updated(path=CSV_PATH):
    if not os.path.exists(path):
        return None
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return rows[-1]["source_updated"] if rows else None


def append(source_updated, readings, path=CSV_PATH):
    new = not os.path.exists(path)
    now = datetime.now().isoformat(timespec="seconds")
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        for garage, pct in readings.items():
            w.writerow({
                "fetched_at": now,
                "source_updated": source_updated,
                "garage": garage,
                "percent": pct,
            })


def sample(path=CSV_PATH):
    source_updated, readings = parse(fetch())
    if source_updated and source_updated == last_source_updated(path):
        print(f"[{datetime.now():%H:%M:%S}] unchanged ({source_updated}) -- skipped")
        return False
    append(source_updated, readings, path)
    summary = "  ".join(f"{g.split()[0]}:{p}%" for g, p in readings.items())
    print(f"[{datetime.now():%H:%M:%S}] {summary}")
    return True


def collect(once=False, interval=INTERVAL, path=CSV_PATH):
    while True:
        try:
            sample(path)
        except Exception as e:
            print(f"[{datetime.now():%H:%M:%S}] error: {e}", file=sys.stderr)
            # In --once mode (cron / CI) a swallowed error is worse than a
            # crash: the job goes green and you find out hours later that you
            # logged nothing. Exit loud. In loop mode, keep going -- one bad
            # response shouldn't kill an overnight run.
            if once:
                sys.exit(1)
        if once:
            return
        time.sleep(interval)


# -------------------------------------------------------------------- plotting

def plot(csv_path=CSV_PATH, png_path=PNG_PATH):
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    series = {}
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            t = datetime.fromisoformat(row["fetched_at"])
            series.setdefault(row["garage"], ([], []))
            series[row["garage"]][0].append(t)
            series[row["garage"]][1].append(int(row["percent"]))

    fig, ax = plt.subplots(figsize=(11, 6))
    for garage in GARAGES:
        if garage in series:
            xs, ys = series[garage]
            ax.plot(xs, ys, label=garage, linewidth=1.8)

    ax.set_ylim(0, 100)
    ax.set_ylabel("Fullness (%)")
    ax.set_xlabel("Time")
    ax.set_title("SJSU garage fullness")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%-I:%M %p"))
    ax.grid(alpha=0.25)
    ax.legend(frameon=False)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(png_path, dpi=150)
    print(f"wrote {png_path}")


# ------------------------------------------------------------------------ cli

def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect")
    c.add_argument("--once", action="store_true")
    c.add_argument("--interval", type=int, default=INTERVAL)
    c.add_argument("--csv", default=CSV_PATH)

    pl = sub.add_parser("plot")
    pl.add_argument("--csv", default=CSV_PATH)
    pl.add_argument("--out", default=PNG_PATH)

    a = p.parse_args()
    if a.cmd == "collect":
        collect(once=a.once, interval=a.interval, path=a.csv)
    else:
        plot(a.csv, a.out)


if __name__ == "__main__":
    main()
