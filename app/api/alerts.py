"""
Alerts API Router
Exposes endpoints to manually trigger alert checks and view recent fired alerts.
Alert history is now persisted in the database via the AlertHistory model.
"""
from fastapi import APIRouter, Query
from sqlalchemy import select, desc, func

from app.alerts.email_service import run_alert_check, _SENT_LOG
from app.config import settings
from app.db.session import AsyncSessionLocal
from app.db.models import AlertHistory

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


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

    return {
        "fired_count":      len(fired),
        "dry_run":          dry_run,
        "emails_enabled":   settings.alert_emails_enabled,
        "alerts":           fired,
    }


@router.get("/recent")
async def get_recent_alerts(
    limit: int = Query(default=50, ge=1, le=200, description="Max alerts to return"),
) -> dict:
    """Return recently fired alerts from the database."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AlertHistory)
            .order_by(desc(AlertHistory.fired_at))
            .limit(limit)
        )
        alerts = result.scalars().all()

        alert_list = [
            {
                "alert_id":    a.alert_id,
                "rule_id":     a.rule_id,
                "domain":      a.domain,
                "severity":    a.severity,
                "subject":     a.subject,
                "body":        a.body,
                "email_sent":  a.email_sent,
                "dry_run":     a.dry_run,
                "fired_at":    a.fired_at.isoformat() if a.fired_at else None,
            }
            for a in alerts
        ]

    return {
        "count":            len(alert_list),
        "emails_enabled":   settings.alert_emails_enabled,
        "smtp_configured":  bool(settings.smtp_user),
        "alerts":           alert_list,
    }


@router.get("/status")
async def get_alert_status() -> dict:
    """Return alert system configuration and cooldown status."""
    # Get count of alerts in last 24 hours from database
    async with AsyncSessionLocal() as db:
        from datetime import datetime, timedelta
        cutoff = datetime.utcnow() - timedelta(hours=24)
        result = await db.execute(
            select(func.count()).where(AlertHistory.fired_at > cutoff)
        )
        alerts_last_24h = result.scalar_one()

    return {
        "emails_enabled":            settings.alert_emails_enabled,
        "smtp_host":                 settings.smtp_host,
        "smtp_port":                 settings.smtp_port,
        "smtp_configured":           bool(settings.smtp_user),
        "check_interval_minutes":    settings.alert_check_interval_minutes,
        "alerts_last_24h":           alerts_last_24h,
        "cooldown_cache": {
            rule_id: str(last_sent)
            for rule_id, last_sent in _SENT_LOG.items()
        },
    }
