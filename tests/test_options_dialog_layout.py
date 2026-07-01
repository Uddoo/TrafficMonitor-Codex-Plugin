from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class OptionsDialogLayoutTest(unittest.TestCase):
    def test_options_dialog_uses_grouped_file_rows_and_copy_actions(self):
        source = (ROOT / "src" / "CodexUsagePlugin.cpp").read_text(encoding="utf-8")

        self.assertIn("刷新设置", source)
        self.assertIn("文件位置", source)
        self.assertIn("当前状态", source)
        self.assertIn("打开脚本", source)
        self.assertIn("Refresh settings", source)
        self.assertIn("File locations", source)
        self.assertIn("Current status", source)
        self.assertIn("Open script", source)
        self.assertIn("kCopyJsonButtonId", source)
        self.assertIn("kCopyLogButtonId", source)
        self.assertIn("kCopyScriptButtonId", source)
        self.assertIn("CopyTextToClipboard", source)

    def test_options_dialog_exposes_language_setting(self):
        source = (ROOT / "src" / "CodexUsagePlugin.cpp").read_text(encoding="utf-8")

        self.assertIn("kLanguageComboId", source)
        self.assertIn("LanguageModeToConfig", source)
        self.assertIn("LanguageModeFromConfig", source)
        self.assertIn("CB_ADDSTRING", source)

    def test_options_dialog_scales_and_refreshes_visible_status(self):
        source = (ROOT / "src" / "CodexUsagePlugin.cpp").read_text(encoding="utf-8")

        self.assertIn("ScaleForDpi", source)
        self.assertIn("DPI_MAIN_WND", source)
        self.assertIn("status_text", source)
        self.assertIn("SetOptionsStatusText", source)


if __name__ == "__main__":
    unittest.main()
