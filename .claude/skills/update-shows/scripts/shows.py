#!/usr/bin/env python3
"""Lint and edit the Minor Mishap Shows page (WordPress block markup).

Page layout this script relies on:
    <style> block, "Upcoming Shows" heading, upcoming show-cards,
    "Past Shows" heading, past show-cards, then older free-form blocks.

Every show-card group carries its end date in the Gutenberg block-name
attribute, e.g. {"className":"show-card","metadata":{"name":"2026-03-29 · HONK!TX"}}.
That date drives sorting and moving expired shows to Past.

Usage:
    shows.py lint shows.html [--today YYYY-MM-DD]
    shows.py add shows.html show.json [--today YYYY-MM-DD]   # insert + move expired + lint
    shows.py move-expired shows.html [--today YYYY-MM-DD]
    shows.py remove shows.html "2026-11-07 · Fall Fest"     # block-name prefix

show.json fields (only date_text, end_date, venue, locations are required):
    {"date_text": "Saturday, Nov 1st, 2026", "end_date": "2026-11-01",
     "venue": "...", "locations": ["..."], "details": ["7pm", "..."],
     "features": "...", "buttons": [{"label": "Get Tickets", "url": "https://..."}],
     "image": {"id": 2332, "url": "https://www.minormishap.com/wp-content/uploads/..."}}
"""
import argparse
import datetime as dt
import html
import json
import re
import sys

UPLOADS = "https://www.minormishap.com/wp-content/uploads/"
BLOCK_RE = re.compile(r"<!--\s+(/)?wp:([a-z][a-z0-9_-]*/)?([a-z][a-z0-9_-]*)\s+(\{.*?\}\s+)?(/)?-->", re.S)
CARD_RE = re.compile(r'<!-- wp:group (\{[^\n]*"show-card"[^\n]*\}) -->\n.*?\n<!-- /wp:group -->', re.S)
SECTION_RE = r'<!-- wp:heading \{"level":2,"className":"section-title"\} -->\n<h2 class="wp-block-heading section-title">%s</h2>\n<!-- /wp:heading -->'


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def card_name(card_text):
    return json.loads(CARD_RE.match(card_text).group(1)).get("metadata", {}).get("name", "")


def card_date(card_text):
    m = re.match(r"(\d{4}-\d{2}-\d{2})\b", card_name(card_text))
    return dt.date.fromisoformat(m.group(1)) if m else None


class Page:
    """The page split into the regions this script edits; everything else is kept verbatim."""

    def __init__(self, text):
        up = re.search(SECTION_RE % "Upcoming Shows", text)
        past = re.search(SECTION_RE % "Past Shows", text)
        if not up or not past or past.start() < up.end():
            raise ValueError('Expected an "Upcoming Shows" heading followed by a "Past Shows" heading')
        self.head = text[:up.end()]
        self.past_heading = past.group(0)
        region = text[up.end():past.start()]
        self.upcoming = [m.group(0) for m in CARD_RE.finditer(region)]
        if CARD_RE.sub("", region).strip():
            raise ValueError("Non-card content between the section headings: "
                             f"{CARD_RE.sub('', region).strip()[:80]!r}")
        # Past cards are the contiguous run of cards right after the heading;
        # the older free-form announcements after them stay untouched in `tail`.
        body = text[past.end():]
        self.past = []
        pos = 0
        while m := CARD_RE.match(body, len(body) - len(body[pos:].lstrip())):
            self.past.append(m.group(0))
            pos = m.end()
        self.tail = body[pos:].lstrip("\n")
        self.undated = sum(1 for c in self.upcoming + self.past if not card_date(c))

    def render(self):
        def section(cards):
            return "\n\n" + "".join(c + "\n\n" for c in cards) + "\n"
        return (self.head + section(self.upcoming) + self.past_heading
                + section(self.past) + self.tail)


def json_attrs(obj):
    """Serialize block attributes the way WordPress does (no raw --, <, >, & or quotes in values)."""
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return (s.replace("--", "\\u002d\\u002d").replace("<", "\\u003c")
             .replace(">", "\\u003e").replace("&", "\\u0026").replace('\\"', "\\u0022"))


def as_list(v):
    return [] if v is None else [v] if isinstance(v, str) else list(v)


