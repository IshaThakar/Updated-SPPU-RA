"""Shared PDF and schema utilities."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Iterable, TypeAlias
import re

import pdfplumber

from models import ParsedResult, Schema, StudentRecord


FOOTNOTE_SUFFIX = re.compile(r"(?<=\d)[#$&]+(?=(?:/|$))")


PdfLine: TypeAlias = tuple[int, list[dict]]


def read_pdf_lines(pdf_path: Path) -> list[PdfLine]:
    """Extract a PDF once into visual reading-order lines.

    Every result parser needs a schema pass and a student pass.  Keeping one
    in-memory representation avoids re-opening large college ledgers (some of
    which contain hundreds of pages) and keeps both passes aligned to exactly
    the same extracted words.
    """
    lines: list[PdfLine] = []
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
                lines.append((page_number, sorted(bucket, key=lambda word: word["x0"])))
    return lines


def iter_pdf_lines(pdf_path: Path) -> Iterable[PdfLine]:
    """Yield words grouped into visual lines, preserving their x coordinates."""
    yield from read_pdf_lines(pdf_path)


def line_text(words: list[dict]) -> str:
    return " ".join(word["text"] for word in words)


def has_data(value: str) -> bool:
    """Return True when a PDF cell has a value other than dashes/whitespace."""
    return bool(value and any(part.strip() and set(part.strip()) != {"-"} for part in value.split(" | ")))


def clean_result_value(value: str) -> str:
    """Remove SPPU footnote suffixes from an otherwise numeric result token.

    Examples: ``10$/025`` becomes ``10/025`` and ``2$`` becomes ``2``.
    The source uses these suffixes as observation references, not as part of a
    mark or ordinal.  Restricting removal to symbols immediately after a digit
    avoids changing normal grade/status values.
    """
    return FOOTNOTE_SUFFIX.sub("", value.lstrip("*").replace('"', ""))


def merge_schema(discovered: Schema, students: list[StudentRecord]) -> Schema:
    """Preserve the document-wide scan, then add any parsed values defensively."""
    schema: Schema = OrderedDict(
        (semester, OrderedDict((subject, list(fields)) for subject, fields in subjects.items()))
        for semester, subjects in discovered.items()
    )
    for student in students:
        for semester, subjects in student.semesters.items():
            target = schema.setdefault(semester, OrderedDict())
            for label, subject in subjects.items():
                fields = target.setdefault(label, [])
                for name in subject.fields:
                    if name not in fields:
                        fields.append(name)
    return schema


def render_structure(result: ParsedResult) -> str:
    """Human-readable validation output shown before an Excel file is created."""
    lines = [
        f"\nPDF: {result.source_name}",
        f"PDF Type: {result.pdf_type}",
        f"Student Count: {len(result.students)}",
        "Semesters:",
    ]
    lines.extend(f"  {semester}" for semester in result.schema)
    lines.append("Subjects:")
    for semester, subjects in result.schema.items():
        lines.append(f"\n{semester}")
        for label, fields in subjects.items():
            code, _, name = label.partition(" ")
            lines.extend([f"  {code}", f"  {name}", "  Fields:"])
            lines.extend(f"    {field}" for field in fields)
    return "\n".join(lines)
