from pathlib import Path
import tempfile

import pandas as pd
import streamlit as st

from excel_writer import ExcelWriter
from parser_engine import ResultParsingEngine
from result_validator import review_summary
from utils import render_structure
from analyzer import ResultAnalyzer


# ============================================================
# Helper
# ============================================================

def _format_metric(value):
    if value is None:
        return "N/A"

    try:
        if pd.isna(value):
            return "N/A"
    except Exception:
        pass

    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


# ============================================================
# Page configuration
# ============================================================

st.set_page_config(
    page_title="SPPU Result Analyzer",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📊 SPPU Result Analyzer")

st.caption(
    "Upload SPPU result PDFs. Automatically detects layout, parses marks, "
    "generates verified Excel exports, and visualizes comprehensive analytics."
)


# ============================================================
# PDF Upload
# ============================================================

uploads = st.file_uploader(
    "Upload SPPU Result PDFs",
    type=["pdf"],
    accept_multiple_files=True,
)


if uploads:

    engine = ResultParsingEngine()
    writer = ExcelWriter()

    with tempfile.TemporaryDirectory() as folder:

        temp = Path(folder)

        for upload in uploads:

            source = temp / upload.name
            source.write_bytes(upload.getvalue())

            try:

                # ========================================================
                # 1. PARSE PDF
                # ========================================================

                result = engine.parse(source)

                st.subheader(f"📄 {upload.name}")

                # Compact extraction summary
                with st.expander("📝 Layout Discovery & Parsing Verification", expanded=False):
                    st.code(
                        f"{render_structure(result)}\n"
                        f"{review_summary(result)}",
                        language="text",
                    )

                # ========================================================
                # 2. CREATE EXCEL EXPORT
                # ========================================================

                suffix = (
                    " - REVIEW REQUIRED"
                    if result.requires_review
                    else ""
                )

                output = temp / f"{source.stem}{suffix}.xlsx"
                dataframe = writer.write(result, output)

                col_stat, col_btn = st.columns([3, 1])
                with col_stat:
                    if result.requires_review:
                        st.warning(
                            "⚠️ This file requires review. Excel workbook contains "
                            "a 'Review Required' sheet and source text."
                        )
                    else:
                        st.success(
                            f"✅ Verified: {len(result.students)} students, "
                            f"{dataframe.shape[1]} columns extracted successfully."
                        )

                with col_btn:
                    st.download_button(
                        label="📥 Download Excel (.xlsx)",
                        data=output.read_bytes(),
                        file_name=output.name,
                        mime=(
                            "application/vnd.openxmlformats-officedocument."
                            "spreadsheetml.sheet"
                        ),
                        key=f"download_{upload.name}",
                        use_container_width=True,
                    )

                st.divider()

                # ========================================================
                # 3. ANALYTICS ENGINE
                # ========================================================

                analyzer = ResultAnalyzer(result)

                # Fetch available semesters
                semester_df = analyzer.semester_frame()
                available_semesters = ["All Semesters"]
                if not semester_df.empty and "semester_label" in semester_df.columns:
                    sems = sorted(semester_df["semester_label"].dropna().astype(str).unique())
                    available_semesters.extend(sems)

                # Header & Global Semester Selector
                head_col1, head_col2 = st.columns([2, 1])
                with head_col1:
                    st.header("Analytics Dashboard")
                with head_col2:
                    selected_sem_label = st.selectbox(
                        "Filter by Semester",
                        available_semesters,
                        key=f"global_sem_{upload.name}",
                    )

                current_sem = "All" if selected_sem_label == "All Semesters" else selected_sem_label

                # ========================================================
                # 4. ORGANIZED TABS LAYOUT
                # ========================================================

                tab_overview, tab_students, tab_subjects, tab_dist, tab_backlogs, tab_quality = st.tabs([
                    "Overview & KPIs",
                    "Student Rankings",
                    "Subject Analytics",
                    "Distributions",
                    "Backlog Tracker",
                    "🔍 Data Quality & Raw Data",
                ])

                # --------------------------------------------------------
                # TAB 1: OVERVIEW & KPIs
                # --------------------------------------------------------
                with tab_overview:
                    metrics = analyzer.summary_metrics(semester=current_sem)

                    st.subheader(f"Cohort Highlights ({selected_sem_label})")

                    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
                    with kpi1:
                        st.metric("Total Students", metrics.get("total_students", 0))
                    with kpi2:
                        st.metric("Passed", metrics.get("passed_students", 0))
                    with kpi3:
                        st.metric("Failed / ATKT", metrics.get("failed_students", 0))
                    with kpi4:
                        st.metric("Pass Rate", f"{metrics.get('pass_percentage', 0):.2f}%")

                    kpi5, kpi6, kpi7, kpi8 = st.columns(4)
                    with kpi5:
                        st.metric("Average SGPA", _format_metric(metrics.get("average_sgpa")))
                    with kpi6:
                        st.metric("Highest SGPA", _format_metric(metrics.get("highest_sgpa")))
                    with kpi7:
                        st.metric("Lowest SGPA", _format_metric(metrics.get("lowest_sgpa")))
                    with kpi8:
                        st.metric("Total Backlogs", metrics.get("total_backlogs", 0))

                    st.markdown("---")

                    # Toppers vs Lowest Performers Spotlight
                    spot_col1, spot_col2 = st.columns(2)

                    with spot_col1:
                        st.markdown("#### Top Performers (Highest SGPA)")
                        top_list = metrics.get("highest_students", [])
                        if top_list:
                            for idx, topper in enumerate(top_list, 1):
                                st.success(
                                    f"**#{idx} {topper['name']}** | SGPA: **{topper['sgpa']:.2f}**  \n"
                                    f"PRN: `{topper['prn'] or 'N/A'}` | Seat: `{topper['seat_no'] or 'N/A'}` | Branch: {topper['branch'] or 'N/A'}"
                                )
                        else:
                            st.info("No SGPA records found for top performers.")

                    with spot_col2:
                        st.markdown("#### Lowest SGPA Performers")
                        low_list = metrics.get("lowest_students", [])
                        if low_list:
                            for idx, student in enumerate(low_list, 1):
                                st.error(
                                    f"**{student['name']}** | SGPA: **{student['sgpa']:.2f}**  \n"
                                    f"PRN: `{student['prn'] or 'N/A'}` | Seat: `{student['seat_no'] or 'N/A'}` | Branch: {student['branch'] or 'N/A'}"
                                )
                        else:
                            st.info("No SGPA records found.")

                    st.markdown("---")
                    st.markdown("#### Semester-wise Performance Summary")
                    sem_summary_df = analyzer.semester_metrics()
                    if not sem_summary_df.empty:
                        st.dataframe(sem_summary_df, use_container_width=True, hide_index=True)
                    else:
                        st.info("Semester summary table is not available.")

                # --------------------------------------------------------
                # TAB 2: STUDENT RANKINGS & SGPA EXPLORER
                # --------------------------------------------------------
                with tab_students:
                    st.subheader(f"Student Rankings ({selected_sem_label})")

                    perf_df = analyzer.student_performance(semester=current_sem)

                    if not perf_df.empty:
                        filter_c1, filter_c2, filter_c3 = st.columns([1.5, 1.5, 2])

                        sgpa_col = "sgpa" if "sgpa" in perf_df.columns else "average_sgpa"

                        with filter_c1:
                            bracket_filter = st.selectbox(
                                "SGPA Bracket Filter",
                                [
                                    "All Brackets",
                                    "9.00 - 10.00 (Outstanding)",
                                    "8.00 - 8.99 (Distinction)",
                                    "7.00 - 7.99 (First Class)",
                                    "6.00 - 6.99 (Higher Second)",
                                    "5.00 - 5.99 (Second Class)",
                                    "4.00 - 4.99 (Pass Class)",
                                    "Failed / ATKT (< 4.0 or Backlog)",
                                    "SGPA Unavailable",
                                ],
                                key=f"bracket_{upload.name}",
                            )

                        with filter_c2:
                            result_filter = st.selectbox(
                                "Result Status",
                                ["All", "Passed Only", "Failed / ATKT Only"],
                                key=f"res_{upload.name}",
                            )

                        with filter_c3:
                            search = st.text_input(
                                "🔍 Search by Name, PRN, or Seat No",
                                key=f"search_{upload.name}",
                                placeholder="Type student name, PRN or Seat...",
                            )

                        display_df = perf_df.copy()

                        # Apply SGPA Bracket Filter
                        if bracket_filter == "9.00 - 10.00 (Outstanding)":
                            display_df = display_df[(display_df[sgpa_col] >= 9.0) & (display_df["backlogs"] == 0)]
                        elif bracket_filter == "8.00 - 8.99 (Distinction)":
                            display_df = display_df[(display_df[sgpa_col] >= 8.0) & (display_df[sgpa_col] < 9.0) & (display_df["backlogs"] == 0)]
                        elif bracket_filter == "7.00 - 7.99 (First Class)":
                            display_df = display_df[(display_df[sgpa_col] >= 7.0) & (display_df[sgpa_col] < 8.0) & (display_df["backlogs"] == 0)]
                        elif bracket_filter == "6.00 - 6.99 (Higher Second)":
                            display_df = display_df[(display_df[sgpa_col] >= 6.0) & (display_df[sgpa_col] < 7.0) & (display_df["backlogs"] == 0)]
                        elif bracket_filter == "5.00 - 5.99 (Second Class)":
                            display_df = display_df[(display_df[sgpa_col] >= 5.0) & (display_df[sgpa_col] < 6.0) & (display_df["backlogs"] == 0)]
                        elif bracket_filter == "4.00 - 4.99 (Pass Class)":
                            display_df = display_df[(display_df[sgpa_col] >= 4.0) & (display_df[sgpa_col] < 5.0) & (display_df["backlogs"] == 0)]
                        elif bracket_filter == "Failed / ATKT (< 4.0 or Backlog)":
                            display_df = display_df[(display_df["backlogs"] > 0) | ((display_df[sgpa_col].notna()) & (display_df[sgpa_col] < 4.0))]
                        elif bracket_filter == "SGPA Unavailable":
                            display_df = display_df[(display_df[sgpa_col].isna()) & (display_df["backlogs"] == 0)]

                        # Apply Result Filter
                        if result_filter == "Passed Only":
                            display_df = display_df[display_df["result"] == "Passed"]
                        elif result_filter == "Failed / ATKT Only":
                            display_df = display_df[display_df["result"] != "Passed"]

                        # Apply Search Filter
                        if search.strip():
                            s_val = search.strip().lower()
                            mask = display_df.astype(str).apply(
                                lambda row: row.str.lower().str.contains(s_val, regex=False, na=False)
                            ).any(axis=1)
                            display_df = display_df[mask]

                        st.caption(f"Showing **{len(display_df)}** of **{len(perf_df)}** students (Sorted by Rank / Highest SGPA)")

                        st.dataframe(
                            display_df.drop(columns=["student_key"], errors="ignore"),
                            use_container_width=True,
                            hide_index=True,
                        )
                    else:
                        st.info("No student performance data available for this selection.")

                # --------------------------------------------------------
                # TAB 3: SUBJECT ANALYTICS
                # --------------------------------------------------------
                with tab_subjects:
                    st.subheader(f"Subject Performance ({selected_sem_label})")

                    subject_df = analyzer.subject_metrics()

                    if not subject_df.empty:
                        disp_subj = subject_df.copy()
                        if current_sem != "All":
                            disp_subj = disp_subj[disp_subj["semester_label"].astype(str) == str(current_sem)]

                        if not disp_subj.empty:
                            st.dataframe(disp_subj, use_container_width=True, hide_index=True)

                            st.markdown("#### Average Marks by Subject")
                            if {"subject", "average"}.issubset(disp_subj.columns):
                                chart_df = disp_subj[["subject", "average"]].dropna().set_index("subject")
                                if not chart_df.empty:
                                    st.bar_chart(chart_df, y="average")

                            st.markdown("---")
                            st.markdown("#### Subject Risk / Highest Failure Rate")
                            risk_df = analyzer.subject_risk()
                            if current_sem != "All" and not risk_df.empty:
                                risk_df = risk_df[risk_df["semester_label"].astype(str) == str(current_sem)]
                            st.dataframe(risk_df, use_container_width=True, hide_index=True)
                        else:
                            st.info("No subject data available for the selected semester.")
                    else:
                        st.info("No subject metrics could be generated.")

                # --------------------------------------------------------
                # TAB 4: DISTRIBUTIONS (GRADE & SGPA)
                # --------------------------------------------------------
                with tab_dist:
                    dist_c1, dist_c2 = st.columns(2)

                    with dist_c1:
                        st.subheader("SGPA Tier Distribution")
                        sgpa_dist = analyzer.sgpa_bracket_distribution(semester=current_sem)
                        if not sgpa_dist.empty:
                            st.dataframe(sgpa_dist, use_container_width=True, hide_index=True)
                            chart_sgpa = sgpa_dist.set_index("bracket")[["students"]]
                            st.bar_chart(chart_sgpa)
                        else:
                            st.info("SGPA distribution is not available.")

                    with dist_c2:
                        st.subheader("Letter Grade Distribution")
                        grade_dist = analyzer.grade_distribution()
                        if not grade_dist.empty:
                            st.dataframe(grade_dist, use_container_width=True, hide_index=True)
                            chart_grade = grade_dist.set_index("grade")[["students"]]
                            st.bar_chart(chart_grade)
                        else:
                            st.info("Grade distribution is not available.")

                # --------------------------------------------------------
                # TAB 5: BACKLOG TRACKER
                # --------------------------------------------------------
                with tab_backlogs:
                    st.subheader("Backlog & ATKT Analysis")
                    backlog_df = analyzer.backlog_analysis()

                    if not backlog_df.empty:
                        st.warning(f"Found **{len(backlog_df)}** students with uncleared backlogs.")
                        st.dataframe(backlog_df, use_container_width=True, hide_index=True)
                    else:
                        st.success("🎉 All students cleared their subjects! No backlogs detected.")

                # --------------------------------------------------------
                # TAB 6: DATA QUALITY & RAW DATA
                # --------------------------------------------------------
                with tab_quality:
                    st.subheader("Extraction Quality & Diagnostics")
                    quality = analyzer.data_quality()

                    q_col1, q_col2 = st.columns(2)
                    with q_col1:
                        st.write("**Requires Review:**", quality.get("requires_review"))
                        st.write(
                            f"**Subjects With Total Detected:** {quality.get('detected_total_count', 0)} / {quality.get('total_subject_count', 0)}"
                        )

                    review_notes = quality.get("review_notes", [])
                    if review_notes:
                        st.markdown("### Review Notes")
                        for note in review_notes:
                            st.info(f"• {note}")

                    unmapped = quality.get("unmapped_fields", [])
                    if unmapped:
                        st.markdown("### Unmapped Assessment Fields")
                        st.write(unmapped)

                    unparsed = quality.get("unparsed_cells", pd.DataFrame())
                    has_unparsed = not unparsed.empty if isinstance(unparsed, pd.DataFrame) else bool(unparsed)
                    if has_unparsed:
                        st.markdown("### Unparsed Assessment Cells")
                        st.dataframe(unparsed, use_container_width=True, hide_index=True)

                    st.markdown("---")
                    st.subheader("Raw Analytical Dataset")
                    marks_df = analyzer.marks_frame()
                    if not marks_df.empty:
                        st.dataframe(marks_df, use_container_width=True, hide_index=True)
                    else:
                        st.info("No raw marks data available.")

            except Exception as error:
                st.error(f"Error processing {upload.name}: {error}")
