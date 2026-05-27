"""
Alerts API Router
Exposes endpoints to manually trigger alert checks and view recent fired alerts.
"""
from fastapi import APIRouter, Query
from app.alerts.email_service import run_alert_check, _SENT_LOG
from app.config import settings

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

# In-memory log of recent fired alerts (last 100)
_recent_alerts: list[dict] = []


@router.post("/run")
async def trigger_alert_check(
    dry_run: bool = Query(
        default=True,
        description="If true, evaluates rules and returns results without sending emails"
    )
) -> dict:
    """
    Manually trigger an alert check across all 5 domains.
    Use dry_run=true to test without sending emails.
    """
    fired = await run_alert_check(dry_run=dry_run)

    # Keep last 100 in memory
    _recent_alerts.extend(fired)
    if len(_recent_alerts) > 100:
        del _recent_alerts[:-100]

    return {
        "fired_count":      len(fired),
        "dry_run":          dry_run,
        "emails_enabled":   settings.alert_emails_enabled,
        "alerts":           fired,
    }


@router.get("/recent")
async def get_recent_alerts() -> dict:
    """Return recently fired alerts from the in-memory log."""
    return {
        "count":            len(_recent_alerts),
        "emails_enabled":   settings.alert_emails_enabled,
        "smtp_configured":  bool(settings.smtp_user),
        "alerts":           list(reversed(_recent_alerts)),
    }


@router.get("/status")
async def get_alert_status() -> dict:
    """Return alert system configuration and cooldown status."""
    return {
        "emails_enabled":            settings.alert_emails_enabled,
        "smtp_host":                 settings.smtp_host,
        "smtp_port":                 settings.smtp_port,
        "smtp_configured":           bool(settings.smtp_user),
        "check_interval_minutes":    settings.alert_check_interval_minutes,
        "cooldown_status": {
            rule_id: str(last_sent)
            for rule_id, last_sent in _SENT_LOG.items()
        },
    }
