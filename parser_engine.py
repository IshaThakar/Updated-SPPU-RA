"""High-level parsing service, independent of a particular layout."""

from __future__ import annotations

from pathlib import Path

from models import ParsedResult
from parser_factory import ParserFactory
from parsers.granite_fallback_parser import GraniteFallbackParser
from parsers.text_fallback_parser import TextFallbackParser
from result_validator import assess_result


class ResultParsingEngine:
    def parse(self, pdf_path: Path) -> ParsedResult:
        parser = ParserFactory.for_pdf(pdf_path)
        try:
            result = parser.parse(pdf_path)
        except Exception as error:
            # A document can advertise "College Ledger" yet change enough
            # that its deterministic adapter no longer applies.  Preserve the
            # document through the lossless text route instead of stopping a
            # batch or emitting a partial table from a half-completed parser.
            # Do not give AI a recognised layout to reinterpret: known formats
            # must be fixed deterministically, not replaced by a guess.
            if isinstance(parser, GraniteFallbackParser):
                raise
            result = TextFallbackParser().parse(pdf_path)
            result.review_notes.append(
                f"The {parser.pdf_type} parser could not establish this layout ({type(error).__name__}); source text was preserved for review."
            )

        # A parser that yields students but no courses is equally unsafe to
        # present as a normal result workbook.  Preserve source text for a
        # deterministic fix rather than allowing an AI model to reinterpret a
        # format we already claim to recognise.
        has_subjects = any(subjects for student in result.students for subjects in student.semesters.values())
        if not isinstance(parser, GraniteFallbackParser) and not has_subjects:
            recovery = TextFallbackParser().parse(pdf_path)
            recovery.review_notes.append(
                f"The {parser.pdf_type} parser found no subject rows; source text was preserved for review."
            )
            result = recovery
        return assess_result(result)
