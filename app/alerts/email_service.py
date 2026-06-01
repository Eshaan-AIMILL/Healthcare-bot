"""
Email Alert Service
Reads alert_rules.json, evaluates each rule's trigger SQL against the live
database, and sends SMTP email alerts when conditions are met.

Runs as a background scheduler (every 60 min) or can be triggered manually.
All SQL queries come from alert_rules.json — none are hardcoded here.
"""
import json
import os
import re
import smtplib
import asyncio
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from sqlalchemy import text

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.utils.logger import logger

_PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompt")
_SENT_LOG: dict[str, datetime] = {}   # rule_id → last sent time (in-memory cooldown)


def _load_alert_rules() -> dict:
    path = os.path.join(_PROMPT_DIR, "alert_rules.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _format_template(template: str, values: dict[str, Any]) -> str:
    """Replace {key} placeholders in subject/body with actual values."""
    for key, val in values.items():
        template = template.replace(f"{{{key}}}", str(val))
    return template


def _evaluate_condition(condition: str, row: dict[str, Any]) -> bool:
    """
    Safely evaluate a simple condition string like 'count > 0' or 'rate > 15'
    against the values returned from the trigger SQL.
    Supports: >, <, >=, <=, =, !=
    """
    match = re.match(r"(\w+)\s*(>|<|>=|<=|=|!=)\s*(.+)", condition.strip())
    if not match:
        logger.warning(f"Alert: unparseable condition '{condition}'")
        return False

    col, op, threshold_str = match.groups()
    actual = row.get(col)
    if actual is None:
        return False

    try:
        threshold = float(threshold_str.strip())
        actual    = float(actual)
    except (ValueError, TypeError):
        # String comparison fallback
        threshold_str = threshold_str.strip().strip("'\"")
        if op == "=":  return str(actual) == threshold_str
        if op == "!=": return str(actual) != threshold_str
        return False

    ops = {
        ">": actual > threshold,
        "<": actual < threshold,
        ">=": actual >= threshold,
        "<=": actual <= threshold,
        "=": actual == threshold,
        "!=": actual != threshold,
    }
    return ops.get(op, False)


def _is_on_cooldown(rule_id: str, cooldown_hours: int) -> bool:
    last_sent = _SENT_LOG.get(rule_id)
    if last_sent is None:
        return False
    return datetime.now() - last_sent < timedelta(hours=cooldown_hours)


def _send_email(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    from_addr: str,
    to_addrs: list[str],
    subject: str,
    body: str,
    use_tls: bool = True,
) -> bool:
    """Send an HTML+plain email. Returns True on success."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = from_addr
    msg["To"]      = ", ".join(to_addrs)

    plain_body = body
    html_body  = f"""
    <html><body style="font-family:Arial,sans-serif;font-size:14px;color:#333;">
    <div style="border-left:4px solid #e53e3e;padding:12px 20px;background:#fff8f8;margin-bottom:16px;">
      <strong style="color:#e53e3e;">Healthcare Operations Alert</strong>
    </div>
    <p>{body.replace(chr(10), '<br>')}</p>
    <hr style="border:none;border-top:1px solid #eee;margin:20px 0;">
    <p style="font-size:11px;color:#999;">
      Sent by Healthcare Bot Alert Service · {datetime.now().strftime('%Y-%m-%d %H:%M')}
      <br>Log in to <a href="http://localhost:8000/dashboard/index.html">the dashboard</a> for details.
    </p>
    </body></html>
    """

    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        if use_tls:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10)

        if smtp_user and smtp_password:
            server.login(smtp_user, smtp_password)

        server.sendmail(from_addr, to_addrs, msg.as_string())
        server.quit()
        logger.info(f"Alert email sent to {to_addrs}: {subject}")
        return True
    except Exception as exc:
        logger.error(f"Failed to send alert email: {exc}")
        return False


async def _evaluate_domain_rules(
    domain: str,
    domain_config: dict,
    cooldown_hours: int,
    smtp_settings: dict,
    dry_run: bool = False,
) -> list[dict]:
    """
    Evaluate all rules for one domain.
    Returns a list of fired alert dicts (for logging/testing).
    """
    if not domain_config.get("enabled", True):
        return []

    fired: list[dict] = []
    recipients = domain_config.get("recipients", [])

    async with AsyncSessionLocal() as db:
        for rule in domain_config.get("rules", []):
            rule_id   = rule["rule_id"]
            trigger   = rule["trigger_sql"]

            # ── Safety: only allow SELECT queries from alert_rules.json ───────
            if not trigger.strip().upper().startswith("SELECT"):
                logger.warning(f"Alert rule {rule_id} has non-SELECT SQL — skipped")
                continue

            # ── Cooldown check ────────────────────────────────────────────────
            if _is_on_cooldown(rule_id, cooldown_hours):
                logger.debug(f"Alert {rule_id} on cooldown — skipping")
                continue

            try:
                result = await db.execute(text(trigger))
                row = result.mappings().fetchone()
                if row is None:
                    continue

                row_dict = dict(row)

                if _evaluate_condition(rule["condition"], row_dict):
                    subject = _format_template(rule["subject"], row_dict)
                    body    = _format_template(rule["body_template"], row_dict)

                    logger.warning(
                        f"Alert FIRED | {rule_id} | {rule['severity']} | {subject}"
                    )
                    fired.append({
                        "rule_id":  rule_id,
                        "domain":   domain,
                        "severity": rule["severity"],
                        "subject":  subject,
                        "body":     body,
                        "values":   row_dict,
                    })

                    if not dry_run:
                        sent = _send_email(
                            smtp_host=smtp_settings["host"],
                            smtp_port=smtp_settings["port"],
                            smtp_user=smtp_settings.get("user", ""),
                            smtp_password=smtp_settings.get("password", ""),
                            from_addr=smtp_settings["from_address"],
                            to_addrs=recipients,
                            subject=subject,
                            body=body,
                            use_tls=smtp_settings.get("use_tls", True),
                        )
                        if sent:
                            _SENT_LOG[rule_id] = datetime.now()
                    else:
                        logger.info(f"[DRY RUN] Would send: {subject} → {recipients}")
                        _SENT_LOG[rule_id] = datetime.now()

            except Exception as exc:
                logger.error(f"Alert rule {rule_id} evaluation failed: {exc}")

    return fired


async def run_alert_check(dry_run: bool = False) -> list[dict]:
    """
    Main entry point — evaluates all domain rules and sends emails.
    Pass dry_run=True to test without sending actual emails.
    Returns list of all fired alerts.
    """
    rules_config = _load_alert_rules()
    cooldown     = rules_config["global"]["alert_cooldown_hours"]

    smtp_settings = {
        "host":         settings.smtp_host,
        "port":         settings.smtp_port,
        "user":         settings.smtp_user,
        "password":     settings.smtp_password,
        "from_address": settings.smtp_from,
        "use_tls":      settings.smtp_use_tls,
    }

    all_fired: list[dict] = []
    domains = ["billing", "compliance", "pharmacy", "patient", "dispatch"]

    for domain in domains:
        if domain not in rules_config:
            continue
        fired = await _evaluate_domain_rules(
            domain=domain,
            domain_config=rules_config[domain],
            cooldown_hours=cooldown,
            smtp_settings=smtp_settings,
            dry_run=dry_run,
        )
        all_fired.extend(fired)

    logger.info(
        f"Alert check complete — {len(all_fired)} alert(s) fired across "
        f"{len(domains)} domains"
    )
    return all_fired
