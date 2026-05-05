"""Automatic Feishu token refresh.

Provides:
- ``refresh_feishu_token()`` — one-shot refresh, suitable for cron jobs.
- ``ensure_token_refresh_job()`` — register a 6-hourly cron job at gateway startup.
"""

from __future__ import annotations

import time
from pathlib import Path

from filelock import FileLock
from loguru import logger


def refresh_feishu_token(
    app_id: str,
    app_secret: str,
    token_dir: str = "~/.theos/feishu_tokens",
) -> dict:
    """Refresh the Feishu access token using the stored refresh token.

    Returns a dict with ``ok``, ``access_token_ttl``, ``refresh_token_ttl``,
    and optionally ``error``.
    """
    from src.feishu.token import (
        get_refresh_token,
        refresh_token_from_api,
        save_access_token,
        save_refresh_token,
    )

    lock_path = Path(token_dir).expanduser() / ".refresh.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(lock_path), timeout=10)

    try:
        with lock:
            old_refresh, rt_ttl = get_refresh_token(token_dir=token_dir)

            if rt_ttl < 0:
                return {
                    "ok": False,
                    "error": f"Refresh token expired {-rt_ttl}s ago. Re-authorization required.",
                }

            data = refresh_token_from_api(old_refresh, app_id=app_id, app_secret=app_secret)

            epoch_now = int(time.time())
            save_access_token(
                data["access_token"],
                epoch_now + data["expires_in"],
                token_dir=token_dir,
            )
            save_refresh_token(
                data["refresh_token"],
                epoch_now + data["refresh_token_expires_in"],
                token_dir=token_dir,
            )
    except (ValueError, RuntimeError) as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"Unexpected error: {e}"}

    at_ttl = data.get("expires_in", 7200)
    rt_ttl = data.get("refresh_token_expires_in", 2592000)
    logger.info(
        "Feishu token refreshed: access_token TTL={}s, refresh_token TTL={}s ({:.1f}d)",
        at_ttl,
        rt_ttl,
        rt_ttl / 86400,
    )
    return {
        "ok": True,
        "access_token_ttl": at_ttl,
        "refresh_token_ttl": rt_ttl,
    }


def ensure_token_refresh_job(cron_service) -> None:
    """Register a Feishu token refresh cron job (every 6 hours).

    Called at gateway startup.  The job is a ``system_event`` so it bypasses
    the agent loop and runs ``handle_token_refresh_event`` directly.

    Runs every 6 hours to keep the refresh_token alive (30-day rolling window).
    Each refresh resets the 30-day clock, so as long as the gateway runs at
    least once every 30 days the token never expires.
    """
    from src.cron.types import CronSchedule

    job_name = "feishu-token-refresh"

    for job in cron_service.list_jobs(include_disabled=True):
        if job.name == job_name and job.enabled:
            # Upgrade: if old job uses daily schedule, replace with 6-hourly
            if job.schedule.expr == "0 4 * * *":
                logger.info("Upgrading Feishu token refresh from daily to every 6h")
                cron_service.remove_job(job.id)
                break
            logger.info("Feishu token refresh job already registered ({})", job.id)
            return

    # Run every 6 hours (00:00, 06:00, 12:00, 18:00 CST)
    schedule = CronSchedule(kind="cron", expr="0 */6 * * *", tz="Asia/Shanghai")
    cron_service.add_job(
        name=job_name,
        schedule=schedule,
        message="feishu_token_refresh",
        kind="system_event",
    )
    logger.info("Registered Feishu token refresh job (every 6 hours CST)")


def handle_token_refresh_event(config, bus=None) -> str:
    """Handle the ``feishu_token_refresh`` system event.

    Called by the cron dispatcher when the system_event message matches.
    When *bus* is provided, sends a proactive re-auth notification to the
    owner if the refresh_token is about to expire (< 7 days).
    """
    fs = config.channels.feishu
    if not fs.app_id or not fs.app_secret:
        return "Feishu not configured, skipping token refresh."

    result = refresh_feishu_token(
        app_id=fs.app_id,
        app_secret=fs.app_secret,
        token_dir=fs.token_dir,
    )

    if result["ok"]:
        rt_ttl = result["refresh_token_ttl"]
        rt_days = rt_ttl / 86400

        # Proactive warning when refresh_token < 7 days remaining
        if rt_days < 7 and bus:
            _send_reauth_warning(config, bus, rt_days)

        return (
            f"✅ Feishu token refreshed. "
            f"access_token TTL={result['access_token_ttl']}s, "
            f"refresh_token TTL={rt_ttl}s "
            f"({rt_days:.1f}d)"
        )
    else:
        error = result["error"]
        # If refresh failed (likely expired), notify owner to re-auth
        if bus and "expired" in error.lower():
            _send_reauth_expired(config, bus)
        return f"❌ Feishu token refresh failed: {error}"


def _send_reauth_warning(config, bus, days_remaining: float) -> None:
    """Send a warning to the owner that refresh_token is expiring soon."""
    msg = (
        f"⚠️ 飞书 refresh_token 将在 {days_remaining:.1f} 天后过期。\n"
        "请尽快重新授权，否则到期后飞书工具将不可用。\n\n"
        "回复「授权飞书」或使用 feishu_auth 工具重新授权。"
    )
    _send_reauth_message(config, bus, msg, "re-auth warning")


def _send_reauth_expired(config, bus) -> None:
    """Notify the owner that the refresh_token has expired."""
    msg = (
        "❌ 飞书 refresh_token 已过期，飞书工具暂时不可用。\n"
        "请重新授权：回复「授权飞书」或使用 feishu_auth 工具。"
    )
    _send_reauth_message(config, bus, msg, "re-auth expired notification")


def _send_reauth_message(config, bus, msg: str, log_label: str) -> None:
    """Publish a Feishu re-auth message to the configured owner, if available."""
    import asyncio

    from src.bus.events import OutboundMessage

    owner = config.channels.owner_ids[0] if config.channels.owner_ids else None
    if not owner:
        return

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            bus.publish_outbound(OutboundMessage(channel="feishu", chat_id=owner, content=msg))
        )
    except RuntimeError:
        logger.warning("Cannot send {}: no running event loop", log_label)
