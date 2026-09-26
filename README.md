# Syllabus-Driven Course Reading Organizer

Files your course readings into `<Course>/Week 03_1012_1018/` — driven by what the
syllabus actually assigns, not by guesswork.

Built for a Canvas school, but every school-specific thing lives in `courses.json`.

---

## Why this exists

Downloading a term's readings is easy. Knowing **which week each one belongs to**,
and **whether you still have gaps**, is not. This tool:

1. Parses your syllabi into an index: course → week → reading, with required/optional
2. Pulls material from wherever the instructor happens to have put it
3. Files each file into the right week, or **refuses to guess** when evidence is thin
4. Reconciles against the syllabus so you can see what's still missing

The last point matters most. Correct filing is not the same as complete coverage.

---

## The core design rule

> **Prefer authority over inference. When neither is conclusive, do nothing.**

Placement decisions come from four tiers, highest first:

| Tier | Source | Certainty |
|---|---|---|
| 1 | Recorded placement (`file_titles.json`) — read from Canvas structure at fetch time | Exact |
| 2 | Canvas manifest — filename matches a file in a week-named module or folder | Exact |
| 3 | Fuzzy manifest match — file was renamed but overlaps a known entry ≥75% | High |
| 4 | Syllabus matching — author surname **required**, plus title-token coverage | Scored |

If tier 4's best candidate doesn't beat the runner-up by `margin`, the file **stays
where it is** and gets listed for review. Failures are refusals, not misplacements.

---

## Instructors organize material in at least three different ways

This was learned the hard way — each one broke an earlier version. All three are
now tried in parallel and **unioned**; a failure in one never disables the others.

### 1. Canvas Modules

Modules named `Week 3: …`. Items of type `File` hang off them directly.
Handled by `canvas_snippet.js` → `sort_downloads.py`.

*Gotcha:* the bulk `/api/v1/courses/:id/files` endpoint is often **403** for
students. Per-file `/files/:id` usually still works, so files are resolved
individually as a fallback.

### 2. Canvas Files folders (no modules at all)

No modules; the `Files` tab has folders literally named `Week 0`, `Week 1`, …
The week lives in the folder hierarchy. Handled automatically — folder names are
read via `/folders` and joined to files by `folder_id`.

### 3. Readings embedded in Canvas Pages

Modules contain only `Page` and `Assignment` items; the actual readings are
hyperlinks **inside the page body**. The page HTML is fetched and
`a[href*="/files/"]` anchors are parsed; the anchor's `title` attribute supplies
the filename, and `Required Readings:` / `Further Readings:` headings in the body
supply required-vs-optional.

*Bonus:* if the course is **publicly accessible**, those links carry a `verifier`
signature and work without a session — so `fetch_public_course.py` can do the whole
job in Python, no browser needed. Set `"public": true` on the course.

### 4. Not on Canvas at all

Some courses distribute a Dropbox/Drive folder or point at the library. Download
the folder as one archive and hand it to `ingest_zip.py`, which uses the archive's
own folder structure first (`Week 3`, `10.13`, `Oct 27` all recognized) and falls
back to syllabus matching.

### Adding a fifth

`canvas_snippet.js` exposes `buildManifest(io, opts)`, where `io` is just
`{getJSON, getText, parseDoc, log, warn}`. A new source means adding one more
loop that contributes to `byId` and `place` — see the three existing ones. Then
add a fixture to `test_canvas_scrape.js`.

---

## Setup

```bash
cp courses.example.json courses.json     # then edit it
python build_index.py                    # parse syllabi -> readings_index.json
```

`courses.json` holds everything school- and term-specific. Nothing else needs editing.

### Term calendar: quarter, semester, any year

Week folder dates are **computed**, never hardcoded:

```jsonc
"term": {
  "week1_monday": "2026-09-28",
  "week_count": 10,               // quarter ~10, semester ~15
  "has_week0": false,             // true if there's an orientation week
  "breaks": [ { "name": "Thanksgiving", "monday": "2026-11-23" } ]
}
```

