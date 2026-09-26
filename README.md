# Auto Course Material Downloader & Manager

[中文说明](README.zh-CN.md)

Auto-download your course materials from Canvas and Dropbox, and auto-sort them
into weekly folders.

Files readings into `<Course>/Week 03_1012_1018/` based on **what the syllabus
actually assigns** — separating required from optional, and telling you what
you're still missing.

Built against Canvas, but every school- and term-specific value lives in one
config file.

---

## The problem this solves

Downloading a term's readings is easy. The hard parts are:

- **Which week does this PDF belong to?** Filenames like `3034157.pdf` or
  `hall_1996.pdf` don't say.
- **Is this required or optional?** Syllabi mark it; downloads don't.
- **What am I still missing?** Having 100 files doesn't mean having all of them.
- **Instructors organize material inconsistently.** Modules, Files folders,
  links inside pages, Dropbox — sometimes several within one school.

This tool parses your syllabi into an index, pulls material from wherever the
instructor put it, and reconciles the two.

---

## Core design rule

> **Prefer authority over inference. When neither is conclusive, do nothing.**

Your downloads folder is the OS default — full of tax forms, invoices, and
screenshots. So placement is allow-list only, decided in four tiers:

| Tier | Basis | Certainty |
|---|---|---|
| 1 | Placement recorded at fetch time, read from Canvas structure | Exact |
| 2 | Filename matches a file in a week-named module or folder | Exact |
| 3 | File was renamed but overlaps a known entry ≥75% | High |
| 4 | Syllabus match: author surname **required**, plus title-token coverage | Scored |

If tier 4's best candidate doesn't beat the runner-up by a margin, the file
**stays put** and is listed for review. Failures are refusals, not misplacements.

---

## Quick start

### Step 1 — Requirements

```bash
python --version            # 3.9+
pip install pymupdf python-docx
node --version              # optional, only to run the JS tests
```

### Step 2 — Create your config

```bash
cp courses.example.json courses.json
```

Then edit `courses.json`. This is the only file you need to change.

### Step 3 — Set your term calendar

```jsonc
"term": {
  "week1_monday": "2026-09-28",
  "week_count": 10,
  "has_week0": false,
  "breaks": [ { "name": "Thanksgiving", "monday": "2026-11-23" } ]
}
```

**Decision: quarter or semester?**

| System | `week_count` |
|---|---|
| Quarter | 10–11 |
| Semester | 14–16 |

**Decision: do you have an orientation week before week 1?**
Set `has_week0: true` and you'll get a `Week 00_…` folder.

> ⚠️ **Breaks are what people get wrong.** Weeks are *not* "previous + 7 days".
> One break week shifts every later week by one. List every break — Thanksgiving,
> fall break, spring break, reading week — and numbering skips them correctly.

Verify before going further:

```bash
python weeks.py
```

This prints the calendar it computed. Check week 1 and the week after each break
against your actual schedule. Everything downstream depends on this.

### Step 4 — Describe your courses

One entry per course. `folder` must match the folder name on disk exactly.

```jsonc
{
  "folder": "Ethnographic Research",
  "syllabus": "Syllabus ANTH 33506.pdf",
  "parser": "era",
  "canvas_course_id": "12345",
  "has_week0": false,
  "public": false
}
```

**Decision: which `parser`?** Open your syllabus and match the week headings:

| Your syllabus looks like | `parser` |
|---|---|
| `Week 1 (Sep 30) Ethnography in Context` | `era` |
| `Week 1: Symbolic technologies` + `Required Readings:` sections | `ai` |
| `WEEK 1 (9/29, 10/1): NARRATIVES` | `persp` |
| No week numbers — sessions headed by date, e.g. `9/29, Tuesday – Topic` | `linc` |

