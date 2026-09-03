"""Layout-independent result data model."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TypeAlias


@dataclass
class SubjectRecord:
    code: str
    name: str
    fields: OrderedDict[str, str] = field(default_factory=OrderedDict)

    @property
    def label(self) -> str:
        return f"{self.code} {self.name}".strip()


@dataclass
class StudentRecord:
    seat_no: str = ""
    prn: str = ""
    name: str = ""
    mother_name: str = ""
    branch: str = ""
    semesters: OrderedDict[str, OrderedDict[str, SubjectRecord]] = field(default_factory=OrderedDict)
    summary: OrderedDict[str, str] = field(default_factory=OrderedDict)


Schema: TypeAlias = OrderedDict[str, OrderedDict[str, list[str]]]


@dataclass
class ParsedResult:
    pdf_type: str
    source_name: str
    students: list[StudentRecord]
    schema: Schema
    # Populated only by the text fallback.  The normal result sheet remains
    # compact, while unfamiliar layouts retain a lossless source-text sheet.
    raw_text: str = ""
    # A workbook may be useful without being safe to present as a verified
    # marksheet.  Unknown layouts and suspicious values are surfaced here so
    # callers can label the output rather than silently exporting bad data.
    requires_review: bool = False
    review_notes: list[str] = field(default_factory=list)