> **Breaks are the thing people get wrong.** Weeks are not "previous + 7 days".
> A single break week shifts every subsequent week by one. List them in `breaks`
> and numbering skips them correctly. `python weeks.py` prints the resulting
> calendar — check it before anything else.

Courses that schedule by date rather than week number need no date table: dates
are resolved through this same calendar.

### Recognizing week labels

Defaults cover `Week 3`, `Unit 3`, `Wk 3`, `Session 3`, `Topic 3`, `第3周`.
Add your own with `"week_patterns": ["\\bSemaine\\s*(\\d{1,2})\\b"]` — each regex
must have one capturing group for the number.

### Syllabus formats

`parser` picks how a syllabus is split into weeks:

| `parser` | Matches |
|---|---|
| `era` | `Week 1 (Sep 30) Title` |
| `ai` | `Week 1: Title`, with `Required Readings:` / `Further Readings:` sections |
| `persp` | `WEEK 1 (9/29, 10/1): TITLE` |
| `linc` | No week numbers; sessions headed by date (`9/29, Tuesday – …`) |

A format none of these fit needs a new splitter in `build_index.py` (they're ~3
lines each) plus a case in `test_sorting.py`.

Required-vs-optional is detected from section headings, or from per-entry markers
(`**` required, `*` recommended, `+` optional) where the syllabus uses them.

---

## Daily use

| Script | What it does |
|---|---|
| `build_index.py` | Re-parse syllabi. Run after a syllabus changes. |
| `canvas_snippet.js` | Paste into the Canvas console. Exports a manifest and triggers downloads. |
| `fetch_public_course.py` | Pure-Python fetch for public courses. No browser. |
| `ingest_zip.py` | Import a Dropbox/Drive archive. |
| `sort_downloads.py` | File anything in the downloads folder. |
| `reorganize.py` | Re-sort already-filed material after a rule or syllabus change. |
| `sort_downloads.py --coverage` | **Reconcile against the syllabus.** |

`--coverage` is the one to run regularly:

```
  ✓ Week 02_1005_1011  required 5/5   optional 1/2   (9 files)
  ! Week 03_1012_1018  required 2/4   optional 1/3   (7 files)
        missing (required): Marsilli-Vargas 2022 — Genres of Listening
```

---

## Safety

The downloads folder is the OS default, full of unrelated files. The sorter is
allow-list only:

- An author surname from the syllabus **must** appear — no surname, no move
- Only configured extensions are considered; `.exe`/`.zip`/`.jpg` never enter
- Top level only, no recursion
- Skips `.crdownload`/`.part` and anything modified in the last 30 seconds
- A file matching >10 entries is treated as a bibliography and left alone
- **Filename evidence outranks body text.** A file *named* for an author is that
  work; a file that merely *cites* it is not. Body text is read only when the
  filename is a meaningless ID like `3034157.pdf`
- Dry run first, confirm, then move. Every move is logged and `--undo` reverses it
- Long paths are shortened automatically (Windows `MAX_PATH` is 260)

---

## Tests

```bash
python test_sorting.py        # 37 cases
node test_canvas_scrape.js    # 10 cases
```

Neither needs network or personal data. Every case corresponds to a bug that
actually happened. Some worth knowing about:

- A chapter assigned in week 7 being filed under week 2 because the filename also
  contained the book title
- Hyphenated surnames (`Lévi-Strauss`) never matching, because the index stored
  the hyphen but filenames tokenize to letters only
- Scoring thresholds that were tuned against a 300-entry index and silently
  rejected everything for smaller ones — IDF is now normalized so thresholds mean
  the same thing at any corpus size
- Prose paragraphs in a syllabus being parsed as citations, inflating the
  "missing" list with entries that never existed

---

## Privacy

`.gitignore` excludes everything derived from your account. In particular
**`canvas_manifest.json` contains signed download URLs** — publishing it hands out
access to the course material. Also excluded: `readings_index.json` (a full
extract of your instructors' syllabi), `moves_log.csv` (absolute paths), and
`courses.json`.

Nothing is uploaded anywhere. No access token is used or stored: the browser
snippet runs inside your existing session, and public-course fetching relies on
signatures the server already hands out.
