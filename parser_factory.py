"""Content-based selection of layout adapters."""

from __future__ import annotations

from pathlib import Path
import pdfplumber

from parsers.base_parser import BaseResultParser
from parsers.be_result_parser import BeStudentResultParser
from parsers.granite_fallback_parser import GraniteFallbackParser
from parsers.ledger_parser import CollegeLedgerParser


class ParserFactory:
    @staticmethod
    def for_pdf(pdf_path: Path) -> BaseResultParser:
        with pdfplumber.open(pdf_path) as pdf:
            # A Paper List can span several pages before the first student
            # result row.  Sample enough pages to identify that structure,
            # without doing a second full extraction of a large ledger.
            sample = "\n".join((page.extract_text() or "") for page in pdf.pages[:12]).upper()
        # This is a per-student table, used by BE as well as 2019-pattern TE
        # and SE results.  It must win over the generic PRN/credit heuristic:
        # those documents also contain Crd, Grd, and Tot columns but are not
        # College Ledger reports.
        if "SEAT NO." in sample and "COURSE NAME" in sample:
            return BeStudentResultParser()
        is_ledger = (
            "COLLEGE LEDGER" in sample
            or ("PAPER LIST" in sample and "CODE PAPER TITLE" in sample)
            # Retain a cautious structural signature for ledger variants that
            # omit the title, but only after student tables have been ruled
            # out above.
            or ("PRN" in sample and "SEAT NO." in sample and "CRD" in sample and "GRD" in sample and ("TOT" in sample or "TOTAL" in sample))
        )
        if is_ledger:
            return CollegeLedgerParser()
        # This parser first preserves every text line, then asks the local
        # Granite model for source-grounded JSON only when it is available.
        # It never replaces the raw source or presents an AI recovery as a
        # verified marksheet.
        return GraniteFallbackParser()
