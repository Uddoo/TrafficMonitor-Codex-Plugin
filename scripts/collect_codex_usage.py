#!/usr/bin/env python3
"""Collect a small Codex usage snapshot for the TrafficMonitor plugin.

The script reads local Codex session/log databases only. It does not read
auth.json and does not make network requests.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


STALE_RATE_LIMIT_SECONDS = 6 * 60 * 60
MAX_SESSION_JSONL_BYTES = 25 * 1024 * 1024
MAX_SESSION_JSONL_FILES = 300


@dataclass
class RateLimitEvent:
    ts: int
    source: str
    payload: dict[str, Any]


def local_now() -> datetime:
    return datetime.now().astimezone()


def from_epoch(seconds: int | float | None) -> datetime | None:
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(float(seconds)).astimezone()
    except (OSError, OverflowError, ValueError):
        return None


def iso_timestamp_to_epoch_seconds(value: str | None) -> int | None:
    if not value:
        return None
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        return int(datetime.fromisoformat(normalized).timestamp())
    except ValueError:
        return None


def format_dt(dt: datetime | None) -> str:
    if dt is None:
        return "--"
    now = local_now()
    if dt.date() == now.date():
        return dt.strftime("%H:%M")
    return dt.strftime("%m-%d %H:%M")


def format_dt_relative_to(dt: datetime | None, now: datetime) -> str:
    if dt is None:
        return "--"
    if dt.date() == now.date():
        return dt.strftime("%H:%M")
    return dt.strftime("%m-%d %H:%M")


def reset_display_for_limits(
    primary_reset: datetime | None,
    secondary_reset: datetime | None,
    stale: bool,
    now: datetime,
) -> str:
    both_resets_expired = (
        primary_reset is not None
        and primary_reset <= now
        and secondary_reset is not None
        and secondary_reset <= now
    )
    prefix = "旧: " if stale or both_resets_expired else ""
    return (
        f"{prefix}5h {format_dt_relative_to(primary_reset, now)}"
        f" / 周 {format_dt_relative_to(secondary_reset, now)}"
    )


def format_duration(seconds: int | float | None) -> str:
    if seconds is None:
        return "--"
    seconds = int(seconds)
    if seconds <= 0:
        return "已重置"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{minutes}m"
    return f"{minutes}m"


def format_tokens(value: int | float | None) -> str:
    if value is None:
        return "--"
    value = int(value)
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1_000_000_000:
        return f"{sign}{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{sign}{value / 1_000_000:.1f}M"
    if value >= 10_000:
        return f"{sign}{value / 1_000:.0f}K"
    if value >= 1_000:
        return f"{sign}{value / 1_000:.1f}K"
    return f"{sign}{value}"


def brace_json_objects(text: str) -> Iterable[dict[str, Any]]:
    for start in [m.start() for m in re.finditer(r"\{", text)]:
        depth = 0
        in_string = False
        escaped = False
        for offset, ch in enumerate(text[start:], start=start):
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : offset + 1]
                    try:
                        yield json.loads(candidate)
                    except json.JSONDecodeError:
                        pass
                    break


def sqlite_connect_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def candidate_log_dbs(codex_home: Path) -> list[Path]:
    return [
        codex_home / "logs_2.sqlite",
        codex_home / "sqlite" / "logs_2.sqlite",
    ]


def candidate_state_dbs(codex_home: Path) -> list[Path]:
    return [
        codex_home / "state_5.sqlite",
        codex_home / "sqlite" / "state_5.sqlite",
    ]


def normalize_session_limit(limit: dict[str, Any]) -> dict[str, Any] | None:
    normalized = dict(limit)
    if "used_percent" not in normalized and "remaining_percent" in normalized:
        try:
            normalized["used_percent"] = 100 - float(normalized["remaining_percent"])
        except (TypeError, ValueError):
            return None
    if "used_percent" not in normalized:
        return None
    if "reset_at" not in normalized and "resets_at" in normalized:
        normalized["reset_at"] = normalized["resets_at"]
    return normalized


def session_rate_limit_payload(obj: dict[str, Any]) -> dict[str, Any] | None:
    if obj.get("type") != "event_msg":
        return None
    payload = obj.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    rate_limits = obj.get("rate_limits")
    if not isinstance(rate_limits, dict):
        rate_limits = payload.get("rate_limits")
    if not isinstance(rate_limits, dict):
        return None

    primary = rate_limits.get("primary")
    secondary = rate_limits.get("secondary")
    if not isinstance(primary, dict) and not isinstance(secondary, dict):
        return None

    normalized_limits: dict[str, Any] = {}
    if isinstance(primary, dict):
        normalized_primary = normalize_session_limit(primary)
        if normalized_primary is not None:
            normalized_limits["primary"] = normalized_primary
    if isinstance(secondary, dict):
        normalized_secondary = normalize_session_limit(secondary)
        if normalized_secondary is not None:
            normalized_limits["secondary"] = normalized_secondary
    if not normalized_limits:
        return None

    return {
        "type": "codex.rate_limits",
        "plan_type": obj.get("plan_type") or rate_limits.get("plan_type") or "--",
        "rate_limits": normalized_limits,
        "code_review_rate_limits": None,
        "credits": rate_limits.get("credits"),
    }


def candidate_session_jsonl_files(codex_home: Path) -> list[Path]:
    sessions_dir = codex_home / "sessions"
    if not sessions_dir.exists():
        return []
    files: list[Path] = []
    for path in sessions_dir.rglob("*.jsonl"):
        try:
            if path.is_file() and path.stat().st_size <= MAX_SESSION_JSONL_BYTES:
                files.append(path)
        except OSError:
            continue
    files.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
    return files[:MAX_SESSION_JSONL_FILES]


def find_latest_rate_limits_from_sessions(codex_home: Path) -> RateLimitEvent | None:
    best: RateLimitEvent | None = None
    for path in candidate_session_jsonl_files(codex_home):
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    if '"event_msg"' not in line or '"rate_limits"' not in line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    payload = session_rate_limit_payload(obj)
                    if payload is None:
                        continue
                    ts = iso_timestamp_to_epoch_seconds(obj.get("timestamp"))
                    if ts is None:
                        try:
                            ts = int(path.stat().st_mtime)
                        except OSError:
                            ts = 0
                    event = RateLimitEvent(ts, f"sessions:{path.name}", payload)
                    if best is None or event.ts > best.ts:
                        best = event
        except OSError:
            continue
    return best


def find_latest_rate_limits(codex_home: Path) -> RateLimitEvent | None:
    session_event = find_latest_rate_limits_from_sessions(codex_home)
    if session_event is not None:
        return session_event

    best: RateLimitEvent | None = None
    query = """
        select id, ts, feedback_log_body
        from logs
        where feedback_log_body like '%codex.rate_limits%'
           or feedback_log_body like '%usage_limit_reached%'
        order by id desc
        limit 400
    """
    for db in candidate_log_dbs(codex_home):
        if not db.exists():
            continue
        try:
            with sqlite_connect_ro(db) as con:
                for row_id, ts, body in con.execute(query):
                    if not body:
                        continue
                    for obj in brace_json_objects(body):
                        payload: dict[str, Any] | None = None
                        if obj.get("type") == "codex.rate_limits":
                            payload = obj
                        elif obj.get("type") == "error" and (obj.get("error") or {}).get("type") == "usage_limit_reached":
                            resets_at = obj.get("resets_at") or (obj.get("error") or {}).get("resets_at")
                            payload = {
                                "type": "codex.rate_limits",
                                "plan_type": obj.get("plan_type") or obj.get("headers", {}).get("X-Codex-Plan-Type"),
                                "rate_limits": {
                                    "allowed": False,
                                    "limit_reached": True,
                                    "primary": {
                                        "used_percent": 100,
                                        "window_minutes": 300,
                                        "reset_at": resets_at,
                                        "reset_after_seconds": obj.get("resets_in_seconds")
                                        or (obj.get("error") or {}).get("resets_in_seconds"),
                                    },
                                    "secondary": {},
                                },
                            }
                        if payload is None:
                            continue
                        event = RateLimitEvent(int(ts or 0), f"{db.name}#{row_id}", payload)
                        if best is None or event.ts > best.ts:
                            best = event
                        break
        except sqlite3.Error:
            continue
    return best


def find_today_tokens_from_logs(codex_home: Path, day: datetime) -> tuple[int | None, str, int]:
    start = datetime(day.year, day.month, day.day, tzinfo=day.tzinfo).timestamp()
    end = (datetime(day.year, day.month, day.day, tzinfo=day.tzinfo) + timedelta(days=1)).timestamp()
    per_turn: dict[str, int] = {}
    pattern = re.compile(r"turn_id=([0-9a-f-]+).*?total_usage_tokens=(\d+)")
    for db in candidate_log_dbs(codex_home):
        if not db.exists():
            continue
        try:
            with sqlite_connect_ro(db) as con:
                rows = con.execute(
                    """
                    select feedback_log_body
                    from logs
                    where ts >= ? and ts < ?
                      and feedback_log_body like '%post sampling token usage%'
                    """,
                    (int(start), int(end)),
                )
                for (body,) in rows:
                    if not body:
                        continue
                    match = pattern.search(body)
                    if not match:
                        continue
                    turn_id, total = match.group(1), int(match.group(2))
                    per_turn[turn_id] = max(per_turn.get(turn_id, 0), total)
        except sqlite3.Error:
            continue
    if not per_turn:
        return None, "logs.post_sampling:no_rows", 0
    return sum(per_turn.values()), "logs.post_sampling", len(per_turn)


def find_today_tokens_from_state(codex_home: Path, day: datetime) -> tuple[int | None, str, int]:
    start = int(datetime(day.year, day.month, day.day, tzinfo=day.tzinfo).timestamp())
    end = int((datetime(day.year, day.month, day.day, tzinfo=day.tzinfo) + timedelta(days=1)).timestamp())
    best_updated = -1
    best_result: tuple[int | None, str, int] = (None, "threads:no_state_db", 0)

    for db in candidate_state_dbs(codex_home):
        if not db.exists():
            continue
        try:
            with sqlite_connect_ro(db) as con:
                max_updated = con.execute("select coalesce(max(updated_at), 0) from threads").fetchone()[0] or 0
                created_rows = con.execute(
                    """
                    select coalesce(sum(tokens_used), 0), count(*)
                    from threads
                    where created_at >= ? and created_at < ?
                    """,
                    (start, end),
                ).fetchone()
                updated_rows = con.execute(
                    """
                    select coalesce(sum(tokens_used), 0), count(*)
                    from threads
                    where updated_at >= ? and updated_at < ?
                    """,
                    (start, end),
                ).fetchone()
        except sqlite3.Error:
            continue

        if max_updated < best_updated:
            continue
        best_updated = max_updated
        created_total, created_count = int(created_rows[0] or 0), int(created_rows[1] or 0)
        updated_total, updated_count = int(updated_rows[0] or 0), int(updated_rows[1] or 0)
        if created_count > 0:
            best_result = (created_total, "threads.created_today", created_count)
        elif updated_count > 0:
            best_result = (updated_total, "threads.updated_today_fallback", updated_count)
        else:
            best_result = (0, "threads.no_today_rows", 0)
    return best_result


def bounded_percent(value: Any) -> int | None:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return None


def quota_percent_display(limit: dict[str, Any] | None, stale: bool, now: datetime) -> tuple[str, int | None, int | None]:
    if not limit:
        return "--", None, None

    used_int = bounded_percent(limit.get("used_percent"))
    remaining_int = bounded_percent(limit.get("remaining_percent"))
    if remaining_int is None and used_int is not None:
        remaining_int = max(0, min(100, 100 - used_int))
    if used_int is None and remaining_int is not None:
        used_int = max(0, min(100, 100 - remaining_int))
    if remaining_int is None:
        return "--", used_int, None

    reset_at = from_epoch(limit.get("reset_at"))
    old = stale or (reset_at is not None and reset_at <= now)
    suffix = " 旧" if old else ""
    return f"{remaining_int}%{suffix}", used_int, remaining_int


def build_snapshot(codex_home: Path) -> dict[str, Any]:
    now = local_now()
    generated_at = now.isoformat(timespec="seconds")

    tokens, token_source, token_count = find_today_tokens_from_logs(codex_home, now)
    if tokens is None:
        tokens, token_source, token_count = find_today_tokens_from_state(codex_home, now)

    event = find_latest_rate_limits(codex_home)
    status = "ok"
    messages: list[str] = []
    plan_type = "--"
    rate_source = "not_found"
    five_display = "--"
    week_display = "--"
    reset_display = "--"
    five_used: int | None = None
    week_used: int | None = None
    five_remaining: int | None = None
    week_remaining: int | None = None
    rate_age_seconds: int | None = None

    if event is None:
        status = "partial"
        messages.append("未找到 codex.rate_limits 日志事件；额度显示为空")
    else:
        payload = event.payload
        plan_type = str(payload.get("plan_type") or "--")
        rate_source = event.source
        rate_age_seconds = max(0, int(now.timestamp()) - event.ts)
        stale = rate_age_seconds > STALE_RATE_LIMIT_SECONDS
        if stale:
            status = "stale"
            messages.append("额度事件较旧，百分比仅供参考")

        limits = payload.get("rate_limits") or {}
        primary = limits.get("primary") or {}
        secondary = limits.get("secondary") or {}
        five_display, five_used, five_remaining = quota_percent_display(primary, stale, now)
        week_display, week_used, week_remaining = quota_percent_display(secondary, stale, now)

        primary_reset = from_epoch(primary.get("reset_at"))
        secondary_reset = from_epoch(secondary.get("reset_at"))
        primary_reset_in = None if primary_reset is None else (primary_reset - now).total_seconds()
        secondary_reset_in = None if secondary_reset is None else (secondary_reset - now).total_seconds()
        reset_display = reset_display_for_limits(primary_reset, secondary_reset, stale, now)
        if primary_reset and primary_reset > now:
            messages.append(f"5h 重置 {format_duration(primary_reset_in)}")
        if secondary_reset and secondary_reset > now:
            messages.append(f"周重置 {format_duration(secondary_reset_in)}")

    if tokens is None:
        today_tokens_display = "--"
        if status == "ok":
            status = "partial"
        messages.append("未能统计今日 Token")
    else:
        today_tokens_display = format_tokens(tokens)

    message = "；".join(messages) if messages else "正常"
    return {
        "schema_version": 1,
        "status": status,
        "message": message,
        "generated_at_local": generated_at,
        "codex_home": str(codex_home),
        "plan_type": plan_type,
        "rate_limits_source": rate_source,
        "rate_limits_age_seconds": rate_age_seconds,
        "rate_limits_age_minutes": None if rate_age_seconds is None else round(rate_age_seconds / 60, 1),
        "five_hour_display": five_display,
        "five_hour_used_percent": five_used,
        "five_hour_remaining_percent": five_remaining,
        "weekly_display": week_display,
        "weekly_used_percent": week_used,
        "weekly_remaining_percent": week_remaining,
        "reset_display": reset_display,
        "today_tokens": tokens,
        "today_tokens_display": today_tokens_display,
        "today_token_source": token_source,
        "today_token_rows": token_count,
    }


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass


def default_codex_home() -> Path:
    env_home = os.environ.get("CODEX_HOME")
    if env_home:
        return Path(env_home).expanduser()
    return Path.home() / ".codex"


def default_output_path(codex_home: Path) -> Path:
    override = os.environ.get("CODEX_TRAFFICMONITOR_USAGE_JSON")
    if override:
        return Path(override).expanduser()
    return codex_home / "trafficmonitor" / "codex_usage_status.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex-home", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    codex_home = Path(args.codex_home).expanduser() if args.codex_home else default_codex_home()
    output = Path(args.output).expanduser() if args.output else default_output_path(codex_home)

    try:
        payload = build_snapshot(codex_home)
    except Exception as exc:  # noqa: BLE001 - this is a background status writer.
        payload = {
            "schema_version": 1,
            "status": "error",
            "message": f"{type(exc).__name__}: {exc}",
            "generated_at_local": local_now().isoformat(timespec="seconds"),
            "five_hour_display": "--",
            "weekly_display": "--",
            "reset_display": "采集失败",
            "today_tokens_display": "--",
            "rate_limits_source": "error",
            "today_token_source": "error",
        }
    write_json_atomic(output, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
