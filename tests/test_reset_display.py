import importlib.util
import sys
from datetime import datetime
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


class ResetDisplayTest(unittest.TestCase):
    def test_stale_rate_limit_still_shows_last_recorded_reset_times(self):
        collector = load_collector_module()
        primary_reset = datetime(2026, 6, 23, 19, 5).astimezone()
        weekly_reset = datetime(2026, 6, 25, 9, 12).astimezone()
        now = datetime(2026, 6, 30, 19, 0).astimezone()

        result = collector.reset_display_for_limits(primary_reset, weekly_reset, stale=True, now=now)

        self.assertEqual(result, "旧: 5h 06-23 19:05 / 周 06-25 09:12")

    def test_reset_and_today_tokens_are_tooltip_only_not_taskbar_items(self):
        source = SOURCE_PATH.read_text(encoding="utf-8")

        self.assertIn("std::array<CodexUsageItem, 2> items_", source)
        self.assertNotIn("CodexResetTime", source)
        self.assertNotIn("CodexTodayTokens", source)
        self.assertNotIn("Codex 重置时间", source)
        self.assertNotIn("Codex 今日 Token", source)
        self.assertIn('FormatTooltipLine(L"重置", next.reset_display)', source)
        self.assertIn('FormatTooltipLine(L"今日 Token", next.today_tokens_display)', source)


if __name__ == "__main__":
    unittest.main()
