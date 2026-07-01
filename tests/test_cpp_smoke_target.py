from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CppSmokeTargetTest(unittest.TestCase):
    def test_cmake_registers_json_parser_smoke_test(self):
        cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        smoke = ROOT / "tests" / "cpp" / "json_parser_smoke.cpp"

        self.assertTrue(smoke.exists())
        self.assertIn("enable_testing()", cmake)
        self.assertIn("add_executable(CodexUsageJsonParserSmoke", cmake)
        self.assertIn("tests/cpp/json_parser_smoke.cpp", cmake)
        self.assertIn("add_test(NAME CodexUsageJsonParserSmoke", cmake)


if __name__ == "__main__":
    unittest.main()
