"""Lossless text-first fallback for unfamiliar SPPU result layouts."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import re

from models import ParsedResult, Schema, StudentRecord, SubjectRecord
from parsers.base_parser import BaseResultParser
from utils import line_text


SEMESTER = re.compile(r"\bSEM(?:ESTER|\.)?\s*:?\s*(\d+)\b", re.IGNORECASE)
PRN = re.compile(r"\bPRN\s*:\s*([A-Z0-9]+)", re.IGNORECASE)
SEAT = re.compile(r"\bSEAT\s+NO\.?\s*:\s*([A-Z0-9]+)", re.IGNORECASE)
NAME = re.compile(r"\bNAME\s*:\s*(.*?)(?=\s+MOTHER(?:'S)?(?:\s+NAME)?\s*:|\s+PRN\s*:|$)", re.IGNORECASE)
MOTHER = re.compile(r"\bMOTHER(?:'S)?(?:\s+NAME)?\s*:-?\s*(.*?)(?=\s+PRN\s*:|$)", re.IGNORECASE)
CODE = re.compile(r"^(?:\d{5,6}[A-Z]?|[A-Z]{2,}[A-Z0-9_-]*-[A-Z0-9_-]+)$", re.IGNORECASE)


class TextFallbackParser(BaseResultParser):
    """Create a usable workbook even when a source layout is unknown.

    This deliberately avoids guessing column names.  It keeps each detected
    course line as an ``Extracted Values`` field and exports every extracted
    PDF line separately, so no information disappears while a new structured
    adapter can be added later.
    """

    pdf_type = "Text Fallback"

    def parse(self, pdf_path: Path) -> ParsedResult:
        result = super().parse(pdf_path)
        result.raw_text = self._raw_text
        return result

    def discover_schema(self, pdf_path: Path) -> Schema:
        schema: Schema = OrderedDict()
        semester = "Unclassified"
        for _, words in self.document_lines(pdf_path):
            text = line_text(words)
            match = SEMESTER.search(text)
            if match:
                semester = f"Semester {match.group(1)}"
                schema.setdefault(semester, OrderedDict())
                continue
            subject = self._subject(words)
            if subject:
                schema.setdefault(semester, OrderedDict()).setdefault(subject.label, ["Extracted Values"])
        return schema

    def parse_students(self, pdf_path: Path) -> list[StudentRecord]:
        students: list[StudentRecord] = []
        current: StudentRecord | None = None
        semester = "Unclassified"
        raw_lines: list[str] = []

        for _, words in self.document_lines(pdf_path):
            text = line_text(words)
            if text:
                raw_lines.append(text)
            if PRN.search(text) and SEAT.search(text):
                if current:
                    students.append(current)
                current = StudentRecord(
                    prn=self._value(PRN, text),
                    seat_no=self._value(SEAT, text),
                    name=self._value(NAME, text),
                    mother_name=self._value(MOTHER, text),
                )
                semester = "Unclassified"
                continue
            match = SEMESTER.search(text)
            if match:
                semester = f"Semester {match.group(1)}"
                if current:
                    current.semesters.setdefault(semester, OrderedDict())
                continue
            subject = self._subject(words)
            if current and subject:
                current.semesters.setdefault(semester, OrderedDict())[subject.label] = subject

        if current:
            students.append(current)
        if not students:
            # A source without reliable identifiers is still delivered as a
            # searchable workbook rather than rejected with no usable output.
            students = [StudentRecord(seat_no="Record 1", name="Unstructured source")]
        self._raw_text = "\n".join(raw_lines)
        return students

    @staticmethod
    def _value(pattern: re.Pattern[str], text: str) -> str:
        match = pattern.search(text)
        return " ".join(match.group(1).split()) if match else ""

    @staticmethod
    def _subject(words: list[dict]) -> SubjectRecord | None:
        if not words or not CODE.fullmatch(words[0]["text"]):
            return None
        code = words[0]["text"].upper()
        values = " ".join(word["text"] for word in words[1:]).strip()
        if not values:
            return None
        return SubjectRecord(code=code, name="", fields=OrderedDict({"Extracted Values": values}))
