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
