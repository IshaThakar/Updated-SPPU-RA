"""Adapter for SPPU College Ledger layouts (including FE 2024/2025 patterns)."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import re

from models import Schema, StudentRecord, SubjectRecord
from parsers.base_parser import BaseResultParser
from utils import clean_result_value, line_text


SEMESTER = re.compile(r"\bSEMESTER\s*:\s*(\d+)\b", re.IGNORECASE)
PAPER_SEMESTER = re.compile(r"\bSEMESTER\s*:\s*(\d+)\b", re.IGNORECASE)
# Course identifiers vary between patterns: PCC-201-COM, OEL-221A,
# 310251_TW, and 304191AB_6 are all valid subject rows.  Keep this broad,
# but require either a separator or a leading numeric course number so common
# document words (MAX, PAGE, SEMESTER) are never treated as subjects.
CODE = re.compile(
    r"^(?:(?:[A-Z]{2,}[A-Z0-9]*)(?:[-_][A-Z0-9]+)+|\d{5,6}[A-Z]{0,3}(?:_[A-Z0-9]+)?)$",
    re.IGNORECASE,
)
PRN = re.compile(r"\bPRN\s*:\s*([A-Z0-9]+)", re.IGNORECASE)
SEAT = re.compile(r"\bSEAT\s+NO\.?\s*:\s*([A-Z0-9]+)", re.IGNORECASE)
MOTHER_LABEL = re.compile(r"\bMOTHER(?:'S|S)?(?:\s+NAME)?\s*:-?", re.IGNORECASE)


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
        for _, words in self.document_lines(pdf_path):
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
        for _, words in self.document_lines(pdf_path):
            text = line_text(words)
            if PRN.search(text) and SEAT.search(text) and "NAME" in text.upper():
                if current:
                    if header_lines:
                        self._populate_identity(current, header_lines)
                    students.append(current)
                current = StudentRecord(prn=self._value(PRN, text), seat_no=self._value(SEAT, text))
                header_lines = [text]
                semester = None
                continue
            layout = self._layout(words) or layout
            match = SEMESTER.search(text)
            if current and match:
                if header_lines:
                    self._populate_identity(current, header_lines)
                header_lines = []
                semester = f"Semester {match.group(1)}"
                current.semesters.setdefault(semester, OrderedDict())
                continue
            # A few ledgers wrap Mother's Name onto a line after the identity
            # header.  Only retain a continuation while that exact field is
            # unfinished.  Earlier versions accepted every non-layout line;
            # after a page transition that allowed an AEC-101 result row to be
            # appended to Mother's Name.
            if current and self._needs_identity_continuation(header_lines) and self._is_identity_continuation(words, text):
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
                self._populate_identity(current, header_lines)
            students.append(current)
        return students

    @staticmethod
    def _value(pattern: re.Pattern[str], text: str) -> str:
        found = pattern.search(text)
        if not found:
            raise ValueError(f"Malformed ledger student header: {text}")
        return " ".join(found.group(1).split())

    @staticmethod
    def _needs_identity_continuation(lines: list[str]) -> bool:
        """Whether a wrapped Mother Name still needs its next text line."""
        if not lines:
            return False
        text = " ".join(lines)
        marker = MOTHER_LABEL.search(text)
        return marker is None or not text[marker.end() :].strip()

    @staticmethod
    def _is_identity_continuation(words: list[dict], text: str) -> bool:
        """Accept a name fragment but never headers, footers, or course rows."""
        if not text.strip() or len(words) == 0 or len(words) > 12:
            return False
        if CODE.fullmatch(words[0]["text"]):
            return False
        upper = text.upper()
        excluded = ("SEMESTER", "PRN:", "SEAT NO", "COLLEGE LEDGER", "PUNCODE", "BRANCH :", "PAGE ")
        return not any(marker in upper for marker in excluded)

    @staticmethod
    def _populate_identity(student: StudentRecord, lines: list[str]) -> None:
        text = " ".join(lines)
        # A semester header is a hard identity boundary, even on malformed
        # sources where text extraction combines visual lines.
        text = re.split(r"\bSEMESTER\s*:", text, maxsplit=1, flags=re.IGNORECASE)[0]
        name = re.search(
            r"\bNAME\s*:\s*(.*?)\s+MOTHER(?:'S|S)?(?:\s+NAME)?\s*:-?\s*(.*?)$",
            text,
            re.IGNORECASE,
        )
        if name:
            student.name = " ".join(name.group(1).split())
            student.mother_name = " ".join(name.group(2).split())
            return
        # Fallback preserves a usable record, but refuses to mistake a subject
        # row for a mother name.
        name = re.search(r"\bNAME\s*:\s*(.*?)\s*$", text, re.IGNORECASE)
        student.name = " ".join(name.group(1).split()) if name else ""
        student.mother_name = ""

    def _paper_catalog(self, pdf_path: Path) -> dict[str, str]:
        """Read the whole Paper List, not just its first page.

        Course catalogues in the supplied FE, SE, and TE ledgers span several
        pages.  Stopping at page one silently produced blank course names for
        most result rows, even when the marks themselves were parsed.
        """
        catalog: dict[str, str] = {}
        in_paper_list = False
        for _, words in self.document_lines(pdf_path):
            text = line_text(words)
            upper = text.upper()
            if "CODE" in upper and "PAPER" in upper and "TITLE" in upper:
                in_paper_list = True
                continue
            if not in_paper_list:
                continue
            # The first student header unambiguously marks the end of the
            # catalogue.  This prevents result rows from replacing titles.
            if PRN.search(text) and SEAT.search(text):
                break
            if SEMESTER.search(text):
                continue
            if not words or not CODE.fullmatch(words[0]["text"]):
                continue
            code = words[0]["text"].upper()
            title_words = [word["text"] for word in words[1:]]
            # 2019 paper lists often repeat the course code in a separate
            # title column (for example, "210241 210241 DISCRETE...").
            if title_words and title_words[0].upper() == code:
                title_words.pop(0)
            title = " ".join(title_words).strip()
            if title:
                catalog.setdefault(code, title)
        return catalog

    @staticmethod
    def _layout(words: list[dict]) -> LedgerLayout | None:
        texts = [word["text"].strip() for word in words]
        normalized = [text.upper() for text in texts]
        total_index = next((index for index, label in enumerate(normalized) if label in {"TOT", "TOTAL"}), None)
        if len(texts) < 6 or total_index is None or "CRD" not in normalized or "GRD" not in normalized:
            return None
        credit_index = next((index for index in range(total_index + 1, len(texts)) if normalized[index] == "CRD"), None)
        if credit_index is None or len(texts) < credit_index + 5:
            return None

        display_names = {
            "TOT": "Tot",
            "TOTAL": "Tot",
            "CRD": "Crd",
            "ERN": "Ern",
            "GRD": "Grd",
        }
        counts: dict[str, int] = {}
        fields: list[tuple[str, float]] = []
        for index, word in enumerate(words):
            label = display_names.get(normalized[index], normalized[index])
            # In current NEP ledgers the printed header repeats ISE/ESE/TW/PR
            # for different assessment slots.  Give every physical x-position
            # a unique field name; otherwise values from two columns collide
            # into one Excel cell and look like invalid marks.
            if index < total_index:
                counts[label] = counts.get(label, 0) + 1
                if counts[label] > 1:
                    label = f"{label} ({counts[label]})"
            fields.append((label, (float(word["x0"]) + float(word["x1"])) / 2))
        # The ledger abbreviates the final columns as Crd Ern Grd Grd Crd.
        # They consistently mean credits, earned credits, grade, grade point,
        # and credit point; use their distinct semantic labels in Excel.
        semantic = ("Crd", "Ern", "Grd", "GP", "CP")
        fields[total_index] = ("Tot", fields[total_index][1])
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
            raw_value = word["text"]
            # Standalone * and P are source status flags, not marks.
            if raw_value in {"*", "P"}:
                continue
            value = CollegeLedgerParser._clean_cell_value(raw_value)
            # Cleaning a standalone asterisk yields an empty string.  Never
            # let that placeholder occupy a marks column, because the next
            # numeric token would otherwise become `` | 027``.
            if not value:
                continue
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
    def _clean_cell_value(value: str) -> str:
        """Remove source footnote symbols from otherwise numeric marks.

        Some ledgers print values such as ``028$`` or ``028#`` and describe
        the symbol separately in an observation line.  The numerical mark is
        the cell value; exporting the trailing symbol makes Excel appear to
        contain currency or invalid marks.  Only a *trailing* footnote on a
        purely numeric value is removed, leaving genuine grade/status tokens
        untouched.
        """
        return clean_result_value(value)

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
        semester_number: int | None = None
        if sgpa_match:
            semester_number = ordinal_to_number[sgpa_match.group(1).casefold()]
            value = sgpa_match.group(2)
            # Failed or withheld results appear as a run of dashes in the PDF.
            # Store one canonical placeholder, never unrelated credit digits.
            student.summary[f"Semester {semester_number} SGPA"] = "----" if set(value) == {"-"} else value

        # Ledger footers also include the document's compact semester/overall
        # SGPA notation, e.g. "SGPA: (1) 8.50, (2) 8.20".  Preserve it rather
        # than dropping the source's year-level summary.
        overall_sgpa = re.search(r"\bSGPA\s*:\s*(\([^\n]+)$", text, re.IGNORECASE)
        if overall_sgpa:
            student.summary["SGPA"] = " ".join(overall_sgpa.group(1).split())

        credits_match = re.search(r"Credits Earned/Total\s*:\s*([0-9/]+)", text, re.IGNORECASE)
        if credits_match:
            label = f"Semester {semester_number} Credits Earned/Total" if semester_number else "Credits Earned/Total"
            student.summary[label] = credits_match.group(1)

        total_credit_match = re.search(r"Total Credits Earned\s*:\s*([0-9/]+)", text, re.IGNORECASE)
        if total_credit_match:
            student.summary["Total Credits Earned"] = total_credit_match.group(1)

        total_points_match = re.search(r"Total Credit Points\s*:\s*([0-9.]+)", text, re.IGNORECASE)
        if total_points_match:
            label = f"Semester {semester_number} Total Credit Points" if semester_number else "Total Credit Points"
            student.summary[label] = total_points_match.group(1)

        year_result_match = re.search(
            r"\b((?:First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth)\s+Year\s+Result)\s*:\s*(.*?)(?=\s+Total\s+Credits\s+Earned\s*:|$)",
            text,
            re.IGNORECASE,
        )
        if year_result_match:
            label = " ".join(word.capitalize() for word in year_result_match.group(1).split())
            student.summary[label] = " ".join(year_result_match.group(2).split())

