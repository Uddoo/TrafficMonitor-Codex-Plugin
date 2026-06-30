from datetime import datetime
import importlib.util
import json
import sys
import tempfile
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


class RemainingQuotaDisplayTest(unittest.TestCase):
    def test_snapshot_displays_remaining_percent_not_used_percent(self):
        collector = load_collector_module()
        fixed_now = datetime.fromtimestamp(1782818400).astimezone()
        collector.local_now = lambda: fixed_now
        collector.find_today_tokens_from_logs = lambda codex_home, now: (12345, "test.tokens", 1)

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
                    "rate_limits": {
                        "primary": {
                            "used_percent": 25,
                            "window_minutes": 300,
                            "resets_at": 1782818694,
                        },
                        "secondary": {
                            "remaining_percent": 66,
                            "window_minutes": 10080,
                            "resets_at": 1783389180,
                        },
                    },
                },
            }
            session_file.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

            snapshot = collector.build_snapshot(codex_home)

            self.assertEqual(snapshot["five_hour_display"], "75%")
            self.assertEqual(snapshot["five_hour_used_percent"], 25)
            self.assertEqual(snapshot["five_hour_remaining_percent"], 75)
            self.assertEqual(snapshot["weekly_display"], "66%")
            self.assertEqual(snapshot["weekly_used_percent"], 34)
            self.assertEqual(snapshot["weekly_remaining_percent"], 66)


class RemainingQuotaCustomDrawTest(unittest.TestCase):
    def test_quota_items_use_custom_drawn_remaining_bar(self):
        source = (ROOT / "src" / "CodexUsagePlugin.cpp").read_text(encoding="utf-8")

        self.assertIn("bool CodexUsageItem::IsCustomDraw() const", source)
        self.assertIn("void CodexUsageItem::DrawItem(void* hDC", source)
        self.assertIn("DrawQuotaBar", source)
        self.assertIn("RemainingQuotaColor", source)
        self.assertIn("\"five_hour_remaining_percent\"", source)
        self.assertIn("\"weekly_remaining_percent\"", source)

    def test_quota_bar_height_is_stable_across_trafficmonitor_rows(self):
        source = (ROOT / "src" / "CodexUsagePlugin.cpp").read_text(encoding="utf-8")

        self.assertIn("constexpr int kQuotaBarHeight = 4;", source)
        self.assertNotIn("h >= 16 ? 6 : 4", source)

    def test_quota_percent_text_has_small_gap_and_right_aligned_100_slot(self):
        source = (ROOT / "src" / "CodexUsagePlugin.cpp").read_text(encoding="utf-8")

        self.assertIn("constexpr int kQuotaValueGap = 3;", source)
        self.assertIn("int QuotaPercentSlotWidth(HDC dc)", source)
        self.assertIn('MeasureTextWidth(dc, L"100%")', source)
        self.assertIn("RECT value_rect{ bar_right + kQuotaValueGap", source)
        self.assertIn("DT_RIGHT | DT_VCENTER", source)
        self.assertNotIn('L"100% 旧"', source)
        self.assertNotIn("value_left = content_right - value_width", source)
        self.assertNotIn("constexpr int kQuotaValueGap = 0;", source)

    def test_quota_bars_share_a_fixed_label_slot_so_left_edges_align(self):
        source = (ROOT / "src" / "CodexUsagePlugin.cpp").read_text(encoding="utf-8")

        self.assertIn("int QuotaLabelSlotWidth(HDC dc)", source)
        self.assertIn("const int label_slot_width = QuotaLabelSlotWidth(dc);", source)
        self.assertIn("const int bar_left = content_left + label_slot_width + gap;", source)
        self.assertNotIn("const int bar_left = content_left + label_width + gap;", source)


if __name__ == "__main__":
    unittest.main()
