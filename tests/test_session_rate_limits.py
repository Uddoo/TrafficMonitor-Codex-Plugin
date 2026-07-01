import importlib.util
import json
import sys
import tempfile
from datetime import datetime
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


class SessionRateLimitsTest(unittest.TestCase):
    def test_reads_rate_limits_from_codex_session_jsonl(self):
        collector = load_collector_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            session_dir = codex_home / "sessions" / "2026" / "06" / "30"
            session_dir.mkdir(parents=True)
            session_file = session_dir / "rollout-2026-06-30T19-24-59-example.jsonl"
            payload = {
                "timestamp": "2026-06-30T11:24:59.000Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": {"total_tokens": 123}},
                    "rate_limits": {
                        "limit_id": "codex",
                        "primary": {
                            "remaining_percent": 8,
                            "window_minutes": 300,
                            "resets_at": 1782818694,
                        },
                        "secondary": {
                            "used_percent": 26,
                            "window_minutes": 10080,
                            "resets_at": 1783389180,
                        },
                        "plan_type": "prolite",
                    },
                },
            }
            session_file.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

            event = collector.find_latest_rate_limits(codex_home)

            self.assertIsNotNone(event)
            self.assertEqual(event.source, "sessions:rollout-2026-06-30T19-24-59-example.jsonl")
            self.assertEqual(event.ts, 1782818699)
            self.assertEqual(event.payload["plan_type"], "prolite")
            self.assertEqual(event.payload["rate_limits"]["primary"]["used_percent"], 92)
            self.assertEqual(event.payload["rate_limits"]["primary"]["reset_at"], 1782818694)
            self.assertEqual(event.payload["rate_limits"]["secondary"]["used_percent"], 26)
            self.assertEqual(event.payload["rate_limits"]["secondary"]["reset_at"], 1783389180)

    def test_snapshot_redacts_unique_ids_from_rate_limit_source(self):
        collector = load_collector_module()
        fixed_now = datetime.fromtimestamp(1782818400).astimezone()
        collector.local_now = lambda: fixed_now
        collector.find_today_tokens_from_logs = lambda codex_home, now: (12345, "test.tokens", 1)

        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            session_dir = codex_home / "sessions" / "2026" / "06" / "30"
            session_dir.mkdir(parents=True)
            session_file = session_dir / "rollout-2026-06-30T19-24-59-11111111-2222-3333-4444-555555555555.jsonl"
            payload = {
                "timestamp": "2026-06-30T11:24:59.000Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "primary": {
                            "remaining_percent": 80,
                            "window_minutes": 300,
                            "resets_at": 1782818694,
                        }
                    },
                },
            }
            session_file.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

            snapshot = collector.build_snapshot(codex_home)

        self.assertEqual(
            snapshot["rate_limits_source"],
            "sessions:rollout-2026-06-30T19-24-59-<id>.jsonl",
        )
        self.assertNotIn("11111111-2222-3333-4444-555555555555", json.dumps(snapshot))


if __name__ == "__main__":
    unittest.main()
