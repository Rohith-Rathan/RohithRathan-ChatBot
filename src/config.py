"""Loads configuration from the project's .env file. No secrets live in code."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _get(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    value = value.strip() if value else ""
    if required and not value:
        raise RuntimeError(f"Missing required setting {name} in .env")
    return value


def _path(name: str, default: str) -> Path:
    p = Path(_get(name, default))
    return p if p.is_absolute() else PROJECT_ROOT / p


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    anthropic_model: str
    gdrive_folder_id: str
    google_credentials_file: Path
    google_token_file: Path
    learner_name: str
    learner_email: str
    dashboard_base_url: str
    dashboard_api_key: str
    dashboard_sender: str
    db_path: Path
    log_dir: Path


def load_settings(require_anthropic: bool = True) -> Settings:
    return Settings(
        anthropic_api_key=_get("ANTHROPIC_API_KEY", required=require_anthropic),
        anthropic_model=_get("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        gdrive_folder_id=_get("GDRIVE_FOLDER_ID", required=True),
        google_credentials_file=_path("GOOGLE_CREDENTIALS_FILE", "credentials.json"),
        google_token_file=_path("GOOGLE_TOKEN_FILE", "token.json"),
        learner_name=_get("LEARNER_NAME", required=True),
        learner_email=_get("LEARNER_EMAIL", required=True),
        dashboard_base_url=_get("DASHBOARD_BASE_URL", required=True).rstrip("/"),
        dashboard_api_key=_get("DASHBOARD_API_KEY", ""),
        dashboard_sender=_get("DASHBOARD_SENDER", required=True),
        db_path=_path("DB_PATH", "data/processed.db"),
        log_dir=_path("LOG_DIR", "logs"),
    )
