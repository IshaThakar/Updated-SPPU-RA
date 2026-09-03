"""Evidence-oriented safety checks shared by every PDF parser.

The aim is deliberately conservative: a result workbook must never look
verified when the parser could not establish the structure or when a cell
contains a value that cannot be a result token.  Different colleges use
different subjects and assessment columns, so this validates evidence and
cell hygiene rather than enforcing one fixed marks schema.
"""

from __future__ import annotations

import re

from models import ParsedResult


# Currency symbols and replacement characters are common signs that a PDF
# glyph was assigned to the wrong column.  They are never valid SPPU marks,
# grades, credits, or status values.
INVALID_MARK_CHARACTERS = re.compile(r"[$\u00a3\u20ac\u20b9\ufffd]")


def assess_result(result: ParsedResult) -> ParsedResult:
    """Attach review information without discarding any extracted evidence."""
    notes: list[str] = []

    if result.pdf_type == "Text Fallback":
        notes.append(
            "The PDF layout was not recognised as a structured result table. "
            "Use the Source Text sheet to verify it before treating it as marks data."
        )

    subject_rows = 0
    for student in result.students:
        if not (student.prn or student.seat_no):
            notes.append("A student record has no PRN or seat number.")
        for subjects in student.semesters.values():
            for subject in subjects.values():
                subject_rows += 1
                for field, value in subject.fields.items():
                    text = str(value).strip()
                    if " | " in text:
                        notes.append(
                            f"{student.prn or student.seat_no}: {subject.code} {field} contains merged values ({text!r})."
                        )
                    if INVALID_MARK_CHARACTERS.search(text):
                        notes.append(
                            f"{student.prn or student.seat_no}: {subject.code} {field} contains an invalid marks character ({text!r})."
                        )

    if result.pdf_type != "Text Fallback" and not result.schema:
        notes.append("No subject schema was discovered for this structured result PDF.")
    if result.pdf_type != "Text Fallback" and not subject_rows:
        notes.append("Student records were found, but no subject rows were extracted.")

    # Preserve parser-provided notes (for example, a local-AI fallback) while
    # making output deterministic and compact.
    result.review_notes = list(dict.fromkeys([*result.review_notes, *notes]))
    result.requires_review = bool(result.review_notes)
    return result


def review_summary(result: ParsedResult) -> str:
    """A short console/UI summary that does not hide a failed quality gate."""
    if not result.requires_review:
        return "Extraction validation: VERIFIED"
    return "Extraction validation: REVIEW REQUIRED\n" + "\n".join(
        f"  - {note}" for note in result.review_notes
    )