None of these fit? See [Adding a syllabus format](#adding-a-syllabus-format).

**Decision: where does this course's material live?** This determines which tool
you use — see the next section.

### Step 5 — Build the index

```bash
python build_index.py
```

Prints how many readings it found per week. **Sanity-check these counts against
your syllabus.** If a week shows 0 or an implausible number, the parser choice is
probably wrong.

---

## Choosing how to fetch material

Instructors distribute material in at least five different ways. Find yours:

| What you see in the course | Use | Config |
|---|---|---|
| Modules named `Week 3`, with files attached | `canvas_snippet.js` | `canvas_course_id` |
| No modules, but **Files** has folders named `Week 3` | `canvas_snippet.js` | same |
| Modules contain only Pages; readings are links **inside** the page | `canvas_snippet.js` | same |
| Course is **public** and you're auditing / not enrolled | `fetch_public_course.py` | `"public": true` |
| A Dropbox / Drive folder, or nothing on Canvas at all | `ingest_zip.py` | `canvas_course_id: null` |

The first three are detected automatically — all sources are tried in parallel and
merged, so one failing never disables the others. You don't have to know which
one your course uses.

### Canvas courses (browser)

```
1. Open your Canvas in Chrome, confirm you're logged in
2. F12 → Console  (first paste may require typing: allow pasting)
3. Paste all of canvas_snippet.js, press Enter
4. Run: python sort_downloads.py
```

The snippet exports a manifest and triggers downloads through your existing
session. **No access token is created or stored.** Many schools disable personal
API tokens; this works anyway.

Edit the constants at the top of the snippet to limit scope:

```js
const ONLY_COURSE_IDS = [];      // [] = all courses; [12345] = just that one
const EXTRA_COURSE_IDS = [];     // courses the list endpoint misses
const DOWNLOAD_FILES = true;     // false = export the manifest only
```

### Public courses (no browser)

If a course is publicly readable, its file links carry a signature that works
without a session — so Python can do everything:

```bash
python fetch_public_course.py --all-public          # preview
python fetch_public_course.py --all-public --apply  # download
```

Set `"public": true` on those courses. Re-run when the instructor publishes a new
week; already-downloaded files are skipped.

### Dropbox / Drive archives

Download the whole folder as one archive (for Dropbox, change `dl=0` to `dl=1` in
the share link), then:

```bash
python ingest_zip.py "path/to/archive.zip" --course "Course Folder" --list   # inspect
python ingest_zip.py "path/to/archive.zip" --course "Course Folder"          # preview
python ingest_zip.py "path/to/archive.zip" --course "Course Folder" --apply
```

It uses the archive's own folder structure first — `Week 3`, `10.13`, `Oct 27`,
`October 13` are all recognized — then falls back to syllabus matching. Only the
archive you name is touched; the downloads folder is never scanned for archives.

---

## Daily use

| Command | Purpose |
|---|---|
| `python sort_downloads.py` | File whatever is in the downloads folder |
| `python sort_downloads.py --coverage` | **Reconcile against the syllabus** |
| `python sort_downloads.py --undo` | Reverse the last batch |
| `python reorganize.py --apply` | Re-sort already-filed material after a rule change |
| `python build_index.py` | Re-parse syllabi after one is updated |

Windows users: numbered `.bat` files wrap these for double-clicking.

`--coverage` is the one to run regularly:

```
  ✓ Week 02_1005_1011  required 5/5   optional 1/2   (9 files)
  ! Week 03_1012_1018  required 2/4   optional 1/3   (7 files)
        missing (required): Marsilli-Vargas 2022 — Genres of Listening
```

Correct filing is not the same as complete coverage. This is the only thing that
catches a silently missing reading.

---

## How required vs optional is decided

Required readings go in the week folder; everything else goes in
`Week 03_…/Additional Readings/`.

Detected from whichever your syllabus uses:

- **Section headings** — `Required Readings:`, `Further Readings (optional):`,
  `Optional/Provided:`
- **Per-entry markers** — `**` required, `*` strongly recommended, `+` optional
- **Canvas page structure** — headings in the page body, when readings are linked there

A reading assigned in several weeks is **copied into each one**, so opening any
week folder shows everything due that week.

---

## Configuration reference

### Recognizing week labels

Defaults: `Week 3`, `Unit 3`, `Wk 3`, `Session 3`, `Topic 3`, `第3周`.
Add your own (one capturing group for the number):

```jsonc
"week_patterns": ["\\bSemaine\\s*(\\d{1,2})\\b", "\\bLecture\\s*(\\d{1,2})\\b"]
```

### Matching thresholds

```jsonc
"matching": {
  "min_score": 8.0,       // raise → stricter; lower → more aggressive
  "margin": 2.0,          // best must beat runner-up by this
  "max_candidates": 10,   // matching more entries than this = bibliography, skip
  "min_age_seconds": 30   // ignore files still being written
}
```

Defaults are deliberately conservative. If files are being skipped that should
match, lower `min_score` to ~5. If anything is ever misfiled, raise `margin`.
Scores are normalized, so these mean the same thing whether your index has 50
readings or 500.

### Paths

```jsonc
"paths": {
  "base_dir": "",                                  // "" = parent of this folder
  "downloads_dir": "C:\\\\Users\\\\NAME\\\\Downloads"
}
```

### Adding a syllabus format

`build_index.py` has one splitter per format, ~3 lines each. Copy the closest one,
regex your week headings, register it in `SPLITTERS`, then add a case to
`test_sorting.py`.

### Adding a material source

`canvas_snippet.js` exposes `buildManifest(io, opts)` where `io` is just
`{getJSON, getText, parseDoc, log, warn}` — no globals, no fetch. Add a loop that
contributes to `byId` (files) and `place` (week per file), following the three
existing sources. Then add a fixture to `test_canvas_scrape.js`.

---

## Safety guarantees

The downloads folder is shared with everything else you download, so:

- An author surname from the syllabus **must** appear, or nothing moves
- Only configured extensions are considered; `.exe`, `.zip`, `.jpg` never enter
- Top level only, no recursion into subfolders
- Skips `.crdownload` / `.part` and anything modified in the last 30 seconds
- A file matching more than `max_candidates` entries is treated as a bibliography
  and left alone
- **Filename evidence outranks body text.** A file *named* for a work is that
  work; a file that merely *cites* it is not. Body text is read only when the
  filename is a meaningless ID like `3034157.pdf`
- Dry run first, confirm, then move. Every move is logged; `--undo` reverses it
- Long paths are shortened automatically (Windows `MAX_PATH` is 260)
- Duplicate downloads (`file (1).pdf`) are recognized, not filed twice

---

## Tests

```bash
python test_sorting.py        # 37 cases
node test_canvas_scrape.js    # 10 cases
```

No network, no personal data. Every case corresponds to a bug that actually
happened, including:

- A chapter assigned in week 7 filed under week 2, because the filename also
  contained the book title
- Hyphenated surnames (`Lévi-Strauss`) never matching, because the index kept the
  hyphen while filenames tokenize to letters
- Thresholds tuned against a 300-entry index silently rejecting everything for
  smaller ones — scores are now scale-independent
- Prose paragraphs in a syllabus parsed as citations, inflating the "missing" list
- A data source removed because *one* course returned 403, breaking every other
  course that relied on it

Add a case before changing a matching rule.

---

## Privacy

No account data is committed. `.gitignore` excludes:

| File | Why |
|---|---|
| `canvas_manifest.json` | **Contains signed download URLs** — publishing it hands out access to the material |
| `readings_index.json` | A full extract of your instructors' syllabi |
| `moves_log.csv` | Absolute paths, including your username |
| `courses.json`, `config.json`, `file_titles.json` | Local paths and course structure |

Nothing is uploaded anywhere. No access token is created or stored — the browser
snippet runs inside your existing session, and public-course fetching uses
signatures the server already hands out.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `build_index.py` reports 0 readings for a week | Wrong `parser` for that syllabus |
| Week folder dates look off after a holiday | A break is missing from `breaks` |
| Everything lands in "couldn't determine" | Index too small for default thresholds — lower `min_score` |
| Canvas returns 403 on files | Normal; per-file lookups are used instead |
| Snippet reports files but no weeks | Module/folder names don't match any `week_patterns` |
| `allow pasting` errors in the console | That text is only needed when Chrome prompts for it — otherwise just paste |

---

## License

MIT. See [LICENSE](LICENSE).
