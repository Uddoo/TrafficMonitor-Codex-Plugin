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

    def test_reset_is_tooltip_only_and_today_tokens_are_hidden(self):
        source = SOURCE_PATH.read_text(encoding="utf-8")

        self.assertIn("std::array<CodexUsageItem, 2> items_", source)
        self.assertNotIn("CodexResetTime", source)
        self.assertNotIn("CodexTodayTokens", source)
        self.assertNotIn("Codex 重置时间", source)
        self.assertNotIn("Codex 今日 Token", source)
        self.assertNotIn("今日 Token", source)
        self.assertIn("BuildTooltip(next)", source)
        self.assertIn('FormatTooltipLine(L"重置", snapshot.reset_display)', source)

    def test_tooltip_only_contains_quota_and_reset(self):
        source = SOURCE_PATH.read_text(encoding="utf-8")

        self.assertIn("BuildTooltip(next)", source)
        self.assertIn('FormatTooltipLine(L"5 小时剩余额度", snapshot.five_hour_display)', source)
        self.assertIn('FormatTooltipLine(L"周剩余额度", snapshot.weekly_display)', source)
        self.assertIn('FormatTooltipLine(L"重置", snapshot.reset_display)', source)
        self.assertNotIn('FormatTooltipLine(L"今日 Token"', source)
        self.assertNotIn("snapshot_.today_tokens_display", source)
        self.assertNotIn('FormatTooltipLine(L"状态"', source)
        self.assertNotIn('FormatTooltipLine(L"说明"', source)
        self.assertNotIn('FormatTooltipLine(L"计划"', source)
        self.assertNotIn('FormatTooltipLine(L"Token 来源"', source)
        self.assertNotIn('FormatTooltipLine(L"额度来源"', source)
        self.assertNotIn('FormatTooltipLine(L"刷新时间间隔"', source)
        self.assertNotIn('FormatTooltipLine(L"生成时间"', source)
        self.assertNotIn('L"状态文件: " + path', source)

    def test_plugin_metadata_version_matches_release(self):
        source = SOURCE_PATH.read_text(encoding="utf-8")

        self.assertIn('case TMI_VERSION: return L"0.1.2";', source)


if __name__ == "__main__":
    unittest.main()
