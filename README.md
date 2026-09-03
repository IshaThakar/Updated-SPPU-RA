# Universal SPPU Result Parsing Engine

Run every PDF placed in `pdfs/`:

```powershell
python -m pip install -r requirements.txt
python main.py
```

For each PDF, the program detects its layout from content, reads the document once, discovers the complete result schema, prints that structure, then writes a same-named workbook in `output/`.

The BE student-result and college-ledger layouts are parsed into the same
multi-row Excel format. College ledgers read their complete multi-page Paper
List so subject names remain populated.

For an unfamiliar text PDF, the analyzer first preserves every extracted line
and then, when the local Ollama service is running, asks `granite4.1:8b` to
return source-grounded JSON. That recovery output always retains `Source Text`
and is labelled `Review Required`; it is never silently presented as a
verified marksheet. The normal result sheet is written only after validation
confirms unique student identifiers, detected subjects, and clean mark cells
(no merged values or invalid currency/replacement characters).

Scanned PDFs with no extractable text require an OCR engine or a
vision-capable model. They are exported with a review flag instead of
inventing data. Set `RESULT_ANALYZER_AI_FALLBACK=off` to use the lossless text
fallback without the local Granite recovery. `RESULT_ANALYZER_OLLAMA_URL` and
`RESULT_ANALYZER_OLLAMA_MODEL` optionally override the local service and model.

Layout-specific parsing is isolated in `parsers/`; the shared model, schema
validation, and Excel writer do not depend on PDF layout. Add a future layout
by implementing `BaseResultParser` and registering its content signature in
`parser_factory.py`.
