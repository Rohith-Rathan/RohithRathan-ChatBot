"""One scheduled run: Drive -> Claude -> dashboard.

Usage:
    python -m src.main             # normal run (what Task Scheduler executes)
    python -m src.main --dry-run   # analyze but do not POST or record anything
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from src.analyzer import Analyzer, EmailAnalysis
from src.config import load_settings
from src.dashboard import DashboardClient, DashboardError
from src.drive_client import DriveClient, get_credentials
from src.state import StateStore

log = logging.getLogger("tracker")


def setup_logging(log_dir) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if root.handlers:
        return
    fmt =logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(log_dir / "tracker.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    if sys.stdout is not None:  # pythonw.exe has no console
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(fmt)
        root.addHandler(console)
    for noisy in ("googleapiclient", "urllib3", "httpx", "httpx2", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def dedupe_key(a: EmailAnalysis, file_id: str) -> str:
    if a.submission_id:
        return f"submission:{a.submission_id}"
    if a.message_id:
        return f"message:{a.message_id}"
    return f"file:{file_id}"


def format_entry(a: EmailAnalysis, sender: str, email: str, file_name: str) -> str:
    when = " ".join(x for x in (a.submission_date, a.submission_time, a.timezone) if x) or "unknown time"
    lines = [
        f"{sender} ({email}) {a.status} the assignment '{a.assignment_name or 'unknown'}' on {when}.",
        f"Assignment: {a.assignment_name or 'unknown'}",
        f"Submission date: {a.submission_date or 'unknown'}",
        f"Submission time: {a.submission_time or 'unknown'} {a.timezone or ''}".rstrip(),
        f"Status: {a.status}",
    ]
    if a.course:
        lines.append(f"Course: {a.course}")
    if a.submission_id:
        lines.append(f"Submission ID: {a.submission_id}")
    if a.message_id:
        lines.append(f"Message ID: {a.message_id}")
    if a.other_details:
        lines.append(f"Details: {a.other_details}")
    lines.append(f"Source file: {file_name}")
    return "\n".join(lines)


def acquire_lock(path):
    """Prevent overlapping runs. Returns file handle or None if another run holds the lock."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        return fd
    except FileExistsError:
        # Stale lock (older than 1 hour) is taken over.
        import time
        if time.time() - os.path.getmtime(path) > 3600:
            os.remove(path)
            return acquire_lock(path)
        return None


def run(dry_run: bool) -> int:
    s = load_settings()
    setup_logging(s.log_dir)
    log.info("=== Run started%s ===", " (dry run)" if dry_run else "")

    lock_path = s.db_path.parent / "run.lock"
    s.db_path.parent.mkdir(parents=True, exist_ok=True)
    lock = acquire_lock(lock_path)
    if lock is None:
        log.warning("Another run is in progress; exiting.")
        return 0

    stats = dict(seen=0, new=0, empty=0, not_mine=0, matched=0, posted=0, duplicates=0, errors=0)
    state = StateStore(s.db_path)
    try:
        try:
            creds = get_credentials(s.google_credentials_file, s.google_token_file, interactive=False)
            drive = DriveClient(creds)
            files = drive.list_txt_files(s.gdrive_folder_id)
        except Exception as e:
            log.error("Google Drive unreachable or not authorized: %s", e)
            return 1

        stats["seen"] = len(files)
        pending = [f for f in files if state.should_process(f["id"])]
        stats["new"] = len(pending)
        log.info("Drive: %d .txt file(s) seen, %d to process", len(files), len(pending))
        if not pending:
            return 0

        analyzer = Analyzer(s.anthropic_api_key, s.anthropic_model, s.learner_name, s.learner_email)
        dashboard = DashboardClient(s.dashboard_base_url, s.dashboard_api_key)
        dashboard_awake = False

        for f in pending:
            name = f["name"]
            try:
                text = drive.download_text(f["id"])
                if not text.strip():
                    log.info("[empty]     %s", name)
                    stats["empty"] += 1
                    if not dry_run:
                        state.record(f, "empty")
                    continue

                analysis = analyzer.analyze(name, text)
                if not analysis.is_mine:
                    log.info("[not mine]  %s (%s)", name, analysis.match_reason)
                    stats["not_mine"] += 1
                    if not dry_run:
                        state.record(f, "not_mine", analysis=analysis)
                    continue

                stats["matched"] += 1
                key = dedupe_key(analysis, f["id"])
                entry_text = format_entry(analysis, s.dashboard_sender, s.learner_email, name)
                log.info("[match]     %s -> %s | %s %s", name, analysis.assignment_name,
                         analysis.submission_date, analysis.submission_time)

                if dry_run:
                    log.info("Dry run, would POST:\n%s", entry_text)
                    continue

                if state.is_submission_posted(key):
                    log.info("[duplicate] %s already posted (%s)", name, key)
                    stats["duplicates"] += 1
                    state.record(f, "duplicate", analysis=analysis, dedupe_key=key)
                    continue

                if not dashboard_awake:
                    dashboard.wake_up()
                    dashboard_awake = True

                # Second guard: the entry may exist if a previous run crashed after POSTing.
                marker = analysis.submission_id or analysis.message_id
                existing = dashboard.find_existing(marker, s.dashboard_sender) if marker else None
                if existing:
                    log.info("[duplicate] %s already on dashboard (entry %s)", name, existing.get("id"))
                    stats["duplicates"] += 1
                    state.mark_submission_posted(key, f["id"], existing.get("id"))
                    state.record(f, "duplicate", analysis=analysis, dedupe_key=key, entry_id=existing.get("id"))
                    continue

                result = dashboard.post_entry(entry_text, s.dashboard_sender)
                entry_id = result.get("id") or result.get("_id")
                log.info("[posted]    %s -> entry %s", name, entry_id)
                stats["posted"] += 1
                state.mark_submission_posted(key, f["id"], entry_id)
                state.record(f, "posted", analysis=analysis, dedupe_key=key, entry_id=entry_id)

            except Exception as e:  # never let one file stop the run
                stats["errors"] += 1
                log.error("[error]     %s: %s: %s", name, type(e).__name__, e)
                if not dry_run:
                    state.record(f, "error", error=f"{type(e).__name__}: {e}"[:1000])
                if isinstance(e, DashboardError) and "set DASHBOARD_API_KEY" in str(e):
                    log.error("Stopping posts for this run: dashboard authentication failed.")
                    break
        return 1 if stats["errors"] else 0
    finally:
        log.info("=== Run finished: %s ===", ", ".join(f"{k}={v}" for k, v in stats.items()))
        state.close()
        os.close(lock)
        try:
            os.remove(lock_path)
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Trainer Central assignment tracker")
    parser.add_argument("--dry-run", action="store_true", help="Analyze without posting or recording state")
    args = parser.parse_args()
    try:
        return run(args.dry_run)
    except Exception as e:
        logging.getLogger("tracker").exception("Fatal error: %s", e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
