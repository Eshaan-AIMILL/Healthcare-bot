"""
Alert Scheduler
Runs alert checks on a configurable interval using asyncio.
Started automatically when the FastAPI app starts.
"""
import asyncio
from app.alerts.email_service import run_alert_check
from app.utils.logger import logger
from app.config import settings


async def alert_scheduler_loop() -> None:
    """
    Infinite loop that runs alert checks every N minutes.
    Designed to run as a background asyncio task.
    """
    interval_seconds = settings.alert_check_interval_minutes * 60
    logger.info(
        f"Alert scheduler started — checking every "
        f"{settings.alert_check_interval_minutes} minutes"
    )

    # Small initial delay so DB is fully ready before first check
    await asyncio.sleep(30)

    while True:
        try:
            logger.info("Running scheduled alert check...")
            fired = await run_alert_check(dry_run=not settings.alert_emails_enabled)
            if fired:
                logger.warning(f"Alert scheduler: {len(fired)} alert(s) fired")
            else:
                logger.info("Alert scheduler: no alerts triggered")
        except Exception as exc:
            logger.error(f"Alert scheduler error: {exc}")

        await asyncio.sleep(interval_seconds)


def start_alert_scheduler(app) -> None:
    """Called from FastAPI lifespan to start the background task."""
    import asyncio
    loop = asyncio.get_event_loop()
    loop.create_task(alert_scheduler_loop())
