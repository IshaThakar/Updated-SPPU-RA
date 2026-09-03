"""Adapter for the BE student-result table layout."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import re

from models import Schema, StudentRecord, SubjectRecord
from parsers.base_parser import BaseResultParser
from utils import clean_result_value, line_text


SUBJECT_CODE = re.compile(r"^\d{6}[A-Z]?$", re.IGNORECASE)
SEMESTER = re.compile(r"\bSEM\.?\s*:?\s*(\d+)\b", re.IGNORECASE)
SEAT = re.compile(r"\bSEAT\s+NO\.?\s*:\s*([A-Z0-9]+)", re.IGNORECASE)
NAME = re.compile(r"\bNAME\s*:\s*(.*?)\s+MOTHER\s*:", re.IGNORECASE)
MOTHER = re.compile(r"\bMOTHER\s*:\s*(.*?)\s*PRN\s*:", re.IGNORECASE)
PRN = re.compile(r"PRN\s*:\s*([A-Z0-9]+)", re.IGNORECASE)


@dataclass(frozen=True)
class TableLayout:
    fields: tuple[tuple[str, float], ...]

    @property
    def first_value_boundary(self) -> float:
        first, second = self.fields[:2]
        return first[1] - (second[1] - first[1]) / 2

    def field_for_x(self, x_center: float) -> str:
        return min(self.fields, key=lambda item: abs(item[1] - x_center))[0]


class BeStudentResultParser(BaseResultParser):
    pdf_type = "Student Result"

    def discover_schema(self, pdf_path: Path) -> Schema:
        schema: Schema = OrderedDict()
        semester: str | None = None
        layout: TableLayout | None = None
        for _, words in self.document_lines(pdf_path):
            text = line_text(words)
            layout = self._layout_from_header(words) or layout
            match = SEMESTER.search(text)
            if match:
                semester = f"Semester {match.group(1)}"
                schema.setdefault(semester, OrderedDict())
                continue
            if not semester or not layout:
                continue
            subject = self._subject(words, layout)
            if subject:
                fields = schema[semester].setdefault(subject.label, [])
                for name in subject.fields:
                    if name not in fields:
                        fields.append(name)
        return schema

    def parse_students(self, pdf_path: Path) -> list[StudentRecord]:
        students: list[StudentRecord] = []
        current: StudentRecord | None = None
        semester: str | None = None
        layout: TableLayout | None = None
        for _, words in self.document_lines(pdf_path):
            text = line_text(words)
            if SEAT.search(text) and "NAME" in text.upper() and "PRN" in text.upper():
                if current:
                    students.append(current)
                current = self._student(text)
                semester = None
                continue
            layout = self._layout_from_header(words) or layout
            match = SEMESTER.search(text)
            if current and match:
                semester = f"Semester {match.group(1)}"
                current.semesters.setdefault(semester, OrderedDict())
                continue
            if current and "FOURTH YEAR SGPA" in text.upper():
                value = re.search(r"SGPA\s*:\s*([\d.-]+)", text, re.IGNORECASE)
                if value:
                    current.summary["Fourth Year SGPA"] = value.group(1)
            if current and "CGPA" in text.upper():
                value = re.search(r"CGPA\s*:\s*([\d.-]+)", text, re.IGNORECASE)
                if value:
                    current.summary["CGPA"] = value.group(1)
            if current and semester and layout:
                subject = self._subject(words, layout)
                if subject:
                    existing = current.semesters[semester].get(subject.label)
                    if existing:
                        existing.fields.update(subject.fields)
                    else:
                        current.semesters[semester][subject.label] = subject
        if current:
            students.append(current)
        return students

    @staticmethod
    def _layout_from_header(words: list[dict]) -> TableLayout | None:
        texts = [word["text"].upper() for word in words]
        if "COURSE" not in texts or "NAME" not in texts:
            return None
        name_index = texts.index("NAME")
        if name_index == 0 or texts[name_index - 1] != "COURSE":
            return None
        fields = tuple((word["text"], float(word["x0"])) for word in words[name_index + 1 :])
        return TableLayout(fields) if len(fields) > 1 else None

    @staticmethod
    def _student(text: str) -> StudentRecord:
        def get(pattern: re.Pattern[str]) -> str:
            found = pattern.search(text)
            if not found:
                raise ValueError(f"Malformed BE student header: {text}")
            return " ".join(found.group(1).split())
        return StudentRecord(seat_no=get(SEAT), prn=get(PRN), name=get(NAME), mother_name=get(MOTHER))

    @staticmethod
    def _subject(words: list[dict], layout: TableLayout) -> SubjectRecord | None:
        index = next((i for i, word in enumerate(words) if SUBJECT_CODE.fullmatch(word["text"])), None)
        if index is None:
            return None
        name_words: list[str] = []
        fields: OrderedDict[str, str] = OrderedDict()
        for word in words[index + 1 :]:
            if word["text"] == "*":
                continue
            center = (word["x0"] + word["x1"]) / 2
            if center < layout.first_value_boundary:
                name_words.append(word["text"])
            elif word["text"] and set(word["text"]) != {"-"}:
                value = clean_result_value(word["text"])
                if value:
                    fields[layout.field_for_x(center)] = value
        name = " ".join(name_words)
        return SubjectRecord(words[index]["text"].upper(), name, fields) if name else None
