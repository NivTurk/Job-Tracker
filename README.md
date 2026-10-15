# Job tracker

A local, single-user tracker for job applications. It keeps the job description and the exact CV sent for each application, scores how well the CV matches the job, and records every status change on a timeline.

It runs on your own machine, stores everything in SQLite and a data folder, and listens only on `127.0.0.1`.

**Status:** phase 2 of 5 (tracker, CV handling, status pipeline, deterministic and AI ATS scores). Telegram reminders and job search come next, see [Roadmap](#roadmap).

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
- **AI score** (optional, needs an Anthropic API key): Claude reads the JD and the CV and returns a score, strengths, missing must-haves, weak matches and 3-5 concrete edits. Results are cached, so each CV/JD pair costs one call until you re-score it.
- **Combined score** of the two, with an adjustable weight. The list, filters and sorting use it.
- **Hebrew.** JDs and CVs in Hebrew or mixed Hebrew/English are stored as UTF-8, shown right-to-left where needed, and handled by the section detection and keyword matching.

## Setup

Requires Python 3.11 or newer.

```bash
git clone <this repo> job-tracker
cd job-tracker
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env      # optional: add ANTHROPIC_API_KEY for the AI score
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

### Turning on the AI score

1. Sign in at [platform.claude.com](https://platform.claude.com/) and create a key at [Settings > API keys](https://platform.claude.com/settings/keys). It starts with `sk-ant-` and is shown once.
2. Put it in `.env`:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```
3. Check it works (one tiny call, a fraction of a cent):
   ```bash
   python -m app.ai.check
   ```
   It should end with `OK. AI scoring will work.`
4. Restart the app. The Settings page shows that AI scoring is on and which model it uses.
5. Add an application with a CV, or upload a new CV version: the AI score runs in the background and the score panel updates when it's done (usually 5-20 seconds). For applications you added before setting the key, press **Score with AI** in the ATS score panel.

If the first call fails with a billing error, add credits under Billing in the Console. Without a key the app runs as before and shows deterministic scores only.

**Cost.** The default model is `claude-sonnet-5-5` ($2 / $10 per million input / output tokens). A score sends about 4,000 tokens and gets about 800 back, roughly $0.016. With auto-scoring on, that's one call per new CV version and one per JD edit; nothing is scored twice. Settings shows calls, tokens and estimated cost for the last 30 days and all time, plus the last 10 calls. To change the model, edit `MODEL` in `app/ai/client.py` (and add its prices to `PRICES` there).

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

**Extending the skill list:** add aliases to `synonyms.yaml`. A skill the file doesn't know about cannot be matched deterministically; the AI score covers those.

## How the AI score works

1. **Request.** `app/ai/client.py` sends the JD and the CV text to the Claude API with a fixed prompt (`app/ai/prompt.py`) and a JSON schema, using structured outputs so the reply is JSON in that shape.
2. **Validation.** The reply is checked locally: score is an integer from 0 to 100, 3-5 edit suggestions, no missing, extra or empty fields. (The API's schema support can't express ranges or counts, so these are checked here.) Extra strengths or weak matches beyond the limits are trimmed.
3. **One retry.** If the reply fails validation, it is retried once: with the validation error sent back, or as the original request if the reply was cut off (with a higher token limit) or refused. If the second reply also fails, the AI score is marked failed with the reason, and the combined score uses the deterministic score alone.
4. **API down.** Connection errors, rate limits and server errors are retried by the SDK (2 retries with backoff). If it still fails, nothing is stored, the AI score stays "pending" with the error shown, and the rest of the app keeps working.
5. **Cache.** A valid result is stored under `sha256(CV text + JD text + model + prompt version)`. Editing the CV or JD, changing the model or bumping `PROMPT_VERSION` means a new key, so the score shows as pending until re-scored. The same CV and JD in two applications share one result. **Re-score** always makes a fresh call; if that call fails, the previous good result stays.
6. **Auto-scoring.** With the setting on (Settings > AI scoring, on by default), a score is queued in the background when you attach a CV (new application or new version) or change the JD text (only the sent version is re-scored, to keep it to one call). It is skipped when that exact CV + JD already has a cached result. While it runs, the score panel says so, hides the button and checks every 3 seconds; when it finishes, the page reloads once. A failure is shown on the page like a manual one, and is not retried automatically. If the app stops mid-score, the score just shows as pending.
7. **Logging.** Every API attempt, including failures and retries, is a row in `ai_calls` with tokens, estimated cost, latency and the request ID.

**Combined score** = (1 - AI share) x deterministic + AI share x AI, rounded half up. The AI share is 50% by default and set on the Settings page (0 ignores the AI score, 100 ignores the deterministic one). When only one half exists, it is shown alone and labelled "deterministic only" or "AI only". The combined score is computed when the page loads, so changing the weight updates every application.

The AI result is not proof of anything: it can misread a CV like a person can. Check its edit suggestions against what you actually did before using them.

## Tests

```bash
pytest
```

Covered: keyword normalization and matching (English, Hebrew, edge cases), JD section detection, the score calculation and its reproducibility, format checks and Israeli phone validation, DOCX/PDF extraction, CV and application dedupe, the stage pipeline including undo, JD fetching (no network), and the HTTP routes end to end against a migrated database.

AI scoring tests never call the API. They cover the combined score math, output validation (malformed JSON, out-of-range score, wrong suggestion count, missing fields, refusal, cut-off replies), the retry-once path, the fallback when the API is down or replies are invalid twice, caching and cache keys, cost estimates, the settings weight, auto-scoring triggers (new application, new CV version, JD edit, cache hits, setting off, API down, the running state and polling), and the real SDK request shape against a mocked HTTP transport.

## Project layout

```
app/
├── main.py            app factory, migrations on startup
├── config.py          .env settings (secrets and paths only)
├── ai/                Claude client (model, timeout, retries, prices),
│                      prompt, JSON schema and output validation
├── models/            SQLAlchemy models
├── services/          business logic: applications, pipeline, CV storage,
│                      text extraction, JD fetching, settings, AI scores
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
- With AI scoring on, the text of the JD and CV you score is sent to the Anthropic API: automatically when you attach a CV or edit a JD (if auto-scoring is on), or when you press the button.

## Known limitations

- Hebrew matching has no morphology beyond attached prefix letters. Hebrew-only skill phrasing matches only when it is in `synonyms.yaml`.
- PDF layout checks are heuristics. DOCX checks are reliable.
- Old `.doc` files are not supported; save as DOCX or PDF.
- The AI score was tested against a mocked API only. The request format follows the October 2026 API docs; the first real call is the real test.
- Fetching a JD from a link works for most company career pages and ATS-hosted postings (Greenhouse, Lever, Comeet and similar publish structured data). Pages that need JavaScript or a login fall back to pasting.

## Roadmap

1. ~~Tracker, CV versions, status pipeline, deterministic score~~ (done)
2. ~~AI half of the score via the Anthropic API, combined score, caching~~ (done)
3. Follow-up thresholds in Israeli business days, overdue banner, Telegram bot with daily digest and inline buttons
4. Job search: public job board APIs (Greenhouse, Lever, Comeet) plus Claude web search, filtering, "Add to tracker"
5. Dashboard stats, systemd unit, README for Telegram setup and running as a service
