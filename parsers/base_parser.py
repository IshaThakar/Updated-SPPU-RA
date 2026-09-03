"""Base contract for layout-specific parsers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from models import ParsedResult, Schema, StudentRecord
from utils import PdfLine, merge_schema, read_pdf_lines


class BaseResultParser(ABC):
    pdf_type = "Unknown"

    def parse(self, pdf_path: Path) -> ParsedResult:
        # This order is contractual: complete schema discovery precedes students.
        self._document_lines = read_pdf_lines(pdf_path)
        try:
            discovered_schema = self.discover_schema(pdf_path)
            students = self.parse_students(pdf_path)
            self.validate(students)
            return ParsedResult(self.pdf_type, pdf_path.name, students, merge_schema(discovered_schema, students))
        finally:
            self._document_lines = None

    def document_lines(self, pdf_path: Path) -> list[PdfLine]:
        """Return the cached extraction for the current parse operation."""
        lines = getattr(self, "_document_lines", None)
        return lines if lines is not None else read_pdf_lines(pdf_path)

    @abstractmethod
    def discover_schema(self, pdf_path: Path) -> Schema:
        raise NotImplementedError

    @abstractmethod
    def parse_students(self, pdf_path: Path) -> list[StudentRecord]:
        raise NotImplementedError

    @staticmethod
    def validate(students: list[StudentRecord]) -> None:
        if not students:
            raise ValueError("No student records were detected.")
        keys = [student.prn or student.seat_no for student in students]
        if not all(keys) or len(set(keys)) != len(keys):
            raise ValueError("Student records are missing a unique PRN or seat number.")
