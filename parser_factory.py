"""Content-based selection of layout adapters."""

from __future__ import annotations

from pathlib import Path
import pdfplumber

from parsers.base_parser import BaseResultParser
from parsers.be_result_parser import BeStudentResultParser
from parsers.ledger_parser import CollegeLedgerParser


class ParserFactory:
    @staticmethod
    def for_pdf(pdf_path: Path) -> BaseResultParser:
        with pdfplumber.open(pdf_path) as pdf:
            sample = "\n".join((page.extract_text() or "") for page in pdf.pages).upper()
        if "COLLEGE LEDGER" in sample:
            return CollegeLedgerParser()
        if "SEAT NO." in sample and "COURSE NAME" in sample:
            return BeStudentResultParser()
        raise ValueError(f"Unsupported SPPU PDF layout: {pdf_path.name}")
