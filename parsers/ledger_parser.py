"""Adapter for SPPU College Ledger layouts (including FE 2024/2025 patterns)."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import re

from models import Schema, StudentRecord, SubjectRecord
from parsers.base_parser import BaseResultParser
from utils import iter_pdf_lines, line_text


SEMESTER = re.compile(r"\bSEMESTER\s*:\s*(\d+)\b", re.IGNORECASE)
PAPER_SEMESTER = re.compile(r"\bSEMESTER\s*:\s*(\d+)\b", re.IGNORECASE)
CODE = re.compile(r"^(?:[A-Z]{2,}[A-Z0-9_-]*-[A-Z0-9_-]+|\d{6}[A-Z]?(?:_[A-Z]+)?)$", re.IGNORECASE)
PRN = re.compile(r"\bPRN\s*:\s*([A-Z0-9]+)", re.IGNORECASE)
SEAT = re.compile(r"\bSEAT\s+NO\.?\s*:\s*([A-Z0-9]+)", re.IGNORECASE)
NAME = re.compile(r"\bNAME\s*:\s*(.*?)\s+MOTHER'?S(?:\s+NAME)?(?:\s*:-?)?(?:\s|$)", re.IGNORECASE)


@dataclass(frozen=True)
class LedgerLayout:
    fields: tuple[tuple[str, float], ...]

    def field_for_x(self, center: float) -> str:
        return min(self.fields, key=lambda item: abs(item[1] - center))[0]


class CollegeLedgerParser(BaseResultParser):
    pdf_type = "College Ledger"

    def discover_schema(self, pdf_path: Path) -> Schema:
        catalog = self._paper_catalog(pdf_path)
        schema: Schema = OrderedDict()
        semester: str | None = None
        layout: LedgerLayout | None = None
        for _, words in iter_pdf_lines(pdf_path):
            text = line_text(words)
            layout = self._layout(words) or layout
            match = SEMESTER.search(text)
            # Ignore the paper-list page's semester catalogue: it has no
            # ledger table and therefore no student-result schema yet.
            if match and layout:
                semester = f"Semester {match.group(1)}"
                schema.setdefault(semester, OrderedDict())
                continue
            if semester and layout:
                subject = self._subject(words, layout, catalog)
                if subject:
                    fields = schema[semester].setdefault(subject.label, [])
                    for field in subject.fields:
                        if field not in fields:
                            fields.append(field)
        return schema

    def parse_students(self, pdf_path: Path) -> list[StudentRecord]:
        catalog = self._paper_catalog(pdf_path)
        students: list[StudentRecord] = []
        current: StudentRecord | None = None
        semester: str | None = None
        layout: LedgerLayout | None = None
        header_lines: list[str] = []
        for _, words in iter_pdf_lines(pdf_path):
            text = line_text(words)
            if PRN.search(text) and SEAT.search(text) and "NAME" in text.upper():
                if current:
                    if header_lines:
                        self._populate_identity(current, " ".join(header_lines))
                    students.append(current)
                current = StudentRecord(prn=self._value(PRN, text), seat_no=self._value(SEAT, text))
                header_lines = [text]
                semester = None
                continue
            layout = self._layout(words) or layout
            match = SEMESTER.search(text)
            if current and match:
                self._populate_identity(current, " ".join(header_lines))
                header_lines = []
                semester = f"Semester {match.group(1)}"
                current.semesters.setdefault(semester, OrderedDict())
                continue
            # The ledger's name/mother header can wrap over two or more lines.
            # Until SEMESTER starts, retain only non-table continuation text.
            if current and header_lines and not self._layout(words) and not (words and CODE.fullmatch(words[0]["text"])):
                header_lines.append(text)
                continue
            if current:
                self._summary(current, text)
            if current and semester and layout:
                subject = self._subject(words, layout, catalog)
                if subject:
                    existing = current.semesters[semester].get(subject.label)
                    if existing:
                        existing.fields.update(subject.fields)
                    else:
                        current.semesters[semester][subject.label] = subject
        if current:
            if header_lines:
                self._populate_identity(current, " ".join(header_lines))
            students.append(current)
        return students

    @staticmethod
    def _value(pattern: re.Pattern[str], text: str) -> str:
        found = pattern.search(text)
        if not found:
            raise ValueError(f"Malformed ledger student header: {text}")
        return " ".join(found.group(1).split())

    @staticmethod
    def _populate_identity(student: StudentRecord, text: str) -> None:
        name = re.search(r"\bNAME\s*:\s*(.*?)\s+MOTHER'?S\s+NAME\s*:-?\s*(.*?)(?=\s+SEMESTER\s*:|$)", text, re.IGNORECASE)
        if name:
            student.name = " ".join(name.group(1).split())
            student.mother_name = " ".join(name.group(2).split())
            return
        # Fallback preserves a usable record, but refuses to mistake a subject
        # row for a mother name.
        name = re.search(r"\bNAME\s*:\s*(.*?)\s*$", text, re.IGNORECASE)
        student.name = " ".join(name.group(1).split()) if name else ""
        student.mother_name = ""

    @staticmethod
    def _paper_catalog(pdf_path: Path) -> dict[str, str]:
        catalog: dict[str, str] = {}
        in_paper_list = False
        for page_number, words in iter_pdf_lines(pdf_path):
            if page_number > 1:
                break
            text = line_text(words)
            if text.upper().startswith("CODE PAPER TITLE"):
                in_paper_list = True
                continue
            if in_paper_list and SEMESTER.search(text):
                continue
            if in_paper_list and words and CODE.fullmatch(words[0]["text"]):
                catalog[words[0]["text"].upper()] = " ".join(word["text"] for word in words[1:])
        return catalog

    @staticmethod
    def _layout(words: list[dict]) -> LedgerLayout | None:
        texts = [word["text"] for word in words]
        if len(texts) < 6 or "Tot" not in texts or "Crd" not in texts or "Grd" not in texts:
            return None
        total_index = texts.index("Tot")
        counts: dict[str, int] = {}
        fields: list[tuple[str, float]] = []
        for index, word in enumerate(words):
            label = word["text"]
            # Repeated pre-total labels are alternative assessment slots. A
            # subject uses at most the relevant slot, so merge them into one
            # semantic field such as TW, PR, OR, ISE, or ESE.
            if index >= total_index:
                counts[label] = counts.get(label, 0) + 1
                if counts[label] > 1:
                    label = f"{label} ({counts[word['text']]})"
            fields.append((label, (float(word["x0"]) + float(word["x1"])) / 2))
        # The ledger abbreviates the final columns as Crd Ern Grd Grd Crd.
        # They consistently mean credits, earned credits, grade, grade point,
        # and credit point; use their distinct semantic labels in Excel.
        credit_index = next(i for i in range(total_index + 1, len(texts)) if texts[i] == "Crd")
        semantic = ("Crd", "Ern", "Grd", "GP", "CP")
        if len(fields) >= credit_index + len(semantic):
            for offset, label in enumerate(semantic):
                fields[credit_index + offset] = (label, fields[credit_index + offset][1])
        # The printed ledger has an unheaded status slot between Total and Crd.
        total_x = fields[total_index][1]
        credit_x = fields[credit_index][1]
        fields.insert(credit_index, ("Result Status", (total_x + credit_x) / 2))
        return LedgerLayout(tuple(fields))

    @staticmethod
    def _subject(words: list[dict], layout: LedgerLayout, catalog: dict[str, str]) -> SubjectRecord | None:
        if not words or not CODE.fullmatch(words[0]["text"]):
            return None
        code = words[0]["text"].upper()
        fields: OrderedDict[str, str] = OrderedDict()
        value_count = 0
        for word in words[1:]:
            value = word["text"]
            # Standalone * and P are source status flags, not marks.
            if value in {"*", "P"}:
                continue
            value = value.lstrip("*").replace('"', "")
            center = (word["x0"] + word["x1"]) / 2
            field = layout.field_for_x(center)
            value_count += 1
            previous = fields.get(field)
            blank = bool(value) and set(value) == {"-"}
            previous_blank = bool(previous) and set(previous) == {"-"}
            if previous is None or (previous_blank and not blank):
                fields[field] = value
            elif not blank and value not in previous.split(" | "):
                fields[field] = f"{previous} | {value}"
        # Reject document-header tokens that look like an alphanumeric code.
        if value_count < 3:
            return None
        # AC is an audit-course result; ! is defined by the source legend as
        # a non-countable credit course. They are statuses, not marks/points.
        if fields.get("CP") == "!" or fields.get("Tot") == "AC":
            status = fields.get("Grd") or fields.get("Tot")
            fields = OrderedDict((("Status", status), ("Remark", "Non-countable credit course")))
        return SubjectRecord(code, catalog.get(code, ""), fields)

    @staticmethod
    def _summary(student: StudentRecord, text: str) -> None:
        ordinal_to_number = {
            "first": 1, "second": 2, "third": 3, "fourth": 4,
            "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
        }
        sgpa_match = re.search(
            r"\b(First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth)\s+Semester\s+SGPA\s*:\s*(\d+(?:\.\d+)?|-{3,})",
            text,
            re.IGNORECASE,
        )
        if sgpa_match:
            semester = ordinal_to_number[sgpa_match.group(1).casefold()]
            value = sgpa_match.group(2)
            # Failed or withheld results appear as a run of dashes in the PDF.
            # Store one canonical placeholder, never unrelated credit digits.
            student.summary[f"Semester {semester} SGPA"] = "----" if set(value) == {"-"} else value

        credits_match = re.search(r"Credits Earned/Total\s*:\s*([0-9/]+)", text, re.IGNORECASE)
        if credits_match:
            student.summary["Credits Earned/Total"] = credits_match.group(1)

        total_credit_match = re.search(r"Total Credits Earned\s*:\s*([0-9/]+)", text, re.IGNORECASE)
        if total_credit_match:
            student.summary["Total Credits Earned"] = total_credit_match.group(1)

