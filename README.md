# Trainer Central Assignment Tracking Agent

Automatically pushes my Trainer Central assignment submissions to the trainer's dashboard.
Every 30 minutes it reads new `.txt` email files from a shared Google Drive folder, asks Claude
whether each one is my submission and extracts the details, then POSTs them to the dashboard API.

## Architecture

```
Windows Task Scheduler (every 30 min, pythonw -m src.main)
        │
        ▼
 Google Drive API (OAuth, read-only) ── list .txt in folder ──► skip files already in SQLite
        │ download new file text
        ▼
 Claude (claude-sonnet-5-5, structured JSON output)
   "Is this a TrainerCentral submission for Rohith Rathan / designerrohith@gmail.com?"
   → assignment name, date, time, status, course, submission ID, message ID, ...
        │ is_mine = true
        ▼
 Duplicate guards: SQLite (posted_submissions) + GET /api/entries?q=<submission id>
        │ not yet posted
        ▼
 POST /api/entries  {"text": "...details...", "sender": "Rohith Rathan"}  → dashboard
```

| File | Role |
|---|---|
| `src/main.py` | One run: orchestration, logging, per-file error isolation, run lock |
| `src/drive_client.py` | Drive OAuth + listing/downloading `.txt` files |
| `src/analyzer.py` | Claude prompt + Pydantic output schema |
| `src/dashboard.py` | Dashboard API client: health wake-up, retries, duplicate lookup, POST |
| `src/state.py` | SQLite state (`data/processed.db`) |
| `src/config.py` | Reads all settings from `.env` |
| `scripts/register_task.ps1` | Creates the Task Scheduler job |

## Setup

1. **Python 3.10+** (tested on 3.14). Create the venv in a short path, because the Anthropic SDK
   has long file names that exceed the Windows 260-char path limit inside deep folders:
   ```powershell
   C:\Python314\python.exe -m venv $env:USERPROFILE\.venvs\trainer-central
   & $env:USERPROFILE\.venvs\trainer-central\Scripts\python.exe -m pip install -r requirements.txt
   ```
2. **Google OAuth client:** in Google Cloud Console enable the Drive API, create an OAuth client
   of type *Desktop app*, download its JSON and save it as `credentials.json` in the project root.
3. **Configuration:** `copy .env.example .env` and fill in `ANTHROPIC_API_KEY`, `GDRIVE_FOLDER_ID`,
   `LEARNER_NAME`, `LEARNER_EMAIL`, `DASHBOARD_SENDER` (and `DASHBOARD_API_KEY` if the trainer issues one).
4. **Authorize Drive once** (opens a browser, writes `token.json`) and confirm the folder is readable:
   ```powershell
   & $env:USERPROFILE\.venvs\trainer-central\Scripts\python.exe -m src.test_drive
   ```

## Running

```powershell
$py = "$env:USERPROFILE\.venvs\trainer-central\Scripts\python.exe"
& $py -m src.main --dry-run   # analyze everything, post nothing, record nothing
& $py -m src.main             # real run
```

## Scheduling

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register_task.ps1    # every 30 min
powershell -ExecutionPolicy Bypass -File .\scripts\unregister_task.ps1  # remove
```
The task `TrainerCentralAssignmentTracker` runs `pythonw.exe -m src.main` (no console window) every
30 minutes as the current user while logged on. It ignores new starts while a run is still going and
catches up on missed runs (`StartWhenAvailable`) after sleep. A 30-minute interval keeps the delay
well under the 1-hour requirement.

## Logs

`logs/tracker.log` (rotating, 2 MB × 5). Each run logs the files seen, the result for each file
(`not mine` / `match` / `posted` / `duplicate` / `empty` / `error`) and a summary line:
```
=== Run finished: seen=9, new=9, empty=0, not_mine=8, matched=1, posted=1, duplicates=0, errors=0 ===
```

## Reliability and duplicate prevention

- **Processed files:** every file ID is recorded in SQLite. Files that were posted, aren't mine, are
  empty or are duplicates are never re-analyzed. Failed files are retried on later runs (up to 5 attempts).
- **No duplicate entries:** before posting, a submission's key (TrainerCentral `assignmentSubmissionId`,
  else the email Message ID) is checked locally *and* on the dashboard (`GET /api/entries?q=<id>` filtered
  by sender). The same submission saved twice under different file names, or a lost local database,
  still doesn't cause a second entry.
- **Errors:** an unreachable Drive or an expired token is logged and the run exits. Claude/API errors
  affect only that file. Render cold starts are handled by `GET /health` with a 90 s timeout and
  exponential-backoff retries on 429/5xx/timeouts. A 401/403 tells you to set `DASHBOARD_API_KEY`.
- **Overlapping runs:** a lock file in `data/` prevents them.

## Security

All secrets live in `.env`, `credentials.json` and `token.json`, which are gitignored along with
`data/`, `logs/` and virtual environments. `.env.example` holds placeholders only. The Drive scope
is read-only. The email text is passed to Claude as untrusted data inside `<email>` tags.

## Assumptions

- The dashboard host is `trainer-central-assigments-api.onrender.com` (the spelling in the
  assignment PDF). The `...assignments-api...` spelling returns 404.
- `x-api-key` is optional per `openapi.json`, so it's sent only when `DASHBOARD_API_KEY` is set.
- Matching is on the learner email `designerrohith@gmail.com` or the name "Rohith Rathan".
  The dashboard `sender` is always "Rohith Rathan".
- The scheduled task runs only while the user is logged on, because the OAuth token lives in the
  user's project folder and no password is stored.
