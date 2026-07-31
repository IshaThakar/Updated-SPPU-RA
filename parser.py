"""Layout-aware parser for SPPU result PDFs.

The parser reads the column labels and their x-coordinates from each PDF page.
It deliberately does not contain a list of semesters, subject codes, or mark fields.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Iterable

import pdfplumber


SUBJECT_CODE = re.compile(r"^\d{6}[A-Z]?$", re.IGNORECASE)
SEMESTER = re.compile(r"\bSEM\.?\s*:?\s*(\d+)\b", re.IGNORECASE)
SEAT = re.compile(r"\bSEAT\s+NO\.?\s*:\s*([A-Z0-9]+)", re.IGNORECASE)
NAME = re.compile(r"\bNAME\s*:\s*(.*?)\s+MOTHER\s*:", re.IGNORECASE)
MOTHER = re.compile(r"\bMOTHER\s*:\s*(.*?)\s*PRN\s*:", re.IGNORECASE)
# A few source records omit the space before PRN (for example, "...NAMEPRN").
PRN = re.compile(r"PRN\s*:\s*([A-Z0-9]+)", re.IGNORECASE)


@dataclass
class Subject:
    code: str
    name: str
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.code} {self.name}".strip()


@dataclass
class Student:
    seat_no: str
    prn: str
    name: str
    mother_name: str
    semesters: dict[str, OrderedDict[str, Subject]] = field(default_factory=dict)


@dataclass(frozen=True)
class TableLayout:
    """A table header discovered from a PDF, with field anchor positions."""

    fields: tuple[tuple[str, float], ...]

    @property
    def first_field_x(self) -> float:
        return self.fields[0][1]

    @property
    def first_value_boundary(self) -> float:
        """Midpoint before the first marks column, derived from header spacing."""
        if len(self.fields) == 1:
            return self.first_field_x
        return self.first_field_x - (self.fields[1][1] - self.first_field_x) / 2

    def field_for_x(self, x_center: float) -> str:
        return min(self.fields, key=lambda item: abs(item[1] - x_center))[0]


Schema = OrderedDict[str, OrderedDict[str, list[str]]]


class ResultParser:
    """Two-pass parser: scan the complete PDF first, then parse students."""

    def scan_schema(self, pdf_path: str | Path) -> Schema:
        """Discover every semester, subject, and populated field in the PDF.

        This pass never creates student records.  It is intentionally performed
        before ``parse_students`` so the output columns do not depend on the
        order in which individual student records are encountered.
        """
        schema: Schema = OrderedDict()
        semester: str | None = None
        layout: TableLayout | None = None

        for _, words in self._lines(pdf_path):
            text = self._line_text(words)
            detected_layout = self._layout_from_header(words)
            if detected_layout:
                layout = detected_layout
                continue
            semester_match = SEMESTER.search(text)
            if semester_match:
                semester = f"Semester {semester_match.group(1)}"
                schema.setdefault(semester, OrderedDict())
                continue
            if not semester or not layout:
                continue
            parsed = self._parse_subject(words, layout)
            if not parsed:
                continue
            subject, _ = parsed
            semester_schema = schema.setdefault(semester, OrderedDict())
            fields = semester_schema.setdefault(subject.label, [])
            for field_name in subject.fields:
                if field_name not in fields:
                    fields.append(field_name)

        if not schema:
            raise ValueError("No semester/subject table was found in the PDF.")
        return schema

    def parse_students(self, pdf_path: str | Path) -> list[Student]:
        """Parse student records after the complete-document schema scan."""
        students: list[Student] = []
        current: Student | None = None
        semester: str | None = None
        layout: TableLayout | None = None

        for _, words in self._lines(pdf_path):
            text = self._line_text(words)
            if self._is_student_header(text):
                if current is not None:
                    students.append(current)
                current = self._parse_student_header(text)
                semester = None
                continue
            detected_layout = self._layout_from_header(words)
            if detected_layout:
                layout = detected_layout
                continue
            semester_match = SEMESTER.search(text)
            if semester_match and current is not None:
                semester = f"Semester {semester_match.group(1)}"
                current.semesters.setdefault(semester, OrderedDict())
                continue
            if current is None or semester is None or layout is None:
                continue
            parsed = self._parse_subject(words, layout)
            if parsed:
                subject, _ = parsed
                current.semesters[semester][subject.label] = subject

        if current is not None:
            students.append(current)
        if not students:
            raise ValueError("No student records were found in the PDF.")
        return students

    def _lines(self, pdf_path: str | Path) -> Iterable[tuple[int, list[dict]]]:
        """Yield reading-order word lines, grouped by their rendered y position."""
        with pdfplumber.open(pdf_path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                buckets: list[tuple[float, list[dict]]] = []
                for word in page.extract_words(x_tolerance=1, y_tolerance=2):
                    for top, bucket in buckets:
                        if abs(word["top"] - top) <= 2:
                            bucket.append(word)
                            break
                    else:
                        buckets.append((word["top"], [word]))
                for _, bucket in sorted(buckets, key=lambda item: item[0]):
                    yield page_number, sorted(bucket, key=lambda word: word["x0"])

    @staticmethod
    def _line_text(words: list[dict]) -> str:
        return " ".join(word["text"] for word in words)

    @staticmethod
    def _is_student_header(text: str) -> bool:
        return bool(SEAT.search(text) and "NAME" in text.upper() and "PRN" in text.upper())

    @staticmethod
    def _parse_student_header(text: str) -> Student:
        def required(pattern: re.Pattern[str], label: str) -> str:
            match = pattern.search(text)
            if not match:
                raise ValueError(f"Could not read {label} from student header: {text}")
            return " ".join(match.group(1).split())

        return Student(
            seat_no=required(SEAT, "seat number"),
            prn=required(PRN, "PRN"),
            name=required(NAME, "student name"),
            mother_name=required(MOTHER, "mother name"),
        )

    @staticmethod
    def _layout_from_header(words: list[dict]) -> TableLayout | None:
        """Read field labels directly from a COURSE NAME table heading."""
        texts = [word["text"].upper() for word in words]
        try:
            name_index = texts.index("NAME")
        except ValueError:
            return None
        if name_index == 0 or texts[name_index - 1] != "COURSE":
            return None

        fields = tuple((word["text"], float(word["x0"])) for word in words[name_index + 1 :])
        return TableLayout(fields) if fields else None

    def _parse_subject(self, words: list[dict], layout: TableLayout) -> tuple[Subject, int] | None:
        """Parse one course row using the discovered column anchor positions."""
        code_index = next(
            (index for index, word in enumerate(words) if SUBJECT_CODE.fullmatch(word["text"])),
            None,
        )
        if code_index is None:
            return None

        code = words[code_index]["text"].upper()
        name_words: list[str] = []
        value_words: list[dict] = []
        for word in words[code_index + 1 :]:
            # The result PDF marks some Semester 8 courses with a standalone
            # asterisk between the course title and the marks grid.
            if word["text"] == "*":
                continue
            center = (word["x0"] + word["x1"]) / 2
            if center < layout.first_value_boundary:
                name_words.append(word["text"])
            else:
                value_words.append(word)
        name = " ".join(name_words).strip()
        if not name:
            return None

        fields: dict[str, str] = {}
        for word in value_words:
            value = word["text"].strip()
            if not value or set(value) == {"-"}:
                continue
            center = (word["x0"] + word["x1"]) / 2
            fields[layout.field_for_x(center)] = value
        return Subject(code=code, name=name, fields=fields), code_index
