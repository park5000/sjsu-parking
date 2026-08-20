#!/usr/bin/env python3
"""
SJSU parking garage fullness logger.

    python parking.py collect          # poll every 2 min until you Ctrl-C
    python parking.py collect --once   # single sample (use this from cron/CI)
    python parking.py debug            # print what the page actually contains
    python parking.py plot             # render parking.png from parking.csv

Collection is stdlib-only. Plotting needs matplotlib.
"""

import argparse
import csv
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

URL = "https://sjsuparkingstatus.sjsu.edu/"
CSV_PATH = "parking.csv"
PNG_PATH = "parking.png"
INTERVAL = 120
FIELDS = ["fetched_at", "source_updated", "garage", "status", "percent"]

GARAGES = [
    "North Garage",
    "West Garage",
    "South Garage",
    "South Campus Garage",
]

# Marks the end of the last garage's block.
END_MARKER = "Parking Shuttles"

HEADERS = {
    "User-Agent": "sjsu-parking-logger/1.0 (student research; contact: you@example.com)"
}


# ---------------------------------------------------------------- fetch/parse

def fetch(url=URL, timeout=20):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        # urllib wraps the real cause in URLError and puts it in .reason.
        if not isinstance(e.reason, ssl.SSLCertVerificationError):
            raise
        # This server omits its intermediate certificate, so Python can't build
        # a chain to a trusted root. Browsers silently go fetch the missing
        # cert; Python doesn't.
        #
        # Dropping verification is acceptable HERE and only here: public page,
        # no credentials sent, worst case someone lies to us about parking.
        # Never copy this into anything that logs in, pays, or touches a source.
        print("warning: TLS chain incomplete -- retrying unverified",
              file=sys.stderr)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.read().decode("utf-8", errors="replace")


def strip_tags(html):
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = html.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", html).strip()


def segment(text, i):
    """Return just the block of text belonging to GARAGES[i].

    Bounded on both ends. The original version searched a fixed 300 characters
    forward, which meant a garage reading 'Full' silently borrowed the next
    garage's percentage. Never let one record's parse run past its own edge.
    """
    start = text.find(GARAGES[i])
    if start < 0:
        return None
    stops = [text.find(m, start + len(GARAGES[i]))
             for m in GARAGES[i + 1:] + [END_MARKER]]
    stops = [s for s in stops if s > 0]
    return text[start:min(stops)] if stops else text[start:]


def parse(html):
    """Return (source_updated, {garage: (status, percent)}).

    status is 'ok' when the page gave a number, 'full' when it gave the word
    Full instead. 'full' is recorded as 100 so it charts, but it is a threshold
    the garage crossed, not a measurement -- treat the two differently when
    writing about it.
    """
    text = strip_tags(html)

    m = re.search(r"Last updated\s+(.+?)\s+Refresh", text)
    source_updated = m.group(1).strip() if m else ""

    readings = {}
    for i, name in enumerate(GARAGES):
        seg = segment(text, i)
        if seg is None:
            continue
        pm = re.search(r"(\d{1,3})\s*%", seg)
        if pm:
            readings[name] = ("ok", int(pm.group(1)))
        elif re.search(r"\bFull\b", seg, re.I):
            readings[name] = ("full", 100)

    if len(readings) < len(GARAGES):
        missing = [g for g in GARAGES if g not in readings]
        raise ValueError(
            f"could not read {missing}. First 500 chars received:\n" + text[:500]
        )
    return source_updated, readings


# ------------------------------------------------------------------- storage

def last_source_updated(path=CSV_PATH):
    if not os.path.exists(path):
        return None
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return rows[-1].get("source_updated") if rows else None


def append(source_updated, readings, path=CSV_PATH):
    new = not os.path.exists(path)
    now = datetime.now().isoformat(timespec="seconds")
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        for garage, (status, pct) in readings.items():
            w.writerow({
                "fetched_at": now,
                "source_updated": source_updated,
                "garage": garage,
                "status": status,
                "percent": pct,
            })


def sample(path=CSV_PATH):
    source_updated, readings = parse(fetch())
    if source_updated and source_updated == last_source_updated(path):
        print(f"[{datetime.now():%H:%M:%S}] unchanged ({source_updated}) -- skipped")
        return False
    append(source_updated, readings, path)
    summary = "  ".join(
        f"{g.split()[0]}:{'FULL' if s == 'full' else str(p) + '%'}"
        for g, (s, p) in readings.items()
    )
    print(f"[{datetime.now():%H:%M:%S}] {summary}")
    return True


def collect(once=False, interval=INTERVAL, path=CSV_PATH):
    while True:
        try:
            sample(path)
        except Exception as e:
            print(f"[{datetime.now():%H:%M:%S}] error: {e}", file=sys.stderr)
            # In --once mode a swallowed error is worse than a crash: the job
            # goes green and you find out hours later you logged nothing.
            if once:
                sys.exit(1)
        if once:
            return
        time.sleep(interval)


# --------------------------------------------------------------------- debug

def debug(url=URL):
    html = fetch(url)
    text = strip_tags(html)
    print(f"--- raw html: {len(html)} chars ---")
    print(f"--- stripped text: {len(text)} chars ---")
    print(text[:3000])
    print("--- per-garage segments ---")
    for i, name in enumerate(GARAGES):
        print(f"  [{name}] {segment(text, i)}")
    print("--- parsed ---")
    updated, readings = parse(html)
    print(f"  source_updated: {updated!r}")
    for g, (s, p) in readings.items():
        print(f"  {g:<22} {s:<5} {p}")


# ------------------------------------------------------------------- plotting

def plot(csv_path=CSV_PATH, png_path=PNG_PATH):
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    series = {}
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            t = datetime.fromisoformat(row["fetched_at"])
            g = row["garage"]
            series.setdefault(g, ([], [], []))
            series[g][0].append(t)
            series[g][1].append(int(row["percent"]))
            series[g][2].append(row.get("status") == "full")

    fig, ax = plt.subplots(figsize=(11, 6))
    for garage in GARAGES:
        if garage not in series:
            continue
        xs, ys, full = series[garage]
        line, = ax.plot(xs, ys, label=garage, linewidth=1.8)
        # Mark the readings that were 'Full' rather than a real number.
        fx = [x for x, f in zip(xs, full) if f]
        fy = [y for y, f in zip(ys, full) if f]
        ax.scatter(fx, fy, s=28, color=line.get_color(), zorder=3)

    ax.set_ylim(0, 105)
    ax.set_ylabel("Fullness (%)")
    ax.set_xlabel("Time")
    ax.set_title("SJSU garage fullness  (dots = reported Full, not a measured 100%)")
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

    sub.add_parser("debug")

    a = p.parse_args()
    if a.cmd == "collect":
        collect(once=a.once, interval=a.interval, path=a.csv)
    elif a.cmd == "debug":
        debug()
    else:
        plot(a.csv, a.out)


if __name__ == "__main__":
    main()
