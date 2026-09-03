from pathlib import Path
import tempfile
import streamlit as st
from excel_writer import ExcelWriter
from parser_engine import ResultParsingEngine
from result_validator import review_summary
from utils import render_structure

st.set_page_config(page_title="SPPU Result Analyzer", page_icon="??", layout="wide")
st.title("SPPU Result Analyzer")
st.caption("Upload SPPU result PDFs. The app detects the layout, validates the structure, and creates an Excel workbook.")
st.caption("SGPA is exported once per detected semester; failed or withheld SGPA is shown as ----.")
uploads = st.file_uploader("Upload SPPU result PDFs", type=["pdf"], accept_multiple_files=True)
if uploads:
    engine, writer = ResultParsingEngine(), ExcelWriter()
    with tempfile.TemporaryDirectory() as folder:
        temp = Path(folder)
        for upload in uploads:
            source = temp / upload.name
            source.write_bytes(upload.getvalue())
            try:
                result = engine.parse(source)
                st.subheader(upload.name)
                st.code(f"{render_structure(result)}\n{review_summary(result)}", language="text")
                suffix = " - REVIEW REQUIRED" if result.requires_review else ""
                output = temp / f"{source.stem}{suffix}.xlsx"
                dataframe = writer.write(result, output)
                if result.requires_review:
                    st.warning("This file needs review. Its workbook includes a Review Required sheet and preserved Source Text.")
                else:
                    st.success(f"Verified: {len(result.students)} students, {dataframe.shape[1]} columns")
                st.download_button("Download Excel", output.read_bytes(), output.name, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key=upload.name)
            except Exception as error:
                st.error(f"{upload.name}: {error}")


