"""
Alert Scheduler
Runs alert checks on a configurable interval using asyncio.
Started automatically when the FastAPI app starts.
Supports graceful shutdown via an asyncio.Event.
"""
import asyncio
from app.alerts.email_service import run_alert_check
from app.utils.logger import logger
from app.config import settings

# ── Shutdown event for graceful termination ───────────────────────────────────
_shutdown_event = asyncio.Event()


def request_shutdown() -> None:
    """Signal the scheduler loop to stop gracefully."""
    _shutdown_event.set()
    logger.info("Alert scheduler shutdown requested")


async def alert_scheduler_loop() -> None:
    """
    Infinite loop that runs alert checks every N minutes.
    Designed to run as a background asyncio task.
    Respects _shutdown_event for graceful termination.
    """
    interval_seconds = settings.alert_check_interval_minutes * 60
    logger.info(
        f"Alert scheduler started — checking every "
        f"{settings.alert_check_interval_minutes} minutes"
    )

    # Small initial delay so DB is fully ready before first check
    try:
        await asyncio.wait_for(_shutdown_event.wait(), timeout=30)
        logger.info("Alert scheduler shutting down during initial delay")
        return
    except asyncio.TimeoutError:
        pass  # Normal: the 30-second initial delay expired, proceed

    while not _shutdown_event.is_set():
        try:
            logger.info("Running scheduled alert check...")
            fired = await run_alert_check(dry_run=not settings.alert_emails_enabled)
            if fired:
                logger.warning(f"Alert scheduler: {len(fired)} alert(s) fired")
            else:
                logger.info("Alert scheduler: no alerts triggered")
        except Exception as exc:
            logger.error(f"Alert scheduler error: {exc}")

        # Wait for the interval, but break early if shutdown is requested
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=interval_seconds)
            break  # Shutdown was requested during the wait
        except asyncio.TimeoutError:
            continue  # Normal: the interval elapsed, run next check

    logger.info("Alert scheduler has stopped gracefully")
