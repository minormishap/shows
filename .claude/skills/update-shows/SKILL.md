---
name: update-shows
description: Add, update, or remove a Minor Mishap Marching Band show on minormishap.com/shows. Uploads the poster into the WordPress "Show Posters" media folder, adds the show card to shows.html, moves finished shows to Past Shows, lints, publishes page 1536 through the WordPress REST API, and opens a PR. Use this whenever the user mentions a new gig, show, parade, event, poster, or schedule change for the band or the shows page, even if they don't say "skill" or name the website. For example "we got booked at X on Saturday", "here's the poster for the canoe show", "the HONK show moved to 3pm", or "update the band schedule".
---

# Update the Minor Mishap shows page

The live page (WordPress page 1536) is the source of truth. `shows.html` in this
repo is a copy that can fall behind, because bandmates also edit the page in WP admin.
Every step below exists so the skill never overwrites their edits and never
publishes broken block markup.

All scripts are in `scripts/` next to this file. They use only the Python standard
library. Run them from the repo root with `python3 -I`. `$SCRATCH` below means your
session scratchpad directory. Temporary files such as `show.json` and `live.html`
go there, not in the repo.

| Script | Purpose |
|---|---|
| `wp.py` | REST calls: `whoami`, `pull`, `upload`, `push`, `purge-cache`, `find-media` |
| `shows.py` | Page edits and the checker: `add`, `remove`, `move-expired`, `lint` |

## 1. Collect the show details

Ask for the show and its poster in one message. If the user gives a poster, Read the
image first and draft the fields from it (date, venue, times, address). The user then
only needs to confirm. A poster is optional, so skip the upload step when there isn't one.

The poster must be a **file path**. An image pasted into chat is not a file the scripts
can upload. If needed, ask the user to drag the file into the terminal, or offer to use
the newest image in `~/Downloads`.

Fill this spec and save it to the scratchpad as `show.json`:

```json
{"date_text": "Saturday, November 7th, 2026",
 "end_date": "2026-11-07",
 "venue": "Fall Fest at Mueller Lake Park",
 "locations": ["4550 Mueller Blvd, Austin, TX 78723"],
 "details": ["Noon to 6pm", "Mishaps play 3pm"],
 "features": "Free | All Ages | Outdoors",
 "buttons": [{"label": "Google map", "url": "https://maps.google.com/..."}],
 "image": {"id": 0, "url": ""}}
```

- `end_date` is the **last** day of the show. The scripts use it to sort shows and to
  decide when a show moves to Past Shows. For a multi-day festival, use the final day.
- `date_text` is what visitors see. Match the existing style: weekday, month, day with
  suffix, year.
- `locations`, `details` and `features` can be a string or a list. Each list item
  becomes its own line.
- Leave out any field the user didn't give. Don't invent addresses or times. Ask instead.

## 2. Check credentials and sync with the live page

```bash
python3 -I .claude/skills/update-shows/scripts/wp.py whoami
```

This should report no `missing_capabilities`. `unfiltered_html` matters: without it,
WordPress strips the page's `<style>` block when the page is saved.

If the Keychain item is missing, the script prints the setup command. Have the user run
it in their own terminal. Never ask for the password in chat, and never put it in a file.

```bash
git checkout main && git pull --ff-only
gh pr list --search "show in:title" --state open     # an unmerged show PR explains drift
python3 -I .claude/skills/update-shows/scripts/wp.py pull --out "$SCRATCH/live.html"
diff -q "$SCRATCH/live.html" shows.html
```

Keep the `modified` value that `pull` prints. You need it for the push.

If the live page differs from `shows.html`, someone edited it in WP admin. Show the user
a short summary of the differences. Normally the live page wins: copy it over `shows.html`
and commit that as its own commit (`sync shows.html from live site`) on the branch from
step 3. If the live page fails `lint`, it was edited by hand. Fix only what lint
reports, and tell the user what you changed.

## 3. Branch, upload, add

```bash
git checkout -b feature/add-<end_date>-<venue-slug>    # e.g. feature/add-2026-11-07-fall-fest
python3 -I .claude/skills/update-shows/scripts/wp.py upload /path/to/poster.jpg --alt "<venue> poster"
```

`upload` puts the image in the Show Posters folder and confirms the folder assignment.
It prints `id` and `source_url`. `source_url` is the "File URL" shown in WP admin. Put
both values into `image` in the spec. Upload once only: re-running `upload` creates a
second copy of the poster in the media library.

```bash
python3 -I .claude/skills/update-shows/scripts/shows.py add shows.html "$SCRATCH/show.json"
```

`add` does four things:
- renders the card in the house format
- puts it in date order
- moves every Upcoming show whose end date has passed to Past Shows
- runs lint

Re-running it with the same `end_date` and `venue` replaces that card, so retrying is
safe. Always edit cards through the script. Hand-written cards miss the date metadata
that sorting depends on.

To change a show's details, edit the spec and run `add` again. If the date or venue
changed, `remove` the old card first:

```bash
python3 -I .claude/skills/update-shows/scripts/shows.py remove shows.html "2026-11-07 · Fall Fest"
```

If `lint` exits non-zero, fix the reported problem before going further.

## 4. Confirm, then publish

Publishing changes the public website, so show the user `git diff shows.html` (or a
summary of it, plus any "Moved to Past" lines) and wait for an explicit OK.

```bash
python3 -I .claude/skills/update-shows/scripts/wp.py push shows.html --expect-modified "<modified from pull>"
```

`push` refuses to run if the page changed after your pull. In that case, pull again,
re-sync, re-run `add` (safe, because it replaces the card), and push again. After
saving, `push` reads the stored content back and fails if WordPress changed anything.

Then check what visitors actually see:

```bash
curl -s https://www.minormishap.com/shows/ | grep -c "<venue text>"
```

If that returns 0 while the REST read-back succeeded, WP Super Cache is serving a stale
copy. Run `wp.py purge-cache` and check again.

## 5. Commit and open the PR

```bash
git add shows.html && git commit -m "add <venue> show"
git push -u origin HEAD
gh pr create --base main --title "Add <venue> show (<date_text>)" --body "..."
```

The PR body should cover:
- the show
- the poster attachment ID and URL
- which shows moved to Past
- the fact that the change is already live

Merging the PR keeps `main` in step with the site.

Give the user the live link, the PR link, and anything you were unsure of, such as a
guessed `end_date` or a missing address.
