"""Guarded local-AI recovery for text PDFs with an unfamiliar result layout.

Granite is asked only after deterministic layout adapters decline the PDF.  It
receives extracted text, not an image, and must return JSON copied from that
text.  The original Source Text sheet is always retained and the result is
marked for review: an LLM is a recovery aid, not evidence that invented marks
are correct.
"""

from __future__ import annotations

from collections import OrderedDict
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from models import ParsedResult, StudentRecord, SubjectRecord
from parsers.text_fallback_parser import TextFallbackParser
from utils import merge_schema


OLLAMA_URL = os.environ.get("RESULT_ANALYZER_OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
OLLAMA_MODEL = os.environ.get("RESULT_ANALYZER_OLLAMA_MODEL", "granite4.1:8b")
MAX_SOURCE_CHARACTERS = 220_000
CODE = re.compile(r"^[A-Z0-9][A-Z0-9_/-]{1,30}$", re.IGNORECASE)


class GraniteFallbackParser(TextFallbackParser):
    """Improve a text fallback when a compatible local Ollama model is live."""

    pdf_type = "Text Fallback"

    def parse(self, pdf_path: Path) -> ParsedResult:
        fallback = super().parse(pdf_path)
        mode = os.environ.get("RESULT_ANALYZER_AI_FALLBACK", "auto").casefold()
        if mode in {"0", "false", "off", "disabled"}:
            fallback.review_notes.append("Local-AI recovery is disabled; only lossless source text was exported.")
            return fallback
        if not fallback.raw_text.strip():
            fallback.review_notes.append("The PDF has no extractable text. OCR or a vision-capable local model is required.")
            return fallback
        if len(fallback.raw_text) > MAX_SOURCE_CHARACTERS:
            fallback.review_notes.append(
                "The unfamiliar PDF is too large for one safe local-AI pass; lossless source text was exported for review."
            )
            return fallback

        payload = self._ask_granite(fallback.raw_text)
        result = self._from_payload(payload, pdf_path.name, fallback.raw_text) if payload else None
        if result is None:
            fallback.review_notes.append(
                "A local Granite structured-extraction pass was unavailable or did not return source-grounded records."
            )
            return fallback

        result.review_notes.append(
            "This unfamiliar layout was structured by the local Granite fallback. "
            "Review the preserved Source Text sheet before publishing or acting on marks."
        )
        return result

    @staticmethod
    def _prompt(source: str) -> str:
        return f"""You are extracting an academic result PDF into JSON. Work only from SOURCE.
Never invent, correct, calculate, or merge values. If a value is unclear, omit it.

Return one JSON object only, with this shape:
{{
  \"document_type\": \"short description\",
  \"records\": [
    {{
      \"seat_no\": \"exact source value or empty\",
      \"prn\": \"exact source value or empty\",
      \"name\": \"exact source value or empty\",
      \"mother_name\": \"exact source value or empty\",
      \"semesters\": [
        {{\"label\": \"Semester 1 or source label\", \"subjects\": [
          {{\"code\": \"exact course code\", \"name\": \"course title or empty\", \"fields\": {{\"column header\": \"exact cell value\"}}}}
        ]}}
      ],
      \"summary\": {{\"source label\": \"exact value\"}}
    }}
  ]
}}

Rules:
- A record must have a PRN or seat number that appears in SOURCE.
- A subject must have a course code copied from SOURCE and at least one field value.
- Keep each student's subjects separate; students may legitimately have different subjects.
- Do not use $, currency symbols, replacement characters, or prose as a marks value.
- Return {{\"records\": []}} if structure cannot be established.

SOURCE:
{source}"""

    @classmethod
    def _ask_granite(cls, source: str) -> dict[str, Any] | None:
        body = json.dumps(
            {
                "model": OLLAMA_MODEL,
                "prompt": cls._prompt(source),
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
            }
        ).encode("utf-8")
        request = Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=120) as response:
                response_body = json.loads(response.read().decode("utf-8"))
            content = response_body.get("response", "")
            decoded = json.loads(content) if isinstance(content, str) else None
            return decoded if isinstance(decoded, dict) else None
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
            return None

    @classmethod
    def _from_payload(cls, payload: dict[str, Any], source_name: str, raw_text: str) -> ParsedResult | None:
        records = payload.get("records")
        if not isinstance(records, list):
            return None

        students: list[StudentRecord] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            seat_no = cls._text(record.get("seat_no"))
            prn = cls._text(record.get("prn"))
            if not (seat_no or prn):
                continue
            # Validate every supplied identifier.  Accepting a real seat
            # number alongside a hallucinated PRN would still corrupt the
            # student identity in Excel.
            if (seat_no and not cls._contained_in_source(seat_no, raw_text)) or (
                prn and not cls._contained_in_source(prn, raw_text)
            ):
                continue
            student = StudentRecord(
                seat_no=seat_no,
                prn=prn,
                name=cls._source_value(record.get("name"), raw_text),
                mother_name=cls._source_value(record.get("mother_name"), raw_text),
            )
            for semester in record.get("semesters", []):
                cls._add_semester(student, semester, raw_text)
            summary = record.get("summary", {})
            if isinstance(summary, dict):
                for label, value in summary.items():
                    clean_label, clean_value = cls._text(label), cls._text(value)
                    if clean_label and clean_value and cls._contained_in_source(clean_value, raw_text):
                        student.summary[clean_label] = clean_value
            if any(student.semesters.values()):
                students.append(student)

        keys = [student.prn or student.seat_no for student in students]
        if not students or len(keys) != len(set(keys)):
            return None
        return ParsedResult(
            pdf_type="AI-Assisted Text Extraction",
            source_name=source_name,
            students=students,
            schema=merge_schema(OrderedDict(), students),
            raw_text=raw_text,
            requires_review=True,
        )

    @classmethod
    def _add_semester(cls, student: StudentRecord, semester: Any, raw_text: str) -> None:
        if not isinstance(semester, dict):
            return
        label = cls._text(semester.get("label")) or "Unclassified"
        subjects = semester.get("subjects")
        if not isinstance(subjects, list):
            return
        target = student.semesters.setdefault(label, OrderedDict())
        for item in subjects:
            if not isinstance(item, dict):
                continue
            code = cls._text(item.get("code")).upper()
            if not CODE.fullmatch(code) or not cls._contained_in_source(code, raw_text):
                continue
            fields = item.get("fields")
            if not isinstance(fields, dict):
                continue
            clean_fields = OrderedDict(
                (cls._text(key), cls._text(value))
                for key, value in fields.items()
                if cls._text(key) and cls._text(value) and cls._contained_in_source(cls._text(value), raw_text)
            )
            if not clean_fields:
                continue
            subject = SubjectRecord(code=code, name=cls._source_value(item.get("name"), raw_text), fields=clean_fields)
            target[subject.label] = subject

    @staticmethod
    def _text(value: Any) -> str:
        return " ".join("" if value is None else str(value).split())

    @classmethod
    def _source_value(cls, value: Any, source: str) -> str:
        text = cls._text(value)
        return text if text and cls._contained_in_source(text, source) else ""

    @staticmethod
    def _contained_in_source(value: str, source: str) -> bool:
        # Whitespace-normalised comparison still requires that the model copied
        # an actual source token rather than inventing a value.
        return " ".join(value.split()).casefold() in " ".join(source.split()).casefold()
