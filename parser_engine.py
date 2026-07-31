"""High-level parsing service, independent of a particular layout."""

from __future__ import annotations

from pathlib import Path

from models import ParsedResult
from parser_factory import ParserFactory


class ResultParsingEngine:
    def parse(self, pdf_path: Path) -> ParsedResult:
        return ParserFactory.for_pdf(pdf_path).parse(pdf_path)
