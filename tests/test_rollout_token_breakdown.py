import importlib.util
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "collect_codex_usage.py"


def load_collector_module():
    spec = importlib.util.spec_from_file_location("collect_codex_usage", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_token_event(path, timestamp, input_tokens, output_tokens, cached_tokens):
    payload = {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cached_input_tokens": cached_tokens,
                }
            },
        },
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


class RolloutTokenBreakdownTest(unittest.TestCase):
    def test_existing_thread_uses_last_pre_midnight_token_count_as_today_baseline(self):
        collector = load_collector_module()
        day = datetime(2026, 7, 1, 12, 0, tzinfo=timezone(timedelta(hours=8)))
        start = datetime(day.year, day.month, day.day, tzinfo=day.tzinfo)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int((start + timedelta(days=1)).timestamp() * 1000)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            rollout_path = Path(temp_dir) / "thread-before-midnight.jsonl"
            write_token_event(rollout_path, "2026-06-30T23:50:00+08:00", 900, 90, 300)
            write_token_event(rollout_path, "2026-07-01T00:30:00+08:00", 1_000, 120, 350)
            write_token_event(rollout_path, "2026-07-01T11:30:00+08:00", 1_500, 190, 500)

            breakdown, event_count = collector.scan_rollout_token_usage(
                rollout_path,
                start_ms,
                end_ms,
                created_ms=start_ms - 60_000,
            )

        self.assertEqual(event_count, 2)
        self.assertEqual(breakdown, {"input": 600, "output": 100, "cached": 200})

    def test_snapshot_uses_rollout_token_count_breakdown(self):
        collector = load_collector_module()
        fixed_now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone(timedelta(hours=8)))
        collector.local_now = lambda: fixed_now
        collector.find_latest_rate_limits = lambda codex_home: None

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            codex_home = Path(temp_dir)
            rollout_dir = codex_home / "rollouts"
            rollout_dir.mkdir(parents=True)
            rollout_path = rollout_dir / "thread-a.jsonl"
            write_token_event(rollout_path, "2026-07-01T01:00:00+08:00", 1_000, 100, 600)
            write_token_event(rollout_path, "2026-07-01T11:30:00+08:00", 2_500, 280, 1_100)

            db = codex_home / "state_5.sqlite"
            with sqlite3.connect(db) as con:
                con.execute(
                    """
                    create table threads (
                        id text,
                        rollout_path text,
                        tokens_used integer,
                        created_at integer,
                        updated_at integer,
                        created_at_ms integer,
                        updated_at_ms integer
                    )
                    """
                )
                con.execute(
                    """
                    insert into threads
                    (id, rollout_path, tokens_used, created_at, updated_at, created_at_ms, updated_at_ms)
                    values (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "thread-a",
                        str(rollout_path),
                        999_999,
                        1782837000,
                        1782876600,
                        1782837000000,
                        1782876600000,
                    ),
                )

            snapshot = collector.build_snapshot(codex_home)

        self.assertEqual(snapshot["today_token_source"], "rollouts.token_count")
        self.assertEqual(snapshot["today_token_rows"], 1)
        self.assertEqual(snapshot["today_input_tokens"], 2500)
        self.assertEqual(snapshot["today_output_tokens"], 280)
        self.assertEqual(snapshot["today_cached_input_tokens"], 1100)
        self.assertEqual(snapshot["today_input_tokens_display"], "2.5K")
        self.assertEqual(snapshot["today_output_tokens_display"], "280")
        self.assertEqual(snapshot["today_cached_input_tokens_display"], "1.1K")


if __name__ == "__main__":
    unittest.main()
