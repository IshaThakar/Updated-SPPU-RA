"""
Analytics layer for the SPPU Result Analyzer.

Reads ParsedResult directly.

The analytics layer:
- does not read the generated Excel workbook
- discovers subject fields dynamically
- handles different SPPU layouts
- calculates student, subject and semester metrics
- extracts or automatically computes SGPA from credits and grade points
- handles PDF-parsed numeric values such as "74", "74/100", "74 (100)"
- exposes data-quality information accurately
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import pandas as pd

from coercion import (
    ABSENT_TOKENS,
    FAILING_GRADES,
    MarkStatus,
    UNMAPPED,
    canonical_component,
    clean_text,
    grade_sort_index,
    is_failing_grade,
    normalize_grade,
    normalize_key,
    parse_mark,
    parse_semester,
    resolve_dash_columns,
)
from models import ParsedResult


# ============================================================
# GRADE POINT MAPPING (SPPU Standard)
# ============================================================

GRADE_POINT_MAP: dict[str, float] = {
    "O": 10.0,
    "A+": 9.0,
    "A": 8.0,
    "B+": 7.0,
    "B": 6.0,
    "C+": 5.0,
    "C": 5.0,
    "D": 4.0,
    "P": 4.0,
    "F": 0.0,
    "FF": 0.0,
    "AB": 0.0,
}


# ============================================================
# GENERAL HELPERS
# ============================================================


def student_key(student: Any) -> str:
    """Create a stable student identifier."""
    prn = clean_text(student.prn)
    if prn:
        return prn

    seat = clean_text(student.seat_no)
    if seat:
        return f"SEAT:{seat}"

    return f"NAME:{clean_text(student.name)}"


def safe_numeric(value: Any) -> float | None:
    """Safely convert a value into a numeric value."""
    if value is None or isinstance(value, bool):
        return None

    text = clean_text(value)
    if not text or set(text) == {"-"} or text.upper() in {"NA", "N/A", "NULL", "NONE", "NIL", "AB", "ABS", "FAIL", "FAILED", "ATKT"}:
        return None

    try:
        parsed = parse_mark(text)
        if parsed.value is not None:
            return float(parsed.value)
    except Exception:
        pass

    try:
        numeric = pd.to_numeric(text, errors="coerce")
        if pd.notna(numeric):
            return float(numeric)
    except Exception:
        pass

    m = re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text)
    if m:
        return float(m.group(0))

    return None


def extract_numeric_total(value: Any) -> float | None:
    """
    Recover a numeric Total Marks value from common PDF formats.
    Supported: 74, "74", "74/100", "74 / 100", "74(100)", "74 marks"
    """
    if value is None:
        return None

    text = clean_text(value)
    if not text or set(text) == {"-"}:
        return None

    try:
        numeric = pd.to_numeric(text, errors="coerce")
        if pd.notna(numeric):
            return float(numeric)
    except Exception:
        pass

    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:/|\()\s*\d+(?:\.\d+)?\s*\)?\s*", text)
    if m:
        return float(m.group(1))

    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:marks?|m)?\s*", text, flags=re.IGNORECASE)
    if m:
        return float(m.group(1))

    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    if len(numbers) == 1:
        return float(numbers[0])

    return None


# ============================================================
# SGPA HELPERS
# ============================================================


def contains_sgpa(value: Any) -> bool:
    """Return True when a value/key contains SGPA or GPA."""
    k = normalize_key(value)
    return "sgpa" in k or "gpa" in k


def flatten_summary(
    value: Any,
    path: tuple[str, ...] = (),
) -> list[tuple[tuple[str, ...], Any]]:
    """Flatten nested summary dictionaries/lists."""
    output: list[tuple[tuple[str, ...], Any]] = []

    if isinstance(value, Mapping):
        for key, child in value.items():
            current_path = (*path, clean_text(key))
            if isinstance(child, (Mapping, list, tuple)):
                output.extend(flatten_summary(child, current_path))
            else:
                output.append((current_path, child))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            current_path = (*path, str(index))
            if isinstance(child, (Mapping, list, tuple)):
                output.extend(flatten_summary(child, current_path))
            else:
                output.append((current_path, child))
    else:
        output.append((path, value))

    return output


def extract_sgpa_entries(summary: Any) -> list[dict[str, Any]]:
    """Find SGPA values anywhere inside a student's summary."""
    entries: list[dict[str, Any]] = []

    for path, raw_value in flatten_summary(summary):
        label = " ".join(clean_text(part) for part in path if clean_text(part))
        str_val = clean_text(raw_value)

        # 1. Composite SGPA e.g. "(1) 8.50, (2) 8.20" or "(1) 8.50 (2) --"
        composite_matches = re.findall(r"\(\s*(\d+)\s*\)\s*([0-9.]+|[-]+|[A-Za-z]+)", str_val)
        if composite_matches:
            for sem_str, sgpa_str in composite_matches:
                sem_no = int(sem_str)
                num_val = safe_numeric(sgpa_str)
                entries.append({
                    "path": path,
                    "label": f"Semester {sem_no} SGPA",
                    "semester_no": sem_no,
                    "value": num_val,
                })
            continue

        if not contains_sgpa(label):
            continue

        val = safe_numeric(str_val)
        sem_no = parse_semester(label)
        if sem_no is None:
            for part in path:
                sem_no = parse_semester(part)
                if sem_no is not None:
                    break

        entries.append({
            "path": path,
            "label": label,
            "semester_no": sem_no,
            "value": val,
        })

    return entries


