import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "collect_codex_usage.py"
SOURCE_PATH = ROOT / "src" / "CodexUsagePlugin.cpp"


def load_collector_module():
    spec = importlib.util.spec_from_file_location("collect_codex_usage", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def local_text(utc_text):
    return datetime.fromisoformat(utc_text.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")


class ResetCreditsCollectorTest(unittest.TestCase):
    def test_snapshot_skips_reset_credits_when_disabled(self):
        collector = load_collector_module()
        fixed_now = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc).astimezone()
        collector.local_now = lambda: fixed_now
        collector.find_today_tokens_from_logs = lambda codex_home, now: (12345, "test.tokens", 1)
        collector.find_latest_rate_limits = lambda codex_home: None

        def unexpected_fetch(access_token, timeout_seconds):
            raise AssertionError("reset credits should not be fetched when disabled")

        collector.fetch_reset_credits_response = unexpected_fetch

        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            (codex_home / "auth.json").write_text(
                json.dumps({"tokens": {"access_token": "secret-access-token"}}),
                encoding="utf-8",
            )

            snapshot = collector.build_snapshot(codex_home, reset_credits_mode="disabled")

        self.assertEqual(snapshot["reset_credits_status"], "disabled")
        self.assertIsNone(snapshot["reset_credits_available_count"])
        self.assertEqual(snapshot["reset_credits"], [])
        self.assertEqual(snapshot["reset_credits_tooltip"], "")

    def test_snapshot_reuses_fresh_reset_credit_cache_without_network(self):
        collector = load_collector_module()
        fixed_now = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc).astimezone()
        collector.local_now = lambda: fixed_now
        collector.find_today_tokens_from_logs = lambda codex_home, now: (12345, "test.tokens", 1)
        collector.find_latest_rate_limits = lambda codex_home: None
        calls = {"count": 0}

        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            cache_path = codex_home / "trafficmonitor" / "reset_credits_cache.json"
            (codex_home / "auth.json").write_text(
                json.dumps({"tokens": {"access_token": "secret-access-token"}}),
                encoding="utf-8",
            )

            def first_fetch(access_token, timeout_seconds):
                calls["count"] += 1
                return {
                    "available_count": 1,
                    "credits": [
                        {
                            "status": "available",
                            "title": "Full reset (Weekly + 5 hr)",
                            "granted_at": "2026-06-18T00:47:25Z",
                            "expires_at": "2026-07-18T00:47:25Z",
                        }
                    ],
                }

            collector.fetch_reset_credits_response = first_fetch
            first = collector.build_snapshot(
                codex_home,
                reset_credits_mode="enabled",
                reset_credits_cache_path=cache_path,
                reset_credits_cache_ttl_seconds=3600,
            )

            def second_fetch(access_token, timeout_seconds):
                raise AssertionError("fresh reset credit cache should avoid network")

            collector.fetch_reset_credits_response = second_fetch
            second = collector.build_snapshot(
                codex_home,
                reset_credits_mode="enabled",
                reset_credits_cache_path=cache_path,
                reset_credits_cache_ttl_seconds=3600,
            )

        self.assertEqual(calls["count"], 1)
        self.assertEqual(first["reset_credits_available_count"], 1)
        self.assertEqual(second["reset_credits_available_count"], 1)
        self.assertEqual(second["reset_credits_status"], "ok")
        self.assertIn("重置卡: 1 张可用", second["reset_credits_tooltip"])

    def test_snapshot_adds_sanitized_reset_credit_tooltip_when_auth_token_exists(self):
        collector = load_collector_module()
        fixed_now = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc).astimezone()
        collector.local_now = lambda: fixed_now
        collector.find_today_tokens_from_logs = lambda codex_home, now: (12345, "test.tokens", 1)
        collector.find_latest_rate_limits = lambda codex_home: None

        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            (codex_home / "auth.json").write_text(
                json.dumps(
                    {
                        "tokens": {"access_token": "secret-access-token"},
                        "refresh_token": "secret-refresh-token",
                    }
                ),
                encoding="utf-8",
            )

            def fake_fetch_reset_credits_response(access_token, timeout_seconds):
                self.assertEqual(access_token, "secret-access-token")
                self.assertGreater(timeout_seconds, 0)
                return {
                    "available_count": 2,
                    "credits": [
                        {
                            "id": "credit-11111111-2222-3333-4444-555555555555",
                            "status": "available",
                            "title": "Full reset (Weekly + 5 hr)",
                            "granted_at": "2026-06-18T00:47:25Z",
                            "expires_at": "2026-07-18T00:47:25Z",
                        },
                        {
                            "id": "credit-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                            "status": "available",
                            "title": "Full reset (Weekly + 5 hr)",
                            "granted_at": "2026-06-24T02:37:49Z",
                            "expires_at": "2026-07-24T02:37:49Z",
                        },
                    ],
                }

            collector.fetch_reset_credits_response = fake_fetch_reset_credits_response

            snapshot = collector.build_snapshot(codex_home)

        self.assertEqual(snapshot["reset_credits_available_count"], 2)
        self.assertEqual(
            snapshot["reset_credits"],
            [
                {
                    "status": "available",
                    "title": "Full reset (Weekly + 5 hr)",
                    "granted_at": local_text("2026-06-18T00:47:25Z"),
                    "expires_at": local_text("2026-07-18T00:47:25Z"),
                },
                {
                    "status": "available",
                    "title": "Full reset (Weekly + 5 hr)",
                    "granted_at": local_text("2026-06-24T02:37:49Z"),
                    "expires_at": local_text("2026-07-24T02:37:49Z"),
                },
            ],
        )
        self.assertIn("重置卡: 2 张可用", snapshot["reset_credits_tooltip"])
        self.assertIn("1. available | Full reset (Weekly + 5 hr)", snapshot["reset_credits_tooltip"])
        self.assertIn("获取 " + local_text("2026-06-18T00:47:25Z"), snapshot["reset_credits_tooltip"])
        self.assertIn("过期 " + local_text("2026-07-18T00:47:25Z"), snapshot["reset_credits_tooltip"])

        serialized = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn("secret-access-token", serialized)
        self.assertNotIn("secret-refresh-token", serialized)
        self.assertNotIn("11111111-2222-3333-4444-555555555555", serialized)
        self.assertNotIn("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", serialized)

    def test_snapshot_omits_reset_credit_tooltip_when_auth_is_missing(self):
        collector = load_collector_module()
        fixed_now = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc).astimezone()
        collector.local_now = lambda: fixed_now
        collector.find_today_tokens_from_logs = lambda codex_home, now: (12345, "test.tokens", 1)
        collector.find_latest_rate_limits = lambda codex_home: None

        def unexpected_fetch(access_token, timeout_seconds):
            raise AssertionError("reset credits should not be fetched without an access token")

        collector.fetch_reset_credits_response = unexpected_fetch

        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = collector.build_snapshot(Path(temp_dir))

        self.assertIsNone(snapshot["reset_credits_available_count"])
        self.assertEqual(snapshot["reset_credits"], [])
        self.assertEqual(snapshot["reset_credits_tooltip"], "")

    def test_snapshot_omits_reset_credit_tooltip_when_credentials_are_unauthorized(self):
        collector = load_collector_module()
        fixed_now = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc).astimezone()
        collector.local_now = lambda: fixed_now
        collector.find_today_tokens_from_logs = lambda codex_home, now: (12345, "test.tokens", 1)
        collector.find_latest_rate_limits = lambda codex_home: None

        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            (codex_home / "auth.json").write_text(
                json.dumps({"tokens": {"access_token": "expired-access-token"}}),
                encoding="utf-8",
            )

            def fake_fetch_reset_credits_response(access_token, timeout_seconds):
                raise collector.ResetCreditsUnauthorizedError()

            collector.fetch_reset_credits_response = fake_fetch_reset_credits_response

            snapshot = collector.build_snapshot(codex_home)

        self.assertEqual(snapshot["reset_credits_status"], "unauthorized")
        self.assertIn("凭证失效", snapshot["reset_credits_message"])
        self.assertEqual(snapshot["reset_credits_tooltip"], "")

    def test_snapshot_can_render_reset_credit_tooltip_in_english(self):
        collector = load_collector_module()
        fixed_now = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc).astimezone()
        collector.local_now = lambda: fixed_now
        collector.find_today_tokens_from_logs = lambda codex_home, now: (12345, "test.tokens", 1)
        collector.find_latest_rate_limits = lambda codex_home: None

        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            (codex_home / "auth.json").write_text(
                json.dumps({"tokens": {"access_token": "secret-access-token"}}),
                encoding="utf-8",
            )

            def fake_fetch_reset_credits_response(access_token, timeout_seconds):
                return {
                    "available_count": 1,
                    "credits": [
                        {
                            "status": "available",
                            "title": "Full reset (Weekly + 5 hr)",
                            "granted_at": "2026-06-18T00:47:25Z",
                            "expires_at": "2026-07-18T00:47:25Z",
                        }
                    ],
                }

            collector.fetch_reset_credits_response = fake_fetch_reset_credits_response

            snapshot = collector.build_snapshot(codex_home, language="en-US")

        self.assertEqual(snapshot["reset_credits_message"], "OK")
        self.assertIn("Reset credits: 1 available", snapshot["reset_credits_tooltip"])
        self.assertIn("Granted " + local_text("2026-06-18T00:47:25Z"), snapshot["reset_credits_tooltip"])
        self.assertIn("Expires " + local_text("2026-07-18T00:47:25Z"), snapshot["reset_credits_tooltip"])


class ResetCreditsTooltipWiringTest(unittest.TestCase):
    def test_plugin_appends_reset_credit_tooltip_only_when_present(self):
        source = SOURCE_PATH.read_text(encoding="utf-8")

        self.assertIn("std::wstring reset_credits_tooltip;", source)
        self.assertIn('ExtractJsonString(json, "reset_credits_tooltip", L"")', source)
        self.assertIn("if (!snapshot.reset_credits_tooltip.empty())", source)
        self.assertIn("tooltip += snapshot.reset_credits_tooltip;", source)

    def test_plugin_exposes_reset_credit_setting_and_passes_it_to_collector(self):
        source = SOURCE_PATH.read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("kResetCreditsCheckboxId", source)
        self.assertIn("reset_credits_enabled_", source)
        self.assertIn('L"reset_credits_enabled"', source)
        self.assertIn("-ResetCreditsMode", source)
        self.assertIn("reset-credit cache", readme)


if __name__ == "__main__":
    unittest.main()
