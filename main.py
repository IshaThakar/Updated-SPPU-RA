"""Process every PDF in pdfs/ with automatic layout detection."""

from pathlib import Path

from excel_writer import ExcelWriter
from parser_engine import ResultParsingEngine
from utils import render_structure


ROOT = Path(__file__).resolve().parent
PDF_DIRECTORY = ROOT / "pdfs"
OUTPUT_DIRECTORY = ROOT / "output"


def main() -> None:
    pdfs = sorted(PDF_DIRECTORY.glob("*.pdf"))
    if not pdfs:
        raise RuntimeError(f"No PDFs found in {PDF_DIRECTORY}")
    engine = ResultParsingEngine()
    writer = ExcelWriter()
    for pdf_path in pdfs:
        result = engine.parse(pdf_path)
        # Validation is intentionally printed before the Excel write.
        print(render_structure(result))
        output = OUTPUT_DIRECTORY / f"{pdf_path.stem}.xlsx"
        dataframe = writer.write(result, output)
        print(f"Created: {output.name} ({dataframe.shape[0]} students x {dataframe.shape[1]} columns)\n")


if __name__ == "__main__":
    main()
