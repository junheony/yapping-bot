"""Google Sheets logging utilities."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials
from loguru import logger

from app.config import settings
from app.models import PostRecord


class GoogleSheetsLogger:
    """Append logs to Google Sheets if configured."""

    def __init__(self) -> None:
        self.enabled = bool(
            settings.google_service_account_json and settings.google_sheets_log_id
        )
        self._client: Optional[gspread.Client] = None
        self._log_sheet = None
        self._queue_sheet = None

        if self.enabled:
            try:
                credentials = Credentials.from_service_account_file(
                    settings.google_service_account_json,
                    scopes=["https://www.googleapis.com/auth/spreadsheets"],
                )
                self._client = gspread.authorize(credentials)
                self._log_sheet = self._client.open_by_key(settings.google_sheets_log_id).sheet1
                if settings.google_sheets_queue_id:
                    self._queue_sheet = self._client.open_by_key(settings.google_sheets_queue_id).sheet1
            except Exception as exc:  # pragma: no cover - external dependency
                logger.error("Failed to initialise Google Sheets client: {}", exc)
                self.enabled = False

    async def log_post(self, record: PostRecord, status: str) -> None:
        """Append a row to the log sheet."""

        if not self.enabled or not self._log_sheet:
            return

        row = [
            datetime.utcnow().isoformat(),
            record.hash,
            record.title,
            record.url,
            record.source or "",
            record.source_type or "",
            record.lang or "",
            record.tags or "",
            status,
            record.channels or "",
            record.posted_at.isoformat() if record.posted_at else "",
            record.error_msg or "",
            record.prompt_version or "",
            str(record.token_usage or ""),
        ]

        await asyncio.to_thread(
            self._log_sheet.append_row,
            row,
            value_input_option="USER_ENTERED",
        )

    async def log_queue_enqueue(self, record: PostRecord, note: str) -> None:
        """Append a row to the queue sheet."""

        if not self.enabled or not self._queue_sheet:
            return

        row = [
            datetime.utcnow().isoformat(),
            record.hash,
            record.title,
            record.url,
            record.source or "",
            record.tags or "",
            note,
        ]
        await asyncio.to_thread(
            self._queue_sheet.append_row,
            row,
            value_input_option="USER_ENTERED",
        )