# ============================================================
# RESULT ANALYZER
# ============================================================


class ResultAnalyzer:
    """Convert ParsedResult into analytical DataFrames and metrics."""

    def __init__(self, result: ParsedResult):
        self.result = result
        self._marks: pd.DataFrame | None = None
        self._subjects: pd.DataFrame | None = None
        self._semesters: pd.DataFrame | None = None

    # ========================================================
    # LEVEL 1: MARKS FRAME
    # ========================================================

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
                semester_row = {
                    **base,
                    "semester_label": clean_text(semester),
                    "semester_no": parse_semester(semester),
                }

                for label, subject in subjects.items():
                    subject_row = {
                        **semester_row,
                        "subject_code": clean_text(subject.code) or clean_text(label),
                        "subject_name": clean_text(subject.name),
                    }

                    for field_name, value in subject.fields.items():
                        comp = canonical_component(field_name)
                        mark = parse_mark(value)

                        status = mark.status.value
                        if comp == "grade":
                            norm_g = normalize_grade(value)
                            if norm_g in ABSENT_TOKENS:
                                status = MarkStatus.ABSENT.value
                            elif norm_g in FAILING_GRADES:
                                status = MarkStatus.FAIL.value
                            elif norm_g:
                                status = MarkStatus.SCORED.value

                        rows.append({
                            **subject_row,
                            "field": clean_text(field_name),
                            "component": comp,
                            "value": mark.value,
                            "status": status,
                            "raw": mark.raw,
                        })

        frame = pd.DataFrame(
            rows,
            columns=[
                "student_key",
                "seat_no",
                "prn",
                "student_name",
                "mother_name",
                "branch",
                "semester_label",
                "semester_no",
                "subject_code",
                "subject_name",
                "field",
                "component",
                "value",
                "status",
                "raw",
            ],
        )

        if not frame.empty:
            frame = self._resolve_dashes(frame)
            frame = self._fill_subject_names(frame)

        self._marks = frame
        return frame.copy()

    @staticmethod
    def _resolve_dashes(frame: pd.DataFrame) -> pd.DataFrame:
        groups = ["semester_label", "subject_code", "field"]
        for _, index in frame.groupby(groups, dropna=False).groups.items():
            block = frame.loc[index]
            dashes = block["status"] == MarkStatus.NOT_OFFERED.value
            if not dashes.any():
                continue

            resolved = resolve_dash_columns(parse_mark(raw) for raw in block["raw"])
            dash_indices = index[dashes.to_numpy()]
            frame.loc[dash_indices, "status"] = resolved.value
        return frame

    @staticmethod
    def _fill_subject_names(frame: pd.DataFrame) -> pd.DataFrame:
        named = (
            frame[frame["subject_name"] != ""]
            .drop_duplicates("subject_code")
            .set_index("subject_code")["subject_name"]
        )
        blank = frame["subject_name"] == ""
        frame.loc[blank, "subject_name"] = frame.loc[blank, "subject_code"].map(named).fillna("")
        return frame

    # ========================================================
    # LEVEL 2: SUBJECT FRAME
    # ========================================================

    def subject_frame(self) -> pd.DataFrame:
        if self._subjects is not None:
            return self._subjects.copy()

        marks = self.marks_frame()
        if marks.empty:
            self._subjects = pd.DataFrame()
            return self._subjects.copy()

        keys = [
            "student_key",
            "seat_no",
            "prn",
            "student_name",
            "branch",
            "semester_label",
            "semester_no",
            "subject_code",
            "subject_name",
        ]

        rows: list[dict[str, Any]] = []

        for key_values, group in marks.groupby(keys, dropna=False):
            record = dict(zip(keys, key_values))
            by_component = {component: block for component, block in group.groupby("component")}

            # 1. GRADE
            grade_block = by_component.get("grade")
            if grade_block is not None:
                record["grade"] = normalize_grade(grade_block.iloc[0]["raw"])
            else:
                record["grade"] = ""

            # 2. CREDIT
            credit_block = by_component.get("credit")
            if credit_block is not None:
                c_val = credit_block.iloc[0]["value"]
                if c_val is None:
                    c_val = extract_numeric_total(credit_block.iloc[0]["raw"])
                record["credit"] = c_val
            else:
                record["credit"] = None

            # 3. OTHER COMPONENTS
            comp_values = []
            for component in ("internal", "external", "theory", "termwork", "practical"):
                block = by_component.get(component)
                if block is not None:
                    value = block.iloc[0]["value"]
                    if value is None:
                        value = extract_numeric_total(block.iloc[0]["raw"])
                    record[component] = value
                    if value is not None:
                        comp_values.append(value)
                else:
                    record[component] = None

            # 4. TOTAL
            total_block = by_component.get("total")
            if total_block is not None:
                cell = total_block.iloc[0]
                record["total_detected"] = True
                total_value = cell["value"]
                if total_value is None:
                    total_value = extract_numeric_total(cell["raw"])
                record["total"] = total_value
                if total_value is not None:
                    record["total_status"] = MarkStatus.SCORED.value
                else:
                    record["total_status"] = cell["status"]
            else:
                # Fallback: sum component marks if available
                if comp_values:
                    record["total"] = sum(comp_values)
                    record["total_detected"] = True
                    record["total_status"] = MarkStatus.SCORED.value
                else:
                    record["total_detected"] = False
                    record["total"] = None
                    record["total_status"] = ""

            # 5. ATTEMPTED & FAILED
            record["attempted"] = self._is_attempted(record, group)
            record["failed"] = self._is_failed(record, group)

            rows.append(record)

        self._subjects = pd.DataFrame(rows)
        return self._subjects.copy()

    @staticmethod
    def _is_failed(record: dict[str, Any], group: pd.DataFrame) -> bool:
        grade = record.get("grade", "")
        if grade:
            if is_failing_grade(grade):
                return True

        if record.get("total_status") == MarkStatus.FAIL.value:
            return True

        result_block = group[group["component"] == "result"]
        if not result_block.empty:
            raw_result = result_block.iloc[0]["raw"]
            if is_failing_grade(raw_result) or normalize_key(raw_result).startswith("fail"):
                return True

        statuses = set(group["status"])
        if statuses and MarkStatus.ABSENT.value in statuses:
            if MarkStatus.SCORED.value not in statuses:
                return True

        return False

    @staticmethod
    def _is_attempted(record: dict[str, Any], group: pd.DataFrame) -> bool:
        grade = record.get("grade", "")
        if grade in {"AB", "ABS", "ABSENT"}:
            return False

        statuses = set(group["status"])
        if statuses & {MarkStatus.SCORED.value, MarkStatus.FAIL.value}:
            return True

        if record.get("total") is not None:
            return True

        return bool(grade) and grade not in {"AB", "ABS", "ABSENT", "NA"}

    # ========================================================
    # SEMESTER FRAME WITH CREDIT-WEIGHTED SGPA FALLBACK
    # ========================================================

    def semester_frame(self) -> pd.DataFrame:
        if self._semesters is not None:
            return self._semesters.copy()

        rows: list[dict[str, Any]] = []

        for student in self.result.students:
            key = student_key(student)
            summary = getattr(student, "summary", {})
            sgpa_entries = extract_sgpa_entries(summary)
            semesters = list(student.semesters.items())

            for position, (semester, subjects) in enumerate(semesters):
                semester_no = parse_semester(semester)
                sgpa = self._match_sgpa(
                    sgpa_entries=sgpa_entries,
                    semester_no=semester_no,
                    position=position,
                    total_semesters=len(semesters),
                    semester_label=semester,
                )

                rows.append({
                    "student_key": key,
                    "seat_no": clean_text(student.seat_no),
                    "prn": clean_text(student.prn),
                    "student_name": clean_text(student.name),
                    "branch": clean_text(student.branch),
                    "semester_label": clean_text(semester),
                    "semester_no": semester_no,
                    "sgpa": sgpa,
                    "subjects": len(subjects),
                })

        frame = pd.DataFrame(rows)
        if frame.empty:
            self._semesters = frame
            return frame.copy()

        frame["sgpa"] = pd.to_numeric(frame["sgpa"], errors="coerce")

        subjects = self.subject_frame()
        if not subjects.empty:
            backlog_counts = (
                subjects.groupby(["student_key", "semester_label"], dropna=False)
                .agg(backlogs=("failed", "sum"))
                .reset_index()
            )
            frame = frame.merge(backlog_counts, on=["student_key", "semester_label"], how="left")

        if "backlogs" not in frame.columns:
            frame["backlogs"] = 0

        frame["backlogs"] = pd.to_numeric(frame["backlogs"], errors="coerce").fillna(0).astype(int)

        # ----------------------------------------------------
        # Fallback: calculate SGPA from credits and grades
        # if SGPA was not found in PDF summary footer
        # ----------------------------------------------------
        if not subjects.empty:
            for idx, row in frame.iterrows():
                if pd.isna(row["sgpa"]) and row["backlogs"] == 0:
                    st_key = row["student_key"]
                    sem_lbl = row["semester_label"]
                    student_subjects = subjects[
                        (subjects["student_key"] == st_key)
                        & (subjects["semester_label"] == sem_lbl)
                    ]
                    calc_sgpa = self._calculate_sgpa_from_subjects(student_subjects)
                    if calc_sgpa is not None:
                        frame.at[idx, "sgpa"] = calc_sgpa

        frame["result_status"] = frame["backlogs"].apply(
            lambda count: "Failed / Backlog" if count > 0 else "Passed"
        )

        self._semesters = frame
        return frame.copy()

    @staticmethod
    def _calculate_sgpa_from_subjects(group: pd.DataFrame) -> float | None:
        """Calculate SGPA = sum(credit * GP) / sum(credits) for a semester."""
        total_credits = 0.0
        total_points = 0.0

        for _, row in group.iterrows():
            credit = row.get("credit")
            if credit is None or pd.isna(credit):
                continue
            try:
                credit = float(credit)
            except (ValueError, TypeError):
                continue

            if credit <= 0:
                continue

            grade = clean_text(row.get("grade", "")).upper()
            gp = GRADE_POINT_MAP.get(grade)
            if gp is not None:
                total_credits += credit
                total_points += credit * gp

        if total_credits > 0:
            return round(total_points / total_credits, 2)

        return None

    @staticmethod
    def _match_sgpa(
        sgpa_entries: list[dict[str, Any]],
        semester_no: int | None,
        position: int,
        total_semesters: int,
        semester_label: str = "",
    ) -> float | None:
        if not sgpa_entries:
            return None

        # 1. Explicit semester match
        if semester_no is not None:
            for entry in sgpa_entries:
                if entry.get("semester_no") == semester_no:
                    return safe_numeric(entry.get("value"))

        # 2. Safe positional match
        if len(sgpa_entries) == total_semesters:
            ordered = sorted(
                sgpa_entries,
                key=lambda entry: (
                    entry.get("semester_no") is None,
                    entry.get("semester_no") or 0,
                    entry.get("label", ""),
                ),
            )
            return safe_numeric(ordered[position].get("value"))

        # 3. Single-semester / Year-level fallback
        if len(sgpa_entries) == 1:
            return safe_numeric(sgpa_entries[0].get("value"))

        return None

    # ========================================================
    # SUMMARY METRICS & TOPPERS SPOTLIGHT
    # ========================================================

    def summary_metrics(self, semester: str = "All") -> dict[str, Any]:
        semesters = self.semester_frame()

        empty = {
            "total_students": 0,
            "passed_students": 0,
            "failed_students": 0,
            "pass_percentage": 0.0,
            "average_sgpa": None,
            "highest_sgpa": None,
            "highest_students": [],
            "lowest_sgpa": None,
            "lowest_students": [],
            "total_backlogs": 0,
            "students_with_backlogs": 0,
        }

        if semesters.empty:
            return empty

        perf = self.student_performance(semester=semester)
        if perf.empty:
            return empty

        total_students = len(perf)
        failed_students = int((perf["backlogs"] > 0).sum())
        passed_students = total_students - failed_students

        sgpa_col = "sgpa" if "sgpa" in perf.columns else "average_sgpa"
        valid_sgpa = pd.to_numeric(perf[sgpa_col], errors="coerce").dropna()
        valid_sgpa = valid_sgpa[(valid_sgpa >= 0.0) & (valid_sgpa <= 10.0)]

        highest_sgpa = round(float(valid_sgpa.max()), 2) if not valid_sgpa.empty else None
        lowest_sgpa = round(float(valid_sgpa.min()), 2) if not valid_sgpa.empty else None

        highest_students = []
        if highest_sgpa is not None:
            top_rows = perf[perf[sgpa_col] == highest_sgpa]
            for _, r in top_rows.iterrows():
                highest_students.append({
                    "name": r.get("student_name", ""),
                    "seat_no": r.get("seat_no", ""),
                    "prn": r.get("prn", ""),
                    "branch": r.get("branch", ""),
                    "sgpa": highest_sgpa,
                })

        lowest_students = []
        if lowest_sgpa is not None:
            low_rows = perf[perf[sgpa_col] == lowest_sgpa]
            for _, r in low_rows.iterrows():
                lowest_students.append({
                    "name": r.get("student_name", ""),
                    "seat_no": r.get("seat_no", ""),
                    "prn": r.get("prn", ""),
                    "branch": r.get("branch", ""),
                    "sgpa": lowest_sgpa,
                })

        return {
            "total_students": total_students,
            "passed_students": passed_students,
            "failed_students": failed_students,
            "pass_percentage": round(passed_students / total_students * 100, 2) if total_students else 0.0,
            "average_sgpa": round(float(valid_sgpa.mean()), 2) if not valid_sgpa.empty else None,
            "highest_sgpa": highest_sgpa,
            "highest_students": highest_students,
            "lowest_sgpa": lowest_sgpa,
            "lowest_students": lowest_students,
            "total_backlogs": int(perf["backlogs"].sum()),
            "students_with_backlogs": failed_students,
        }

    # ========================================================
    # STUDENT PERFORMANCE (SORTED RANKINGS)
    # ========================================================

    def student_performance(self, semester: str = "All") -> pd.DataFrame:
        semesters = self.semester_frame()
        if semesters.empty:
            return pd.DataFrame()

        semesters = semesters.copy()
        semesters["sgpa"] = pd.to_numeric(semesters["sgpa"], errors="coerce")
        semesters["backlogs"] = pd.to_numeric(semesters["backlogs"], errors="coerce").fillna(0).astype(int)

        if semester != "All":
            filtered = semesters[semesters["semester_label"].astype(str) == str(semester)].copy()
            if filtered.empty:
                return pd.DataFrame()

            frame = filtered[[
                "student_key",
                "seat_no",
                "prn",
                "student_name",
                "branch",
                "semester_label",
                "sgpa",
                "backlogs",
            ]].copy()

            frame["sgpa"] = pd.to_numeric(frame["sgpa"], errors="coerce").round(2)
            frame["result"] = frame["backlogs"].apply(lambda b: "Passed" if b == 0 else "Failed / ATKT")

            frame = frame.sort_values(
                by=["backlogs", "sgpa", "student_name"],
                ascending=[True, False, True],
                na_position="last",
            ).reset_index(drop=True)

            frame.insert(0, "rank", range(1, len(frame) + 1))
            return frame

        # Overall Cohort aggregation
        frame = (
            semesters.groupby(
                ["student_key", "seat_no", "prn", "student_name", "branch"],
                dropna=False,
            )
            .agg(
                average_sgpa=("sgpa", "mean"),
                highest_sgpa=("sgpa", "max"),
                backlogs=("backlogs", "sum"),
                semesters=("semester_label", "nunique"),
            )
            .reset_index()
        )

        frame["average_sgpa"] = pd.to_numeric(frame["average_sgpa"], errors="coerce").round(2)
        frame["highest_sgpa"] = pd.to_numeric(frame["highest_sgpa"], errors="coerce").round(2)
        frame["backlogs"] = pd.to_numeric(frame["backlogs"], errors="coerce").fillna(0).astype(int)
        frame["result"] = frame["backlogs"].apply(lambda b: "Passed" if b == 0 else "Failed / ATKT")

        frame = frame.sort_values(
            by=["backlogs", "average_sgpa", "student_name"],
            ascending=[True, False, True],
            na_position="last",
        ).reset_index(drop=True)

        frame.insert(0, "rank", range(1, len(frame) + 1))
        return frame

    # ========================================================
    # SGPA BRACKET DISTRIBUTION
    # ========================================================

    def sgpa_bracket_distribution(self, semester: str = "All") -> pd.DataFrame:
        perf = self.student_performance(semester=semester)
        columns = ["bracket", "students", "percentage"]

        if perf.empty:
            return pd.DataFrame(columns=columns)

        sgpa_col = "sgpa" if "sgpa" in perf.columns else "average_sgpa"

        def get_bracket(row: pd.Series) -> str:
            sgpa = row.get(sgpa_col)
            backlogs = row.get("backlogs", 0)

            if backlogs > 0 or (pd.notna(sgpa) and sgpa < 4.0):
                return "Failed / ATKT (< 4.0 or Backlog)"
            if pd.isna(sgpa):
                return "SGPA Not Available"
            if sgpa >= 9.0:
                return "9.00 - 10.00 (Outstanding / O)"
            if sgpa >= 8.0:
                return "8.00 - 8.99 (Distinction / A+)"
            if sgpa >= 7.0:
                return "7.00 - 7.99 (First Class / A)"
            if sgpa >= 6.0:
                return "6.00 - 6.99 (Higher Second / B+)"
            if sgpa >= 5.0:
                return "5.00 - 5.99 (Second Class / B)"
            return "4.00 - 4.99 (Pass Class / C)"

        order_map = {
            "9.00 - 10.00 (Outstanding / O)": 1,
            "8.00 - 8.99 (Distinction / A+)": 2,
            "7.00 - 7.99 (First Class / A)": 3,
            "6.00 - 6.99 (Higher Second / B+)": 4,
            "5.00 - 5.99 (Second Class / B)": 5,
            "4.00 - 4.99 (Pass Class / C)": 6,
            "Failed / ATKT (< 4.0 or Backlog)": 7,
            "SGPA Not Available": 8,
        }

        perf["_bracket"] = perf.apply(get_bracket, axis=1)
        counts = perf["_bracket"].value_counts().rename_axis("bracket").reset_index(name="students")
        total = counts["students"].sum()
        counts["percentage"] = (counts["students"] / total * 100).round(2) if total else 0.0
        counts["_order"] = counts["bracket"].map(order_map).fillna(99)

        return counts.sort_values("_order").drop(columns="_order").reset_index(drop=True)

    # ========================================================
    # SUBJECT METRICS
    # ========================================================

    def subject_metrics(self) -> pd.DataFrame:
        subjects = self.subject_frame()
        columns = [
            "semester_label",
            "subject_code",
            "subject",
            "appeared",
            "absent",
            "passed",
            "pass_pct",
            "fail_pct",
            "average",
            "highest",
            "lowest",
            "total_detected",
        ]

        if subjects.empty:
            return pd.DataFrame(columns=columns)

        rows: list[dict[str, Any]] = []

        for (semester, code), group in subjects.groupby(["semester_label", "subject_code"], dropna=False):
            attempted = group[group["attempted"]]
            marks = pd.to_numeric(attempted["total"], errors="coerce").dropna()
            appeared = len(attempted)
            failed = int(attempted["failed"].sum())
            passed = appeared - failed
            name = next((value for value in group["subject_name"] if value), "")

            rows.append({
                "semester_label": semester,
                "subject_code": code,
                "subject": f"{code} {name}".strip(),
                "appeared": appeared,
                "absent": int((~group["attempted"]).sum()),
                "passed": passed,
                "pass_pct": round(passed / appeared * 100, 2) if appeared else None,
                "fail_pct": round(failed / appeared * 100, 2) if appeared else None,
                "average": round(float(marks.mean()), 2) if not marks.empty else None,
                "highest": round(float(marks.max()), 2) if not marks.empty else None,
                "lowest": round(float(marks.min()), 2) if not marks.empty else None,
                "total_detected": bool(group["total_detected"].any()),
            })

        frame = pd.DataFrame(rows, columns=columns)
        if not frame.empty:
            frame = frame.sort_values("average", ascending=False, na_position="last").reset_index(drop=True)

        return frame

    # ========================================================
    # SUBJECT RISK
    # ========================================================

    def subject_risk(self) -> pd.DataFrame:
        frame = self.subject_metrics()
        if frame.empty:
            return frame

        return frame.sort_values(
            ["fail_pct", "appeared"],
            ascending=[False, False],
            na_position="last",
        ).reset_index(drop=True)

    # ========================================================
    # GRADE DISTRIBUTION
    # ========================================================

    def grade_distribution(self) -> pd.DataFrame:
        subjects = self.subject_frame()
        columns = ["grade", "students", "percentage"]

        if subjects.empty or "grade" not in subjects.columns:
            return pd.DataFrame(columns=columns)

        grades = subjects.loc[subjects["grade"] != "", "grade"]
        if grades.empty:
            return pd.DataFrame(columns=columns)

        counts = grades.value_counts().rename_axis("grade").reset_index(name="students")
        total = counts["students"].sum()
        counts["percentage"] = (counts["students"] / total * 100).round(2) if total else 0.0
        counts["_order"] = counts["grade"].map(grade_sort_index)

        return counts.sort_values("_order").drop(columns="_order").reset_index(drop=True)

    # ========================================================
    # SEMESTER METRICS
    # ========================================================

    def semester_metrics(self) -> pd.DataFrame:
        semesters = self.semester_frame()
        columns = [
            "semester_label",
            "students",
            "average_sgpa",
            "highest_sgpa",
            "lowest_sgpa",
            "pass_pct",
        ]

        if semesters.empty:
            return pd.DataFrame(columns=columns)

        rows: list[dict[str, Any]] = []

        for label, group in semesters.groupby("semester_label", dropna=False):
            sgpa = pd.to_numeric(group["sgpa"], errors="coerce").dropna()
            sgpa = sgpa[(sgpa >= 0.0) & (sgpa <= 10.0)]
            students = group["student_key"].nunique()
            failed = int((group["backlogs"] > 0).sum())
            semester_numbers = pd.to_numeric(group["semester_no"], errors="coerce").dropna()
            order = float(semester_numbers.min()) if not semester_numbers.empty else 99

            rows.append({
                "semester_label": label,
                "students": students,
                "average_sgpa": round(float(sgpa.mean()), 2) if not sgpa.empty else None,
                "highest_sgpa": round(float(sgpa.max()), 2) if not sgpa.empty else None,
                "lowest_sgpa": round(float(sgpa.min()), 2) if not sgpa.empty else None,
                "pass_pct": round((students - failed) / students * 100, 2) if students else None,
                "_order": order,
            })

        frame = pd.DataFrame(rows)
        if frame.empty:
            return pd.DataFrame(columns=columns)

        frame = frame.sort_values("_order").drop(columns="_order")
        return frame[columns].reset_index(drop=True)

    # ========================================================
    # BACKLOG ANALYSIS
    # ========================================================

    def backlog_analysis(self) -> pd.DataFrame:
        subjects = self.subject_frame()
        columns = [
            "prn",
            "student_name",
            "branch",
            "backlogs",
            "subjects",
        ]

        if subjects.empty:
            return pd.DataFrame(columns=columns)

        failed = subjects[subjects["failed"].astype(bool)]
        if failed.empty:
            return pd.DataFrame(columns=columns)

        frame = (
            failed.groupby(
                ["prn", "student_name", "branch"],
                dropna=False,
            )
            .agg(
                backlogs=("subject_code", "count"),
                subjects=("subject_code", lambda codes: ", ".join(dict.fromkeys(codes))),
            )
            .reset_index()
        )

        return frame.sort_values("backlogs", ascending=False).reset_index(drop=True)

    # ========================================================
    # DATA QUALITY
    # ========================================================

    def data_quality(self) -> dict[str, Any]:
        marks = self.marks_frame()
        subjects = self.subject_frame()

        if not marks.empty:
            unmapped = sorted(
                marks.loc[marks["component"] == UNMAPPED, "field"].dropna().unique()
            )
            unparsed = marks[
                (marks["status"] == MarkStatus.UNPARSED.value)
                & (marks["component"] != "grade")
                & (marks["component"] != "result")
            ][["student_key", "subject_code", "field", "raw"]]
        else:
            unmapped = []
            unparsed = pd.DataFrame()

        if not subjects.empty:
            missing_total = sorted(
                subjects.loc[~subjects["total_detected"], "subject_code"].dropna().unique()
            )
            detected_total_count = int(subjects["total_detected"].sum())
            total_subject_count = len(subjects)
        else:
            missing_total = []
            detected_total_count = 0
            total_subject_count = 0

        return {
            "requires_review": self.result.requires_review,
            "review_notes": list(self.result.review_notes),
            "unmapped_fields": unmapped,
            "unparsed_cells": unparsed,
            "subjects_without_total": missing_total,
            "detected_total_count": detected_total_count,
            "total_subject_count": total_subject_count,
            "detected_fields": self.available_fields(),
        }

    # ========================================================
    # AVAILABLE FIELDS
    # ========================================================

    def available_fields(self) -> list[str]:
        fields: set[str] = set()
        for student in self.result.students:
            for subjects in student.semesters.values():
                for subject in subjects.values():
                    fields.update(clean_text(name) for name in subject.fields)
        return sorted(field for field in fields if field)


# ============================================================
# CONVENIENCE FUNCTION
# ============================================================


def analyze_result(result: ParsedResult) -> ResultAnalyzer:
    """Convenience helper for Streamlit or other callers."""
    return ResultAnalyzer(result)
