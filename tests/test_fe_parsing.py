import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parser_engine import ResultParsingEngine


class FeParsingTests(unittest.TestCase):
    def test_fe_pdfs_capture_overall_sgpa_summary(self) -> None:
        root = Path(__file__).resolve().parents[1] / "pdfs"
        engine = ResultParsingEngine()

        for pdf_name in ["FE 2024 pattern.pdf", "FE 2025.pdf"]:
            result = engine.parse(root / pdf_name)
            self.assertGreater(len(result.students), 0, pdf_name)
            student = result.students[0]
            self.assertTrue(
                student.summary.get("Overall SGPA") or student.summary.get("SGPA"),
                f"{pdf_name} should contain an overall SGPA summary",
            )


if __name__ == "__main__":
    unittest.main()
