from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RefreshIntervalFeatureTest(unittest.TestCase):
    def test_refresh_interval_setting_is_wired_through_plugin_options(self):
        source = (ROOT / "src" / "CodexUsagePlugin.cpp").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("codex_usage_plugin.ini", source)
        self.assertIn("refresh_interval_seconds", source)
        self.assertIn("refresh_interval_seconds_", source)
        self.assertIn('L"language"', source)
        self.assertIn("language_mode_", source)
        self.assertIn("RunOptionsDialog", source)
        self.assertIn("RefreshIntervalMilliseconds", source)
        self.assertIn("刷新时间间隔", readme)

    def test_collector_launch_is_guarded_by_inflight_process_handle(self):
        source = (ROOT / "src" / "CodexUsagePlugin.cpp").read_text(encoding="utf-8")

        self.assertIn("collector_process_", source)
        self.assertIn("CloseCollectorProcessIfFinished", source)
        self.assertIn("IsCollectorRunning", source)
        self.assertIn("WaitForSingleObject(collector_process_, 0)", source)
        self.assertIn("if (IsCollectorRunning())", source)
        self.assertIn("CRITICAL_SECTION collector_lock_", source)
        self.assertIn("InitializeCriticalSection(&collector_lock_)", source)
        self.assertIn("EnterCriticalSection", source)
        self.assertIn("LeaveCriticalSection", source)


if __name__ == "__main__":
    unittest.main()
