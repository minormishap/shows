#!/usr/bin/env python3
"""Read the band's public Gig-o-Matic calendar feed (iCal).

The feed URL is a secret, so it lives in the macOS Keychain (service
"minormishap-gigo-feed") and is never printed. The feed carries only what the
band marked public: title, set/end time, address, public description. Band-only
notes (call time, parking, details) are not in it.

Usage:
    gigo.py list [--from YYYY-MM-DD]        # gigs on or after a date (default today)
    gigo.py on YYYY-MM-DD                   # gigs on one date, to match a poster
    gigo.py missing shows.html              # upcoming gigs with no card on the page
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.request
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shows  # noqa: E402

KEYCHAIN_SERVICE = "minormishap-gigo-feed"
BAND_TZ = ZoneInfo("America/Chicago")


def feed_url():
    try:
        url = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                             capture_output=True, text=True, check=True).stdout.strip()
    except subprocess.CalledProcessError:
        sys.exit(f"No Keychain item '{KEYCHAIN_SERVICE}'. Copy the band page's Public Gig Calendar Feed URL, then run:\n"
                 f"  security add-generic-password -s {KEYCHAIN_SERVICE} -a calfeed -w")
    # Cloudflare 301s plain http, and urllib won't follow that into a cached HTML page reliably.
    return "https://" + url.split("://", 1)[-1]


def unescape(v):
    return v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def parse_time(params, value):
    """Return a band-local datetime, or a date for all-day gigs."""
    if params.get("VALUE") == "DATE" or len(value) == 8:
        # Gig-o writes all-day gigs as VALUE=DATE:20260214T000000, so keep only the date part.
        return dt.datetime.strptime(value[:8], "%Y%m%d").date()
    if value.endswith("Z"):
        t = dt.datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
    else:
        t = dt.datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=ZoneInfo(params.get("TZID", "America/Chicago")))
    return t.astimezone(BAND_TZ)


def parse_ics(text):
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]  # RFC 5545 line folding
        else:
            lines.append(raw)
    events, cur = [], None
    for line in lines:
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT" and cur is not None:
            events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            head, value = line.split(":", 1)
            name, *params = head.split(";")
            cur[name] = (dict(p.split("=", 1) for p in params if "=" in p), value)
    gigs = []
    for e in events:
        start = parse_time(*e["DTSTART"])
        end = parse_time(*e["DTEND"]) if "DTEND" in e else None
        all_day = not isinstance(start, dt.datetime)
        gigs.append({
            "date": (start if all_day else start.date()).isoformat(),
            "title": unescape(e.get("SUMMARY", ({}, ""))[1]),
            # DTSTART is the set time when the gig has one, otherwise the call time.
            "start": None if all_day else start.strftime("%-I:%M%p").lower(),
            "end": None if all_day or not end else end.strftime("%-I:%M%p").lower(),
            "address": unescape(e.get("LOCATION", ({}, ""))[1]),
            "public_description": unescape(e.get("DESCRIPTION", ({}, ""))[1]),
        })
    return sorted(gigs, key=lambda g: (g["date"], g["start"] or ""))


def fetch():
    req = urllib.request.Request(feed_url(), headers={"User-Agent": "Mozilla/5.0 (minormishap-update-shows)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode("utf-8")
    if "BEGIN:VCALENDAR" not in body:
        sys.exit("Feed did not return a calendar; the Keychain URL may be wrong or revoked.")
    return parse_ics(body)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list"); s.add_argument("--from", dest="since", type=dt.date.fromisoformat, default=dt.date.today())
    s = sub.add_parser("on"); s.add_argument("date", type=dt.date.fromisoformat)
    s = sub.add_parser("missing"); s.add_argument("file"); s.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today())
    a = p.parse_args()
    gigs = fetch()
    if a.cmd == "list":
        out = [g for g in gigs if g["date"] >= a.since.isoformat()]
    elif a.cmd == "on":
        out = [g for g in gigs if g["date"] == a.date.isoformat()]
    else:
        with open(a.file, encoding="utf-8") as f:
            page = shows.Page(f.read())
        on_page = {str(shows.card_date(c)) for c in page.upcoming + page.past}
        out = [g for g in gigs if g["date"] >= a.today.isoformat() and g["date"] not in on_page]
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
