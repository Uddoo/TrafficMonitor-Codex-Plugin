#!/usr/bin/env python3
"""Collect a small Codex usage snapshot for the TrafficMonitor plugin."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


STALE_RATE_LIMIT_SECONDS = 6 * 60 * 60
MAX_SESSION_JSONL_BYTES = 25 * 1024 * 1024
MAX_SESSION_JSONL_FILES = 300
RESET_CREDITS_URL = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"
RESET_CREDITS_TIMEOUT_SECONDS = 6.0
UNIQUE_ID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


@dataclass
class RateLimitEvent:
    ts: int
    source: str
    payload: dict[str, Any]


class ResetCreditsUnauthorizedError(Exception):
    pass


class ResetCreditsRequestError(Exception):
    pass


def local_now() -> datetime:
    return datetime.now().astimezone()


def sanitize_unique_ids(value: str) -> str:
    return UNIQUE_ID_RE.sub("<id>", value)


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


def default_reset_credits_snapshot(status: str = "not_found", message: str = "") -> dict[str, Any]:
    return {
        "reset_credits_status": status,
        "reset_credits_message": message,
        "reset_credits_available_count": None,
        "reset_credits": [],
        "reset_credits_tooltip": "",
    }


def read_codex_access_token(codex_home: Path) -> str | None:
    auth_path = codex_home / "auth.json"
    try:
        payload = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        return None
    access_token = tokens.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        return None
    return access_token


def fetch_reset_credits_response(access_token: str, timeout_seconds: float = RESET_CREDITS_TIMEOUT_SECONDS) -> dict[str, Any]:
    request = urllib.request.Request(
        RESET_CREDITS_URL,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "TrafficMonitor-CodexUsage/0.1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            body = response.read(1024 * 1024).decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise ResetCreditsUnauthorizedError() from None
        raise ResetCreditsRequestError(f"http_status:{exc.code}") from None
    except (OSError, TimeoutError) as exc:
        raise ResetCreditsRequestError(type(exc).__name__) from None

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ResetCreditsRequestError(type(exc).__name__) from None
    if not isinstance(payload, dict):
        raise ResetCreditsRequestError("unexpected_response")
    return payload


def local_datetime_from_utc_value(value: Any) -> datetime | None:
    if value is None or value == "":
        return None

    seconds: float | None = None
    if isinstance(value, int | float):
        seconds = float(value)
    elif isinstance(value, str) and re.fullmatch(r"\d+(\.\d+)?", value.strip()):
        seconds = float(value.strip())

    if seconds is not None:
        if seconds > 100_000_000_000:
            seconds /= 1000
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone()
        except (OSError, OverflowError, ValueError):
            return None

    try:
        text = str(value).strip()
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def format_reset_credit_dt(value: Any) -> str:
    dt = local_datetime_from_utc_value(value)
    if dt is None:
        return "--"
    return dt.strftime("%Y-%m-%d %H:%M")


def normalize_reset_credit(credit: Any) -> dict[str, str] | None:
    if not isinstance(credit, dict):
        return None
    return {
        "status": str(credit.get("status") or "--"),
        "title": str(credit.get("title") or "--"),
        "granted_at": format_reset_credit_dt(credit.get("granted_at")),
        "expires_at": format_reset_credit_dt(credit.get("expires_at")),
    }


def build_reset_credits_tooltip(available_count: int | None, credits: list[dict[str, str]]) -> str:
    if available_count is None:
        return ""

    lines = [f"重置卡: {available_count} 张可用"]
    for index, credit in enumerate(credits, start=1):
        lines.append(f"  {index}. {credit['status']} | {credit['title']}")
        lines.append(f"     获取 {credit['granted_at']}    过期 {credit['expires_at']}")
    return "\r\n".join(lines) + "\r\n"


def collect_reset_credits(codex_home: Path) -> dict[str, Any]:
    access_token = read_codex_access_token(codex_home)
    if access_token is None:
        return default_reset_credits_snapshot("missing_auth")

    try:
        payload = fetch_reset_credits_response(access_token, RESET_CREDITS_TIMEOUT_SECONDS)
    except ResetCreditsUnauthorizedError:
        return default_reset_credits_snapshot("unauthorized", "401: 凭证失效或未携带 Authorization header")
    except ResetCreditsRequestError as exc:
        return default_reset_credits_snapshot("error", str(exc))

    raw_credits = payload.get("credits")
    if not isinstance(raw_credits, list):
        raw_credits = []
    credits = [credit for credit in (normalize_reset_credit(item) for item in raw_credits) if credit is not None]

    raw_available_count = payload.get("available_count")
    available_count: int | None
    try:
        available_count = int(raw_available_count)
    except (TypeError, ValueError):
        available_count = len([credit for credit in credits if credit["status"] == "available"]) if credits else None

    return {
        "reset_credits_status": "ok",
        "reset_credits_message": "正常",
        "reset_credits_available_count": available_count,
        "reset_credits": credits,
        "reset_credits_tooltip": build_reset_credits_tooltip(available_count, credits),
    }


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


def iso_timestamp_to_epoch_milliseconds(value: str | None) -> int | None:
    if not value:
        return None
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        return int(datetime.fromisoformat(normalized).timestamp() * 1000)
    except ValueError:
        return None


def normalize_rollout_path(codex_home: Path, value: Any) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("\\\\?\\"):
        text = text[4:]
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = codex_home / path
    return path


def token_usage_totals(payload: dict[str, Any]) -> dict[str, int]:
    info = payload.get("info") or {}
    usage = info.get("total_token_usage") or {}

    def token_int(key: str) -> int:
        try:
            return int(usage.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "input": token_int("input_tokens"),
        "output": token_int("output_tokens"),
        "cached": token_int("cached_input_tokens"),
    }


def scan_rollout_token_usage(
    path: Path,
    start_ms: int,
    end_ms: int,
    created_ms: int | None,
) -> tuple[dict[str, int] | None, int]:
    if not path.exists():
        return None, 0

    baseline: dict[str, int] | None = None
    latest: dict[str, int] | None = None
    event_count = 0
    try:
        with path.open("rb") as handle:
            for raw_line in handle:
                line = raw_line.decode("utf-8", errors="replace")
                if '"token_count"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = obj.get("payload") or {}
                if payload.get("type") != "token_count":
                    continue
                event_ms = iso_timestamp_to_epoch_milliseconds(obj.get("timestamp"))
                if event_ms is None or event_ms < start_ms or event_ms >= end_ms:
                    continue
                event_count += 1
                totals = token_usage_totals(payload)
                if baseline is None:
                    baseline = {"input": 0, "output": 0, "cached": 0}
                    if created_ms is None or created_ms < start_ms:
                        baseline = totals
                latest = totals
    except OSError:
        return None, event_count

    if latest is None or baseline is None:
        return None, event_count
    return {
        "input": max(0, latest["input"] - baseline["input"]),
        "output": max(0, latest["output"] - baseline["output"]),
        "cached": max(0, latest["cached"] - baseline["cached"]),
    }, event_count


def find_today_token_breakdown_from_rollouts(codex_home: Path, day: datetime) -> tuple[dict[str, int] | None, str, int]:
    start = datetime(day.year, day.month, day.day, tzinfo=day.tzinfo)
    end = start + timedelta(days=1)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    best_updated = -1
    best_result: tuple[dict[str, int] | None, str, int] = (None, "rollouts:no_state_db", 0)

    for db in candidate_state_dbs(codex_home):
        if not db.exists():
            continue
        try:
            con = sqlite_connect_ro(db)
            try:
                max_updated = con.execute("select coalesce(max(updated_at), 0) from threads").fetchone()[0] or 0
                rows = con.execute(
                    """
                    select id, rollout_path, created_at_ms, created_at, updated_at_ms, updated_at
                    from threads
                    where coalesce(updated_at_ms, updated_at * 1000) >= ?
                      and coalesce(updated_at_ms, updated_at * 1000) < ?
                    """,
                    (start_ms, end_ms),
                ).fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            continue

        if max_updated < best_updated:
            continue
        best_updated = max_updated
        totals = {"input": 0, "output": 0, "cached": 0}
        threads_with_tokens = 0
        for _thread_id, rollout_path, created_at_ms, created_at, _updated_at_ms, _updated_at in rows:
            path = normalize_rollout_path(codex_home, rollout_path)
            if path is None:
                continue
            try:
                created_ms = int(created_at_ms) if created_at_ms is not None else None
            except (TypeError, ValueError):
                created_ms = None
            if created_ms is None and created_at is not None:
                try:
                    created_ms = int(created_at) * 1000
                except (TypeError, ValueError):
                    created_ms = None
            breakdown, _event_count = scan_rollout_token_usage(path, start_ms, end_ms, created_ms)
            if breakdown is None:
                continue
            threads_with_tokens += 1
            for key in totals:
                totals[key] += int(breakdown.get(key) or 0)

        if threads_with_tokens > 0:
            best_result = (totals, "rollouts.token_count", threads_with_tokens)
        else:
            best_result = (None, "rollouts:no_token_count", 0)
    return best_result


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
    reset_credits = collect_reset_credits(codex_home)

    token_breakdown, token_source, token_count = find_today_token_breakdown_from_rollouts(codex_home, now)
    if token_breakdown is not None:
        tokens = token_breakdown["input"] + token_breakdown["output"]
    else:
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
        rate_source = sanitize_unique_ids(event.source)
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

    if token_breakdown is None:
        today_input_tokens = None
        today_output_tokens = None
        today_cached_input_tokens = None
        today_input_tokens_display = "--"
        today_output_tokens_display = "--"
        today_cached_input_tokens_display = "--"
    else:
        today_input_tokens = token_breakdown["input"]
        today_output_tokens = token_breakdown["output"]
        today_cached_input_tokens = token_breakdown["cached"]
        today_input_tokens_display = format_tokens(today_input_tokens)
        today_output_tokens_display = format_tokens(today_output_tokens)
        today_cached_input_tokens_display = format_tokens(today_cached_input_tokens)

    message = "；".join(messages) if messages else "正常"
    snapshot = {
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
        "today_input_tokens": today_input_tokens,
        "today_input_tokens_display": today_input_tokens_display,
        "today_output_tokens": today_output_tokens,
        "today_output_tokens_display": today_output_tokens_display,
        "today_cached_input_tokens": today_cached_input_tokens,
        "today_cached_input_tokens_display": today_cached_input_tokens_display,
    }
    snapshot.update(reset_credits)
    return snapshot


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
            "reset_credits_status": "error",
            "reset_credits_message": "采集失败",
            "reset_credits_available_count": None,
            "reset_credits": [],
            "reset_credits_tooltip": "",
        }
    write_json_atomic(output, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