def render_card(spec):
    end = dt.date.fromisoformat(spec["end_date"])
    name = f"{end.isoformat()} · {spec['venue']}"
    out = [f'<!-- wp:group {json_attrs({"className": "show-card", "metadata": {"name": name}})} -->',
           '<div class="wp-block-group show-card">',
           '    <!-- wp:heading {"level":4,"className":"show-date"} -->',
           f'    <h4 class="wp-block-heading show-date">{html.escape(spec["date_text"], quote=False)}</h4>',
           '    <!-- /wp:heading -->', '',
           '    <!-- wp:heading {"level":5,"className":"show-venue"} -->',
           f'    <h5 class="wp-block-heading show-venue">{html.escape(spec["venue"], quote=False)}</h5>',
           '    <!-- /wp:heading -->', '']
    # One paragraph block per line, matching how multi-line cards render on the live page.
    for cls, key in (("show-location", "locations"), ("show-details", "details"), ("show-features", "features")):
        for line in as_list(spec.get(key)):
            out += [f'    <!-- wp:paragraph {{"className":"{cls}"}} -->',
                    f'    <p class="{cls}">{html.escape(line, quote=False)}</p>',
                    '    <!-- /wp:paragraph -->', '']
    buttons = spec.get("buttons") or []
    if buttons:
        out += ['    <!-- wp:buttons -->', '    <div class="wp-block-buttons">']
        for b in buttons:
            if not re.match(r"https?://", b["url"]):
                raise ValueError(f"Button URL must be http(s): {b['url']}")
            out += ['        <!-- wp:button {"backgroundColor":"primary","textColor":"white","className":"show-ticket-link"} -->',
                    '        <div class="wp-block-button show-ticket-link"><a class="wp-block-button__link has-white-color '
                    f'has-primary-background-color wp-element-button" href="{html.escape(b["url"])}" target="_blank" '
                    f'rel="noopener noreferrer">{html.escape(b["label"], quote=False)}</a></div>',
                    '        <!-- /wp:button -->']
        out += ['    </div>', '    <!-- /wp:buttons -->', '']
    img = spec.get("image")
    if img:
        iid = int(img["id"])
        out += [f'    <!-- wp:image {{"id":{iid},"sizeSlug":"large","linkDestination":"none"}} -->',
                f'    <figure class="wp-block-image size-large"><img src="{html.escape(img["url"])}" alt="" class="wp-image-{iid}"/></figure>',
                '    <!-- /wp:image -->', '']
    out += ['</div>', '<!-- /wp:group -->']
    return "\n".join(out)


