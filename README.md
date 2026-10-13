# Job tracker

A local, single-user tracker for job applications. It keeps the job description and the exact CV sent for each application, scores how well the CV matches the job, and records every status change on a timeline.

It runs on your own machine, stores everything in SQLite and a data folder, and listens only on `127.0.0.1`.

**Status:** phase 1 of 5 (tracker, CV handling, status pipeline, deterministic ATS score). AI scoring, Telegram reminders and job search come next, see [Roadmap](#roadmap).

<!-- Screenshots: add docs/screenshots/list.png and docs/screenshots/detail.png -->
| Applications list | Application detail |
| --- | --- |
| _screenshot placeholder_ | _screenshot placeholder_ |

## What it does

- **Applications.** Add, edit and archive applications: company, title, location, source, contact, notes. Paste the job description or give a link; the app fetches the page and stores the text, because postings get taken down. LinkedIn links are not fetched (their terms forbid scraping), so paste those.
- **Duplicate warning.** Adding the same company with a similar title shows the existing application. Company names are normalized, so "Check Point Software Technologies Ltd." and "Check Point" are the same company.
- **CV versions.** Upload PDF or DOCX files per application. Each upload is a new version and the newest becomes the "sent" one; older versions are kept and you can mark any of them as sent. Files are stored once by content hash, so the same CV attached to ten jobs is one file on disk.
- **Status pipeline.** Saved, Applied, HR screen, Technical interview (rounds counted), Home assignment, Final interview, Offer, and the closed states Accepted, Rejected, Withdrawn, Ghosted. Every change is logged with a date and an optional note. Dates can be backdated. The last change can be undone; undone entries stay on the timeline, crossed out.
- **"Expected answer by"** per stage, for when a recruiter gives a date. Phase 3 uses it to decide when a follow-up is due.
- **Deterministic ATS score** for every CV version, with the full breakdown (see below).
- **Hebrew.** JDs and CVs in Hebrew or mixed Hebrew/English are stored as UTF-8, shown right-to-left where needed, and handled by the section detection and keyword matching.

## Setup

Requires Python 3.11 or newer.

```bash
git clone <this repo> job-tracker
cd job-tracker
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env      # nothing is required in phase 1; defaults work
python -m app
```

Open http://127.0.0.1:8000.

The database schema is created and upgraded automatically on startup (Alembic). To run migrations by hand: `alembic upgrade head`.

Data lives in `data/` (set `DATA_DIR` in `.env` to move it):

```
data/
├── app.db                      SQLite database
└── cvs/ab/ab12...ef.pdf        CV files, named by SHA-256
```

Back up that folder and you have everything.

## How the deterministic score works

The score is 0-100 and reproducible: the same CV text and JD text always give the same number (for the same version of `synonyms.yaml` and the same weights).

1. **Split the JD.** Lines are sorted into required, preferred and other, using headings such as "Requirements", "Must have", "Nice to have", "Advantage", "דרישות", "יתרון". A sentence under Requirements that says "an advantage" / "a plus" / "יתרון" counts as preferred. Skills mentioned only under "Responsibilities" or "About us" are ignored. If the JD has no recognizable headings, the whole text is treated as requirements and the page says so.
2. **Find skills.** [`app/scoring/synonyms.yaml`](app/scoring/synonyms.yaml) maps aliases to canonical skills: `cpp` and `c++17` are C++, `k8s` is Kubernetes, `לינוקס` is Linux. Matching understands C++, C#, TCP/IP and Hebrew prefix letters (`בלינוקס`), and does not match C inside "Objective-C" or Java inside "JavaScript".
3. **Compare with the CV.** Each JD skill is *matched* (in the CV), *implied* (the CV has a more specific skill: TCP/IP implies Networking) or *missing*.
4. **Keyword score.** Required skills weigh 3, preferred 1. Matched earns full credit, implied 75%.
5. **Format score.** 100 minus 10 per format warning:
   - no extractable text (scanned PDF): scoring is skipped, not scored as 0
   - missing email or phone; phone not a valid Israeli number (`05X-XXXXXXX`, `+972-5X-...`, landlines, `07X`)
   - no 4-digit years, or `'21`-style years
   - more than one page
   - content in DOCX text boxes, tables or page headers, which many ATS parsers skip (read from the DOCX XML)
   - multi-column PDF layout or contact details at the page edge (PDF heuristics; the warning says so)
   - Hebrew in a PDF that extracts in reversed order (corrected for scoring, flagged because parsers have the same problem)
6. **Total** = 80% keyword score + 20% format score.

The detail page shows the sub-scores, every skill with its status, missing required skills, format warnings, and which JD headings were found. Weights are in the `scoring` setting (`app/services/settings.py`).

**Extending the skill list:** add aliases to `synonyms.yaml`. A skill the file doesn't know about cannot be matched deterministically; the AI half of the score (phase 2) covers those.

## Tests

```bash
pytest
```

Covered: keyword normalization and matching (English, Hebrew, edge cases), JD section detection, the score calculation and its reproducibility, format checks and Israeli phone validation, DOCX/PDF extraction, CV and application dedupe, the stage pipeline including undo, JD fetching (no network), and the HTTP routes end to end against a migrated database.

## Project layout

```
app/
├── main.py            app factory, migrations on startup
├── config.py          .env settings (secrets and paths only)
├── models/            SQLAlchemy models
├── services/          business logic: applications, pipeline, CV storage,
│                      text extraction, JD fetching, settings
├── scoring/           normalization, synonyms.yaml, JD section detection,
│                      matching, format checks, deterministic score
└── web/               routes, Jinja2 templates, static files (htmx is vendored)
migrations/            Alembic
tests/                 pytest, with sample JDs in tests/fixtures
```

## Security notes

- The server binds to `127.0.0.1`. Requests with an unexpected `Host` header are refused (protects against DNS rebinding), and state-changing requests from another origin are refused (protects against cross-site form posts from pages open in the same browser).
- There is no login. Anyone with access to your user account on the machine can open it.
- Secrets go in `.env`, which is gitignored.

## Known limitations

- Hebrew matching has no morphology beyond attached prefix letters. Hebrew-only skill phrasing matches only when it is in `synonyms.yaml`.
- PDF layout checks are heuristics. DOCX checks are reliable.
- Old `.doc` files are not supported; save as DOCX or PDF.
- Fetching a JD from a link works for most company career pages and ATS-hosted postings (Greenhouse, Lever, Comeet and similar publish structured data). Pages that need JavaScript or a login fall back to pasting.

## Roadmap

1. ~~Tracker, CV versions, status pipeline, deterministic score~~ (done)
2. AI half of the score via the Anthropic API, combined score, caching
3. Follow-up thresholds in Israeli business days, overdue banner, Telegram bot with daily digest and inline buttons
4. Job search: public job board APIs (Greenhouse, Lever, Comeet) plus Claude web search, filtering, "Add to tracker"
5. Dashboard stats, systemd unit, README for Telegram setup and running as a service
