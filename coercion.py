"""Typed coercion for SPPU result values.

Every mark cell resolves to a (value, status) pair. This is the single
abstraction the analytics layer depends on: an absent student is not a
zero, an unoffered subject is not a failure, and an unrecognised cell is
never silently dropped.

This module is pure. No pandas, no I/O, no knowledge of PDF layouts.
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
    text = text.replace("_", " ").replace("-", " ").replace(".", " ")
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


ABSENT_TOKENS = {"AB", "ABS", "ABSENT", "A"}
FAIL_TOKENS = {"F", "FF", "FAIL", "FAILED", "ATKT", "KT", "BACKLOG"}
WITHHELD_TOKENS = {"W", "WH", "WITHHELD", "RLD", "RESULT WITHHELD"}
NOT_OFFERED_TOKENS = {"NA", "N/A", "N.A.", "NULL", "NONE", "NIL"}

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
    """Coerce one cell to a Mark.

    A dash run is reported as NOT_OFFERED. Whether it really means
    "absent" can only be decided across the cohort, which
    ``resolve_dash_columns`` does once all rows are known.
    """
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
    """Decide what a dash means for one column across the whole cohort.

    If nobody in the cohort has a value, the column does not apply
    (NOT_OFFERED). If some students scored and others show a dash, the
    dash is an individual absence.
    """
    marks = list(marks)
    if any(mark.status in ATTEMPTED for mark in marks):
        return MarkStatus.ABSENT
    return MarkStatus.NOT_OFFERED


# ---------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------

#: canonical component -> aliases seen in SPPU layouts.
COMPONENT_ALIASES: dict[str, tuple[str, ...]] = {
    "internal": ("ise", "cie", "cce", "ia", "th in", "in", "internal", "insem"),
    "external": ("ese", "ue", "ese th", "end sem", "external", "endsem"),
    "theory": ("th", "theory"),
    "termwork": ("tw", "term work", "termwork"),
    "practical": ("pr", "or", "pr or", "practical", "oral"),
    "total": ("tot", "total", "total marks", "grand total", "obtained"),
    "grade": ("gr", "grd", "grade"),
    "credit": ("cr", "crd", "credit", "credits"),
    "grade_point": ("gp", "grade point"),
    "credit_point": ("cp", "credit point", "c g", "cxg"),
    "sgpa": ("sgpa",),
    "cgpa": ("cgpa",),
    "result": ("result", "status", "remark", "remarks"),
}

_EXACT = {
    alias: canonical
    for canonical, aliases in COMPONENT_ALIASES.items()
    for alias in aliases
}

UNMAPPED = "unmapped"


def canonical_component(field_name: Any) -> str:
    """Map a parser field name to a canonical component.

    Unrecognised names return ``unmapped`` rather than being guessed at,
    so a new layout degrades visibly on the data-quality panel.
    """
    key = normalize_key(field_name)
    if not key:
        return UNMAPPED
    if key in _EXACT:
        return _EXACT[key]

    # Word-level match: "ese marks" -> external.
    words = set(key.split())
    for canonical, aliases in COMPONENT_ALIASES.items():
        for alias in aliases:
            if " " not in alias and alias in words:
                return canonical
    return UNMAPPED


# ---------------------------------------------------------------------
# Semesters
# ---------------------------------------------------------------------

_ROMAN = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4,
    "v": 5, "vi": 6, "vii": 7, "viii": 8,
}


def parse_semester(label: Any) -> int | None:
    """'SEM III', 'Semester 3', 'S.E. Sem-I' -> integer, else None."""
    key = normalize_key(label)
    if not key:
        return None

    arabic = re.search(r"\b(\d{1,2})\b", key)
    if arabic:
        number = int(arabic.group(1))
        if 1 <= number <= 12:
            return number

    for token in reversed(key.split()):
        if token in _ROMAN:
            return _ROMAN[token]
    return None


# ---------------------------------------------------------------------
# Grades
# ---------------------------------------------------------------------

#: Display order, best to worst. Used for chart ordering.
GRADE_ORDER = ("O", "A+", "A", "B+", "B", "C+", "C", "D", "P", "F", "FF", "AB")

FAILING_GRADES = {"F", "FF", "DROP"}


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
