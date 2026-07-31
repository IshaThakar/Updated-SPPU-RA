"""Common Excel writer. It is deliberately unaware of PDF layouts."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from models import ParsedResult
from utils import has_data


class ExcelWriter:
    def write(self, result: ParsedResult, output_path: Path) -> pd.DataFrame:
        dataframe = self.dataframe(result)
        columns = list(dataframe.columns)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Results"
        for row in range(1, 4):
            for column, value in enumerate(dataframe.columns.get_level_values(row - 1), start=1):
                worksheet.cell(row, column, value)
        for values in dataframe.itertuples(index=False, name=None):
            worksheet.append(list(values))
        columns = self._remove_empty_subject_columns(worksheet, columns)
        self._merge_headers(worksheet, columns)
        self._format(worksheet)
        workbook.save(output_path)
        return dataframe

    @staticmethod
    def dataframe(result: ParsedResult) -> pd.DataFrame:
        # Defensive final filter: never export fields that are blank for all students.
        active_schema = result.schema
        summary_fields = list(dict.fromkeys(field for student in result.students for field in student.summary))
        columns = [
            ("Student Information", "", "Seat Number"),
            ("Student Information", "", "PRN"),
            ("Student Information", "", "Student Name"),
            ("Student Information", "", "Mother Name"),
        ]
        columns.extend(("Student Information", "", field) for field in summary_fields)
        for semester, subjects in active_schema.items():
            for subject, fields in subjects.items():
                columns.extend((semester, subject, field) for field in fields)
        rows = []
        for student in result.students:
            row = {
                ("Student Information", "", "Seat Number"): student.seat_no,
                ("Student Information", "", "PRN"): student.prn,
                ("Student Information", "", "Student Name"): student.name,
                ("Student Information", "", "Mother Name"): student.mother_name,
            }
            for key, value in student.summary.items():
                row[("Student Information", "", key)] = value
            for semester, subjects in student.semesters.items():
                for label, subject in subjects.items():
                    for field, value in subject.fields.items():
                        row[(semester, label, field)] = value
            rows.append(row)
        return pd.DataFrame(rows, columns=pd.MultiIndex.from_tuples(columns))



    @staticmethod
    def _remove_empty_subject_columns(worksheet, columns):
        """Remove only subject columns that are dashes/blanks for every row.

        Parsing first writes the complete table, preserving all positional mark
        assignments.  Cleanup happens only after those values exist in Excel.
        """
        retained = list(columns)
        for column in range(worksheet.max_column, 4, -1):
            values = [worksheet.cell(row, column).value for row in range(4, worksheet.max_row + 1)]
            empty = all(value is None or str(value).strip() == "" or set(str(value).strip()) == {"-"} for value in values)
            if empty:
                worksheet.delete_cols(column)
                retained.pop(column - 1)
        return retained

    @staticmethod
    def _filtered_schema(result: ParsedResult):
        """Keep a subject column only when at least one student has a value."""
        filtered = {}
        for semester, subjects in result.schema.items():
            target = {}
            for subject_label, fields in subjects.items():
                retained = []
                for field in fields:
                    if any(
                        has_data(student.semesters.get(semester, {}).get(subject_label, type("Empty", (), {"fields": {}})()).fields.get(field, ""))
                        for student in result.students
                    ):
                        retained.append(field)
                if retained:
                    target[subject_label] = retained
            if target:
                filtered[semester] = target
        return filtered

    @staticmethod
    def _merge_headers(worksheet, columns: list[tuple[str, str, str]]) -> None:
        for header_row, level in ((1, 0), (2, 1)):
            start = 0
            while start < len(columns):
                label = columns[start][level]
                end = start
                while end + 1 < len(columns) and columns[end + 1][level] == label:
                    end += 1
                if label and end > start:
                    worksheet.merge_cells(start_row=header_row, start_column=start + 1, end_row=header_row, end_column=end + 1)
                start = end + 1

    @staticmethod
    def _format(worksheet) -> None:
        fill = PatternFill("solid", fgColor="1F4E78")
        font = Font(bold=True, color="FFFFFF")
        alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        border = Border(*(Side(style="thin", color="B7C9D6") for _ in range(4)))
        for row in worksheet.iter_rows():
            for cell in row:
                cell.alignment = alignment
                cell.border = border
                if cell.row <= 3:
                    cell.fill = fill
                    cell.font = font
        worksheet.freeze_panes = "E4"
        worksheet.auto_filter.ref = worksheet.dimensions
        for column in range(1, worksheet.max_column + 1):
            values = (str(worksheet.cell(row, column).value or "") for row in range(1, worksheet.max_row + 1))
            worksheet.column_dimensions[get_column_letter(column)].width = min(max(map(len, values)) + 2, 34)