def lint(text, today):
    errors = []
    # 1. Block comments nest and close properly.
    stack = []
    for m in BLOCK_RE.finditer(text):
        closing, ns, name, attrs, selfclosing = m.groups()
        full = (ns or "") + name
        ln = line_of(text, m.start())
        if attrs:
            try:
                json.loads(attrs)
            except ValueError:
                errors.append(f"line {ln}: wp:{full} has invalid JSON attributes")
        if selfclosing:
            continue
        if closing:
            if stack and stack[-1][0] == full:
                stack.pop()
            else:
                want = f"/wp:{stack[-1][0]} (opened line {stack[-1][1]})" if stack else "nothing"
                errors.append(f"line {ln}: /wp:{full} closes but expected {want}")
        else:
            if full == "heading" and attrs and json.loads(attrs).get("level") == 1:
                errors.append(f"line {ln}: stray level-1 heading block")
            stack.append((full, ln))
    for name, ln in stack:
        errors.append(f"line {ln}: wp:{name} is never closed")
    try:
        page = Page(text)
    except ValueError as e:
        return errors + [str(e)]
    # 2. Every card is well formed.
    for section, cards in (("Upcoming", page.upcoming), ("Past", page.past)):
        for card in cards:
            ln = line_of(text, text.find(card))
            date = card_date(card)
            label = f"line {ln} ({section})"
            if not date:
                errors.append(f'{label}: card has no "metadata":{{"name":"YYYY-MM-DD · ..."}} date')
            for cls in ("show-date", "show-venue"):
                if card.count(f'className":"{cls}"') != 1:
                    errors.append(f"{label}: needs exactly one {cls}")
            if 'className":"show-location"' not in card:
                errors.append(f"{label}: missing show-location")
            for p in re.finditer(r"<!-- wp:paragraph.*?-->(.*?)<!-- /wp:paragraph -->", card, re.S):
                if len(re.findall(r"<p[ >]", p.group(1))) != 1:
                    errors.append(f"{label}: a paragraph block must hold exactly one <p> (split lines into separate blocks)")
            for im in re.finditer(r'<!-- wp:image (\{.*?\}) -->\s*<figure[^>]*><img src="([^"]+)"[^>]*class="wp-image-(\d+)"', card, re.S):
                if json.loads(im.group(1)).get("id") != int(im.group(3)):
                    errors.append(f"{label}: image id {json.loads(im.group(1)).get('id')} != wp-image-{im.group(3)}")
                if not im.group(2).startswith(UPLOADS):
                    errors.append(f"{label}: image src is not a site upload: {im.group(2)}")
            for href in re.findall(r'show-ticket-link"><a [^>]*href="([^"]+)"', card):
                if not re.match(r"https?://", href):
                    errors.append(f"{label}: button href is not http(s): {href}")
    # 3. Sections are in order and Upcoming holds no expired shows.
    up = [card_date(c) for c in page.upcoming]
    past = [card_date(c) for c in page.past]
    if None not in up and up != sorted(up):
        errors.append("Upcoming shows are not sorted soonest-first")
    if None not in past and past != sorted(past, reverse=True):
        errors.append("Past shows are not sorted newest-first")
    names = [card_name(c) for c in page.upcoming + page.past]
    for n in sorted({n for n in names if n and names.count(n) > 1}):
        errors.append(f"Duplicate show card: {n}")
    for d in up:
        if d and d < today:
            errors.append(f"Upcoming show dated {d} has already happened (run move-expired)")
    return errors


def move_expired(page, today):
    expired = [c for c in page.upcoming if card_date(c) < today]
    page.upcoming = [c for c in page.upcoming if card_date(c) >= today]
    page.past = sorted(expired + page.past, key=card_date, reverse=True)
    return expired


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["lint", "add", "move-expired", "remove"])
    p.add_argument("file")
    p.add_argument("spec", nargs="?")
    p.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today())
    a = p.parse_args()
    with open(a.file, encoding="utf-8") as f:
        text = f.read()

    if a.cmd in ("add", "move-expired", "remove"):
        page = Page(text)
        if page.undated:
            sys.exit(f"{page.undated} card(s) have no metadata date; run lint and fix them first")
        if a.cmd == "add":
            if not a.spec:
                sys.exit("add needs a show.json spec")
            with open(a.spec, encoding="utf-8") as f:
                card = render_card(json.load(f))
            # Re-running with the same date and venue updates the card instead of duplicating it.
            name = card_name(card)
            before = len(page.upcoming) + len(page.past)
            page.upcoming = [c for c in page.upcoming if card_name(c) != name]
            page.past = [c for c in page.past if card_name(c) != name]
            if len(page.upcoming) + len(page.past) < before:
                print(f"Replaced existing card: {name}")
            if card_date(card) < a.today:
                page.past = sorted(page.past + [card], key=card_date, reverse=True)
                print(f"Note: {card_date(card)} is in the past, so the show went into Past Shows")
            else:
                page.upcoming = sorted(page.upcoming + [card], key=card_date)
        if a.cmd == "remove":
            hits = [c for c in page.upcoming + page.past if card_name(c).startswith(a.spec or "\0")]
            if len(hits) != 1:
                sys.exit(f"remove needs a prefix matching exactly one card; matched {[card_name(c) for c in hits]}")
            page.upcoming = [c for c in page.upcoming if c is not hits[0]]
            page.past = [c for c in page.past if c is not hits[0]]
            print(f"Removed: {card_name(hits[0])}")
        for c in move_expired(page, a.today):
            print(f"Moved to Past: {json.loads(CARD_RE.match(c).group(1))['metadata']['name']}")
        text = page.render()
        with open(a.file, "w", encoding="utf-8") as f:
            f.write(text)

    errors = lint(text, a.today)
    for e in errors:
        print("ERROR", e)
    print(f"lint: {len(errors)} error(s)")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
