#!/usr/bin/env python3
"""Minimal WordPress REST client for the Minor Mishap Shows page.

Credentials come from the macOS Keychain (service "minormishap-wp"): the
account is the WordPress username and the password is a WordPress
Application Password. The secret is read here and sent only to the site;
it is never printed.

Usage:
    wp.py whoami
    wp.py pull --out shows.html          # prints {"modified": ...}
    wp.py upload poster.jpg [--alt TEXT] # prints {"id", "source_url", ...}
    wp.py push shows.html --expect-modified 2026-07-25T11:57:24
    wp.py find-media URL [URL ...]       # attachment IDs for upload URLs
    wp.py purge-cache                    # clear WP Super Cache after a push
"""
import argparse
import base64
import json
import mimetypes
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

SITE = "https://www.minormishap.com"
API = SITE + "/wp-json/wp/v2"
PAGE_ID = 1536
POSTER_FOLDER_SLUG = "show-posters-1745086953-3"
KEYCHAIN_SERVICE = "minormishap-wp"
UA = "minormishap-update-shows/1.0"


class WPError(Exception):
    pass


def credentials():
    """Return (username, app_password) from the Keychain, or exit with setup help."""
    try:
        meta = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE],
            capture_output=True, text=True, check=True).stdout
        secret = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, check=True).stdout.strip()
    except subprocess.CalledProcessError:
        sys.exit(f"No Keychain item '{KEYCHAIN_SERVICE}'. Create one with:\n"
                 f"  security add-generic-password -s {KEYCHAIN_SERVICE} -a <wp-username> -w\n"
                 "(it prompts for the Application Password; paste it there, not in chat)")
    m = re.search(r'"acct"<blob>="([^"]*)"', meta)
    if not m or not secret:
        sys.exit(f"Keychain item '{KEYCHAIN_SERVICE}' is missing its account or password.")
    return m.group(1), secret.replace(" ", "")


def request(method, path, *, body=None, headers=None, auth=True):
    url = path if path.startswith("http") else API + path
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    if auth:
        user, pw = credentials()
        hdrs["Authorization"] = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
    if isinstance(body, (dict, list)):
        body = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read())
            detail = f"{err.get('code')}: {err.get('message')}"
        except ValueError:
            detail = e.reason
        raise WPError(f"{method} {url} -> HTTP {e.code} {detail}") from None


def get_page():
    return request("GET", f"/pages/{PAGE_ID}?context=edit&_fields=content,modified,link")


def poster_folder_id():
    terms = request("GET", f"/media_folder?slug={POSTER_FOLDER_SLUG}&_fields=id", auth=False)
    if not terms:
        raise WPError(f"Media folder '{POSTER_FOLDER_SLUG}' not found")
    return terms[0]["id"]


def cmd_whoami(_):
    me = request("GET", "/users/me?context=edit&_fields=id,username,name,roles,capabilities")
    caps = me.get("capabilities", {})
    need = ["upload_files", "edit_pages", "edit_others_pages", "unfiltered_html"]
    print(json.dumps({"username": me["username"], "roles": me["roles"],
                      "missing_capabilities": [c for c in need if not caps.get(c)]}, indent=2))


def cmd_pull(a):
    page = get_page()
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(page["content"]["raw"])
    print(json.dumps({"modified": page["modified"], "link": page["link"], "out": a.out}))


def cmd_upload(a):
    name = os.path.basename(a.file)
    mime = mimetypes.guess_type(name)[0]
    if not mime or not mime.startswith("image/"):
        sys.exit(f"{name}: not an image file")
    with open(a.file, "rb") as f:
        data = f.read()
    media = request("POST", "/media", body=data, headers={
        "Content-Type": mime,
        "Content-Disposition": f'attachment; filename="{urllib.parse.quote(name)}"'})
    folder = poster_folder_id()
    # The raw-body upload cannot carry fields, so the folder and alt text are a second call.
    media = request("POST", f"/media/{media['id']}", body={
        "media_folder": [folder], "alt_text": a.alt or ""})
    if folder not in media.get("media_folder", []):
        raise WPError(f"Uploaded {media['id']} but folder assignment did not stick")
    sizes = media.get("media_details", {}).get("sizes", {})
    print(json.dumps({"id": media["id"], "source_url": media["source_url"],
                      "large_url": sizes.get("large", {}).get("source_url"),
                      "media_folder": media["media_folder"]}, indent=2))


def cmd_push(a):
    with open(a.file, encoding="utf-8") as f:
        content = f.read()
    current = get_page()
    if current["modified"] != a.expect_modified:
        sys.exit(f"Refusing to push: page was modified at {current['modified']} "
                 f"(expected {a.expect_modified}). Pull again and redo the edit.")
    request("POST", f"/pages/{PAGE_ID}", body={"content": content})
    after = get_page()
    # WordPress silently filters markup (e.g. <style>) for users without unfiltered_html.
    if after["content"]["raw"].strip() != content.strip():
        sys.exit("Pushed, but WordPress stored different content than was sent. "
                 "Run `wp.py pull` and diff to see what it changed.")
    print(json.dumps({"modified": after["modified"], "link": after["link"]}))


def cmd_purge_cache(_):
    print(json.dumps(request("POST", SITE + "/wp-json/wp-super-cache/v1/cache",
                             body={"wp_delete_cache": True})))


def cmd_find_media(a):
    out = {}
    for url in a.urls:
        stem = os.path.splitext(os.path.basename(urllib.parse.urlparse(url).path))[0]
        base = re.sub(r"-(\d+x\d+|scaled)$", "", stem)
        hits = request("GET", "/media?per_page=20&_fields=id,source_url,media_details&search="
                       + urllib.parse.quote(base), auth=False)
        match = None
        for h in hits:
            urls = {h["source_url"]} | {s["source_url"] for s in
                                         h.get("media_details", {}).get("sizes", {}).values()}
            if url in urls:
                match = h["id"]
                break
        out[url] = match
    print(json.dumps(out, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("whoami").set_defaults(fn=cmd_whoami)
    s = sub.add_parser("pull"); s.add_argument("--out", required=True); s.set_defaults(fn=cmd_pull)
    s = sub.add_parser("upload"); s.add_argument("file"); s.add_argument("--alt"); s.set_defaults(fn=cmd_upload)
    s = sub.add_parser("push"); s.add_argument("file"); s.add_argument("--expect-modified", required=True)
    s.set_defaults(fn=cmd_push)
    sub.add_parser("purge-cache").set_defaults(fn=cmd_purge_cache)
    s = sub.add_parser("find-media"); s.add_argument("urls", nargs="+"); s.set_defaults(fn=cmd_find_media)
    a = p.parse_args()
    try:
        a.fn(a)
    except WPError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
