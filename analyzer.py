"""Analytics layer for the SPPU Result Analyzer.

Reads ``ParsedResult`` directly. It never reads the generated workbook
back, because the Excel shape is a presentation artifact whose columns
change per file.

Everything here is layout-independent: subject fields are discovered at
runtime and mapped through ``coercion.canonical_component``.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from coercion import (
    MarkStatus,
    canonical_component,
    clean_text,
    grade_sort_index,
    is_failing_grade,
    normalize_grade,
    normalize_key,
    parse_mark,
    parse_semester,
    resolve_dash_columns,
    UNMAPPED,
)
from models import ParsedResult


def student_key(student: Any) -> str:
    """PRN is durable; seat numbers recycle across exam sessions."""
    prn = clean_text(student.prn)
    if prn:
        return prn
    seat = clean_text(student.seat_no)
    return f"SEAT:{seat}" if seat else f"NAME:{clean_text(student.name)}"


class ResultAnalyzer:
    """Turns one ParsedResult into analytical frames and metrics."""

    def __init__(self, result: ParsedResult):
        self.result = result
        self._marks: pd.DataFrame | None = None
        self._subjects: pd.DataFrame | None = None

    # -----------------------------------------------------------------
    # Level 1 — long frame, one row per student x semester x subject x field
    # -----------------------------------------------------------------

    def marks_frame(self) -> pd.DataFrame:
        if self._marks is not None:
            return self._marks.copy()

        rows: list[dict[str, Any]] = []

        for student in self.result.students:
            base = {
                "student_key": student_key(student),
                "seat_no": clean_text(student.seat_no),
                "prn": clean_text(student.prn),
                "student_name": clean_text(student.name),
                "mother_name": clean_text(student.mother_name),
                "branch": clean_text(student.branch),
            }

            for semester, subjects in student.semesters.items():
                sem_row = {
                    **base,
                    "semester_label": clean_text(semester),
                    "semester_no": parse_semester(semester),
                }

                for label, subject in subjects.items():
                    subject_row = {
                        **sem_row,
                        "subject_code": clean_text(subject.code) or clean_text(label),
                        "subject_name": clean_text(subject.name),
                    }

                    for field_name, value in subject.fields.items():
                        mark = parse_mark(value)
                        rows.append(
                            {
                                **subject_row,
                                "field": clean_text(field_name),
                                "component": canonical_component(field_name),
                                "value": mark.value,
                                "status": mark.status.value,
                                "raw": mark.raw,
                            }
                        )

        frame = pd.DataFrame(
            rows,
            columns=[
                "student_key", "seat_no", "prn", "student_name", "mother_name",
                "branch", "semester_label", "semester_no", "subject_code",
                "subject_name", "field", "component", "value", "status", "raw",
            ],
        )

        if not frame.empty:
            frame = self._resolve_dashes(frame)
            frame = self._fill_subject_names(frame)

        self._marks = frame
        return frame.copy()

    @staticmethod
    def _resolve_dashes(frame: pd.DataFrame) -> pd.DataFrame:
        """A dash means 'absent' only if someone else in the column scored."""
        groups = ["semester_label", "subject_code", "field"]
        for _, index in frame.groupby(groups, dropna=False).groups.items():
            block = frame.loc[index]
            dashes = block["status"] == MarkStatus.NOT_OFFERED.value
            if not dashes.any():
                continue
            resolved = resolve_dash_columns(
                parse_mark(raw) for raw in block["raw"]
            )
            frame.loc[index[dashes.to_numpy()], "status"] = resolved.value
        return frame

    @staticmethod
    def _fill_subject_names(frame: pd.DataFrame) -> pd.DataFrame:
        """Marksheets often omit subject names that ledgers carry."""
        named = (
            frame[frame["subject_name"] != ""]
            .drop_duplicates("subject_code")
            .set_index("subject_code")["subject_name"]
        )
        blank = frame["subject_name"] == ""
        frame.loc[blank, "subject_name"] = (
            frame.loc[blank, "subject_code"].map(named).fillna("")
        )
        return frame

    # -----------------------------------------------------------------
    # Level 2 — one row per student x semester x subject
    # -----------------------------------------------------------------

    def subject_frame(self) -> pd.DataFrame:
        """Wide-per-subject view with total / grade / credit resolved.

        ``total_detected`` is False when no field maps to a total. The
        analyzer never guesses a total from an arbitrary numeric column;
        an undetected total is reported, not invented.
        """
        if self._subjects is not None:
            return self._subjects.copy()

        marks = self.marks_frame()
        if marks.empty:
            self._subjects = pd.DataFrame()
            return self._subjects.copy()

        keys = [
            "student_key", "seat_no", "prn", "student_name", "branch",
            "semester_label", "semester_no", "subject_code", "subject_name",
        ]

        rows = []
        for key_values, group in marks.groupby(keys, dropna=False):
            record = dict(zip(keys, key_values))
            by_component = {
                component: block
                for component, block in group.groupby("component")
            }

            total = by_component.get("total")
            if total is None:
                record["total_detected"] = False
                record["total"] = None
                record["total_status"] = ""
            else:
                cell = total.iloc[0]
                record["total_detected"] = True
                record["total"] = cell["value"]
                record["total_status"] = cell["status"]

            grade_block = by_component.get("grade")
            record["grade"] = (
                normalize_grade(grade_block.iloc[0]["raw"]) if grade_block is not None else ""
            )

            credit_block = by_component.get("credit")
            record["credit"] = (
                credit_block.iloc[0]["value"] if credit_block is not None else None
            )

            for component in ("internal", "external", "theory", "termwork", "practical"):
                block = by_component.get(component)
                record[component] = block.iloc[0]["value"] if block is not None else None

            record["failed"] = self._is_failed(record, group)
            record["attempted"] = self._is_attempted(record, group)
            rows.append(record)

        self._subjects = pd.DataFrame(rows)
        return self._subjects.copy()

    @staticmethod
    def _is_failed(record: dict[str, Any], group: pd.DataFrame) -> bool:
        if record["grade"]:
            return is_failing_grade(record["grade"])
        if record["total_status"] == MarkStatus.FAIL.value:
            return True
        result_block = group[group["component"] == "result"]
        if not result_block.empty:
            return is_failing_grade(result_block.iloc[0]["raw"]) or \
                normalize_key(result_block.iloc[0]["raw"]).startswith("fail")
        return False

    @staticmethod
    def _is_attempted(record: dict[str, Any], group: pd.DataFrame) -> bool:
        """Absent and not-offered rows are excluded from pass-rate denominators."""
        if record["grade"] in {"AB"}:
            return False
        statuses = set(group["status"])
        if statuses & {MarkStatus.SCORED.value, MarkStatus.FAIL.value}:
            return True
        return bool(record["grade"]) and record["grade"] != "AB"

    # -----------------------------------------------------------------
    # Semester summary (SGPA matched to its own semester)
    # -----------------------------------------------------------------

    def semester_frame(self) -> pd.DataFrame:
        """One row per student x semester, with the correct SGPA."""
        rows = []
        for student in self.result.students:
            key = student_key(student)
            summary = dict(getattr(student, "summary", {}) or {})
            sgpa_keys = {
                name: value for name, value in summary.items()
                if "sgpa" in normalize_key(name)
            }
            semesters = list(student.semesters.items())

            for position, (semester, subjects) in enumerate(semesters):
                sem_no = parse_semester(semester)
                rows.append(
                    {
                        "student_key": key,
                        "seat_no": clean_text(student.seat_no),
                        "prn": clean_text(student.prn),
                        "student_name": clean_text(student.name),
                        "branch": clean_text(student.branch),
                        "semester_label": clean_text(semester),
                        "semester_no": sem_no,
                        "sgpa": self._match_sgpa(sgpa_keys, sem_no, position, len(semesters)),
                        "subjects": len(subjects),
                    }
                )

        frame = pd.DataFrame(rows)
        if frame.empty:
            return frame

        subjects = self.subject_frame()
        if not subjects.empty:
            agg = (
                subjects.groupby(["student_key", "semester_label"])
                .agg(backlogs=("failed", "sum"))
                .reset_index()
            )
            frame = frame.merge(agg, on=["student_key", "semester_label"], how="left")
        frame["backlogs"] = frame.get("backlogs", 0).fillna(0).astype(int)
        frame["result_status"] = frame["backlogs"].map(
            lambda count: "Failed / Backlog" if count else "Passed"
        )
        return frame

    @staticmethod
    def _match_sgpa(
        sgpa_keys: dict[str, Any], sem_no: int | None, position: int, total: int
    ) -> float | None:
        """Pick the SGPA belonging to this semester, not merely the first one.

        Summary keys look like 'SGPA SEM III'. Only when a single SGPA
        key exists for a single semester is a positional match safe.
        """
        if not sgpa_keys:
            return None

        if sem_no is not None:
            for name, value in sgpa_keys.items():
                if parse_semester(name) == sem_no:
                    return parse_mark(value).value

        if len(sgpa_keys) == total:
            ordered = list(sgpa_keys.values())
            return parse_mark(ordered[position]).value

        if len(sgpa_keys) == 1 and total == 1:
            return parse_mark(next(iter(sgpa_keys.values()))).value

        return None

    # -----------------------------------------------------------------
    # Metrics
    # -----------------------------------------------------------------

    def summary_metrics(self) -> dict[str, Any]:
        semesters = self.semester_frame()
        empty = {
            "total_students": 0, "passed_students": 0, "failed_students": 0,
            "pass_percentage": 0.0, "average_sgpa": None, "highest_sgpa": None,
            "lowest_sgpa": None, "total_backlogs": 0, "students_with_backlogs": 0,
        }
        if semesters.empty:
            return empty

        per_student = semesters.groupby("student_key").agg(
            backlogs=("backlogs", "sum"),
            sgpa=("sgpa", "mean"),
        )
        total_students = len(per_student)
        failed = int((per_student["backlogs"] > 0).sum())
        sgpa = per_student["sgpa"].dropna()

        return {
            "total_students": total_students,
            "passed_students": total_students - failed,
            "failed_students": failed,
            "pass_percentage": (total_students - failed) / total_students * 100,
            "average_sgpa": float(sgpa.mean()) if not sgpa.empty else None,
            "highest_sgpa": float(sgpa.max()) if not sgpa.empty else None,
            "lowest_sgpa": float(sgpa.min()) if not sgpa.empty else None,
            "total_backlogs": int(per_student["backlogs"].sum()),
            "students_with_backlogs": failed,
        }

    def subject_metrics(self) -> pd.DataFrame:
        """Grouped by (semester, subject code) — names can be blank or shared."""
        subjects = self.subject_frame()
        columns = [
            "semester_label", "subject_code", "subject", "appeared", "absent",
            "passed", "pass_pct", "fail_pct", "average", "highest", "lowest",
            "total_detected",
        ]
        if subjects.empty:
            return pd.DataFrame(columns=columns)

        rows = []
        for (semester, code), group in subjects.groupby(
            ["semester_label", "subject_code"], dropna=False
        ):
            attempted = group[group["attempted"]]
            marks = pd.to_numeric(attempted["total"], errors="coerce").dropna()
            appeared = len(attempted)
            failed = int(attempted["failed"].sum())
            name = next((n for n in group["subject_name"] if n), "")

            rows.append(
                {
                    "semester_label": semester,
                    "subject_code": code,
                    "subject": f"{code} {name}".strip(),
                    "appeared": appeared,
                    "absent": int((~group["attempted"]).sum()),
                    "passed": appeared - failed,
                    "pass_pct": (appeared - failed) / appeared * 100 if appeared else None,
                    "fail_pct": failed / appeared * 100 if appeared else None,
                    "average": float(marks.mean()) if not marks.empty else None,
                    "highest": float(marks.max()) if not marks.empty else None,
                    "lowest": float(marks.min()) if not marks.empty else None,
                    "total_detected": bool(group["total_detected"].any()),
                }
            )

        frame = pd.DataFrame(rows, columns=columns)
        return frame.sort_values("average", ascending=False, na_position="last").reset_index(drop=True)

    def subject_risk(self) -> pd.DataFrame:
        frame = self.subject_metrics()
        if frame.empty:
            return frame
        return frame.sort_values(
            ["fail_pct", "appeared"], ascending=[False, False], na_position="last"
        ).reset_index(drop=True)

    def grade_distribution(self) -> pd.DataFrame:
        """Ordered best-to-worst, not alphabetically."""
        subjects = self.subject_frame()
        if subjects.empty or "grade" not in subjects:
            return pd.DataFrame(columns=["grade", "students", "percentage"])

        grades = subjects.loc[subjects["grade"] != "", "grade"]
        if grades.empty:
            return pd.DataFrame(columns=["grade", "students", "percentage"])

        counts = grades.value_counts().rename_axis("grade").reset_index(name="students")
        counts["percentage"] = counts["students"] / counts["students"].sum() * 100
        counts["_order"] = counts["grade"].map(grade_sort_index)
        return counts.sort_values("_order").drop(columns="_order").reset_index(drop=True)

    def semester_metrics(self) -> pd.DataFrame:
        semesters = self.semester_frame()
        columns = [
            "semester_label", "students", "average_sgpa", "highest_sgpa",
            "lowest_sgpa", "pass_pct",
        ]
        if semesters.empty:
            return pd.DataFrame(columns=columns)

        rows = []
        for label, group in semesters.groupby("semester_label", dropna=False):
            sgpa = group["sgpa"].dropna()
            students = group["student_key"].nunique()
            failed = int((group["backlogs"] > 0).sum())
            rows.append(
                {
                    "semester_label": label,
                    "students": students,
                    "average_sgpa": float(sgpa.mean()) if not sgpa.empty else None,
                    "highest_sgpa": float(sgpa.max()) if not sgpa.empty else None,
                    "lowest_sgpa": float(sgpa.min()) if not sgpa.empty else None,
                    "pass_pct": (students - failed) / students * 100 if students else None,
                    "_order": group["semester_no"].dropna().min()
                    if group["semester_no"].notna().any() else 99,
                }
            )

        frame = pd.DataFrame(rows).sort_values("_order").drop(columns="_order")
        return frame[columns].reset_index(drop=True)

    def student_performance(self) -> pd.DataFrame:
        semesters = self.semester_frame()
        if semesters.empty:
            return pd.DataFrame()

        frame = (
            semesters.groupby(["student_key", "seat_no", "prn", "student_name", "branch"])
            .agg(
                average_sgpa=("sgpa", "mean"),
                highest_sgpa=("sgpa", "max"),
                backlogs=("backlogs", "sum"),
                semesters=("semester_label", "nunique"),
            )
            .reset_index()
        )
        frame["average_sgpa"] = frame["average_sgpa"].round(2)
        frame["highest_sgpa"] = frame["highest_sgpa"].round(2)
        frame = frame.sort_values(
            ["average_sgpa", "student_name"], ascending=[False, True], na_position="last"
        ).reset_index(drop=True)
        frame.insert(0, "rank", range(1, len(frame) + 1))
        return frame

    def backlog_analysis(self) -> pd.DataFrame:
        subjects = self.subject_frame()
        columns = ["prn", "student_name", "branch", "backlogs", "subjects"]
        if subjects.empty:
            return pd.DataFrame(columns=columns)

        failed = subjects[subjects["failed"].astype(bool)]
        if failed.empty:
            return pd.DataFrame(columns=columns)

        frame = (
            failed.groupby(["prn", "student_name", "branch"], dropna=False)
            .agg(
                backlogs=("subject_code", "count"),
                subjects=("subject_code", lambda codes: ", ".join(dict.fromkeys(codes))),
            )
            .reset_index()
        )
        return frame.sort_values("backlogs", ascending=False).reset_index(drop=True)

    # -----------------------------------------------------------------
    # Data quality — read this before trusting any number above
    # -----------------------------------------------------------------

    def data_quality(self) -> dict[str, Any]:
        marks = self.marks_frame()
        subjects = self.subject_frame()

        unmapped = sorted(
            marks.loc[marks["component"] == UNMAPPED, "field"].unique()
        ) if not marks.empty else []

        unparsed = (
            marks[marks["status"] == MarkStatus.UNPARSED.value][
                ["student_key", "subject_code", "field", "raw"]
            ]
            if not marks.empty else pd.DataFrame()
        )

        missing_total = (
            sorted(subjects.loc[~subjects["total_detected"], "subject_code"].unique())
            if not subjects.empty else []
        )

        return {
            "requires_review": self.result.requires_review,
            "review_notes": list(self.result.review_notes),
            "unmapped_fields": unmapped,
            "unparsed_cells": unparsed,
            "subjects_without_total": missing_total,
            "detected_fields": self.available_fields(),
        }

    def available_fields(self) -> list[str]:
        fields: set[str] = set()
        for student in self.result.students:
            for subjects in student.semesters.values():
                for subject in subjects.values():
                    fields.update(clean_text(name) for name in subject.fields)
        return sorted(field for field in fields if field)
