"""Logging setup using Loguru."""

from loguru import logger

from app.config import settings


def setup_logging() -> None:
    """Configure Loguru sinks."""

    logger.remove()
    logger.add(
        sink=lambda msg: print(msg, end=""),
        level="DEBUG" if settings.app_env == "local" else "INFO",
        backtrace=False,
        diagnose=False,
    )
