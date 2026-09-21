"""Typed coercion for SPPU result values.

Every mark cell resolves to a (value, status) pair. This is the single
abstraction the analytics layer depends on: an absent student is not a
zero, an unoffered subject is not a failure, and an unrecognised cell is
never silently dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable


# ---------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------

def clean_text(value: Any) -> str:
    """Normalise any parser value to a stripped string."""
    if value is None:
        return ""
    return str(value).strip()


def normalize_key(value: Any) -> str:
    """Lowercase, collapse whitespace, drop punctuation used as separators."""
    text = clean_text(value).lower()
    text = text.replace("_", " ").replace("-", " ").replace(".", " ").replace("/", " ").replace("%", " ")
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------
# Mark status
# ---------------------------------------------------------------------

class MarkStatus(str, Enum):
    SCORED = "SCORED"            # a real number
    FAIL = "FAIL"                # explicit failure token
    ABSENT = "ABSENT"            # student did not appear
    WITHHELD = "WITHHELD"        # result withheld
    NOT_OFFERED = "NOT_OFFERED"  # column does not apply to this student
    UNPARSED = "UNPARSED"        # unrecognised; surfaced, never dropped


#: Statuses that mean the student sat the assessment. Pass/fail rates use
#: this as the denominator, never the raw row count.
ATTEMPTED = frozenset({MarkStatus.SCORED, MarkStatus.FAIL})

#: Statuses that mean the value is a usable number.
NUMERIC = frozenset({MarkStatus.SCORED})


ABSENT_TOKENS = {"AB", "ABS", "ABSENT", "AB.", "A.B."}
FAIL_TOKENS = {"F", "FF", "FAIL", "FAILED", "ATKT", "KT", "BACKLOG", "DROP", "FAIL/ATKT", "FX", "F-FF"}
WITHHELD_TOKENS = {"W", "WH", "WITHHELD", "RLD", "RESULT WITHHELD", "RL", "R.L."}
NOT_OFFERED_TOKENS = {"NA", "N/A", "N.A.", "NULL", "NONE", "NIL", "NOT APPLICABLE", "---", "--", "-", "----"}

_DASH_RUN = re.compile(r"^[-\u2010-\u2015]+$")
_NUMBER = re.compile(r"^[-+]?\d+(?:\.\d+)?$")


@dataclass(frozen=True)
class Mark:
    """A single assessment cell."""

    value: float | None
    status: MarkStatus
    raw: str

    @property
    def attempted(self) -> bool:
        return self.status in ATTEMPTED

    @property
    def is_numeric(self) -> bool:
        return self.value is not None and self.status in NUMERIC


def parse_mark(raw: Any) -> Mark:
    """Coerce one cell to a Mark."""
    text = clean_text(raw)
    if not text:
        return Mark(None, MarkStatus.NOT_OFFERED, text)

    if _DASH_RUN.match(text):
        return Mark(None, MarkStatus.NOT_OFFERED, text)

    upper = text.upper().replace(",", "")

    if _NUMBER.match(upper):
        return Mark(float(upper), MarkStatus.SCORED, text)

    if upper in ABSENT_TOKENS:
        return Mark(None, MarkStatus.ABSENT, text)
    if upper in FAIL_TOKENS:
        return Mark(None, MarkStatus.FAIL, text)
    if upper in WITHHELD_TOKENS:
        return Mark(None, MarkStatus.WITHHELD, text)
    if upper in NOT_OFFERED_TOKENS:
        return Mark(None, MarkStatus.NOT_OFFERED, text)

    # Forms such as "45 F", "F(45)", "45*".
    numbers = re.findall(r"\d+(?:\.\d+)?", upper)
    letters = set(re.findall(r"[A-Z]+", upper))

    if numbers and letters & FAIL_TOKENS:
        return Mark(float(numbers[0]), MarkStatus.FAIL, text)
    if numbers and letters & ABSENT_TOKENS:
        return Mark(None, MarkStatus.ABSENT, text)
    if len(numbers) == 1 and not letters:
        return Mark(float(numbers[0]), MarkStatus.SCORED, text)

    return Mark(None, MarkStatus.UNPARSED, text)


def resolve_dash_columns(marks: Iterable[Mark]) -> MarkStatus:
    """Decide what a dash means for one column across the cohort."""
    marks = list(marks)
    if any(mark.status in ATTEMPTED for mark in marks):
        return MarkStatus.ABSENT
    return MarkStatus.NOT_OFFERED


# ---------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------

COMPONENT_ALIASES: dict[str, tuple[str, ...]] = {
    "internal": ("ise", "cie", "cce", "ia", "th in", "in", "internal", "insem", "in sem", "in_sem", "in-sem", "ise marks", "ca", "cia"),
    "external": ("ese", "ue", "ese th", "end sem", "external", "endsem", "end sem", "end_sem", "end-sem", "ese marks", "th ese", "university exam", "univ exam"),
    "theory": ("th", "theory", "th paper", "th marks"),
    "termwork": ("tw", "term work", "termwork", "tw marks", "tw/or", "tw/pr", "tw-or", "tw-pr"),
    "practical": ("pr", "or", "pr or", "practical", "oral", "pr/or", "oral/practical", "pr marks", "or marks", "pr-or"),
    "total": ("tot", "total", "total marks", "grand total", "obtained", "obt", "obt marks", "obt tot", "marks", "tot marks", "max tot", "tot%"),
    "grade": ("gr", "grd", "grade", "final grade", "letter grade"),
    "credit": ("cr", "crd", "credit", "credits", "earn cr", "earned cr", "ern cr", "crd earned"),
    "grade_point": ("gp", "grade point", "grd point", "gr point"),
    "credit_point": ("cp", "credit point", "c g", "cxg", "c*g", "pts", "points", "crd point", "total points"),
    "sgpa": ("sgpa", "sem sgpa", "semester sgpa"),
    "cgpa": ("cgpa",),
    "result": ("result", "status", "remark", "remarks", "sts", "result status", "p/f"),
}

_EXACT = {
    alias: canonical
    for canonical, aliases in COMPONENT_ALIASES.items()
    for alias in aliases
}

UNMAPPED = "unmapped"


def canonical_component(field_name: Any) -> str:
    """Map a parser field name to a canonical component."""
    key = normalize_key(field_name)
    if not key:
        return UNMAPPED
    if key in _EXACT:
        return _EXACT[key]

    clean_k = re.sub(r"[^\w\s]", " ", key).strip()
    clean_k = re.sub(r"\s+", " ", clean_k)
    if clean_k in _EXACT:
        return _EXACT[clean_k]

    words = set(clean_k.split())
    for canonical, aliases in COMPONENT_ALIASES.items():
        for alias in aliases:
            if alias in clean_k:
                return canonical
            if " " not in alias and alias in words:
                return canonical
    return UNMAPPED

# ---------------------------------------------------------------------
# Semesters
# ---------------------------------------------------------------------

ORDINALS = {
    "first": 1, "1st": 1, "i": 1,
    "second": 2, "2nd": 2, "ii": 2,
    "third": 3, "3rd": 3, "iii": 3,
    "fourth": 4, "4th": 4, "iv": 4,
    "fifth": 5, "5th": 5, "v": 5,
    "sixth": 6, "6th": 6, "vi": 6,
    "seventh": 7, "7th": 7, "vii": 7,
    "eighth": 8, "8th": 8, "viii": 8,
}

YEAR_MAP = {
    "first year": 1, "fe": 1,
    "second year": 3, "se": 3,
    "third year": 5, "te": 5,
    "fourth year": 7, "be": 7,
}


def parse_semester(label: Any) -> int | None:
    """'SEM III', 'Semester 3', 'S.E. Sem-I', 'SGPA1', 'First Semester' -> integer, else None."""
    key = normalize_key(label)
    if not key:
        return None

    m = re.search(r"(?:sem(?:ester)?|sgpa)\s*[:\-_\s]?\s*(\d{1,2})\b", key)
    if m:
        val = int(m.group(1))
        if 1 <= val <= 12:
            return val

    m_rom = re.search(r"(?:sem(?:ester)?|sgpa)\s*[:\-_\s]?\s*([ivx]+)\b", key)
    if m_rom and m_rom.group(1) in ORDINALS:
        return ORDINALS[m_rom.group(1)]

    for y_name, sem_num in YEAR_MAP.items():
        if y_name in key:
            return sem_num

    arabic = re.search(r"\b(\d{1,2})\b", key)
    if arabic:
        number = int(arabic.group(1))
        if 1 <= number <= 12:
            return number

    for token in key.split():
        if token in ORDINALS:
            return ORDINALS[token]

    return None

# ---------------------------------------------------------------------
# Grades
# ---------------------------------------------------------------------

GRADE_ORDER = ("O", "A+", "A", "B+", "B", "C+", "C", "D", "P", "F", "FF", "AB")
FAILING_GRADES = {"F", "FF", "DROP", "FAIL", "FAILED", "FX", "AB", "ABS", "ABSENT"}


def normalize_grade(value: Any) -> str:
    text = clean_text(value).upper().replace(" ", "")
    if not text or _DASH_RUN.match(text):
        return ""
    return text


def is_failing_grade(value: Any) -> bool:
    return normalize_grade(value) in FAILING_GRADES


def grade_sort_index(grade: str) -> int:
    grade = normalize_grade(grade)
    return GRADE_ORDER.index(grade) if grade in GRADE_ORDER else len(GRADE_ORDER)
