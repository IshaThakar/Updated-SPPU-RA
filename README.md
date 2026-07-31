# Universal SPPU Result Parsing Engine

Run every PDF placed in `pdfs/`:

```powershell
python -m pip install -r requirements.txt
python main.py
```

For each PDF, the program detects its layout from content, scans the entire document to discover the schema, prints that structure, then writes a same-named workbook in `output/`.

Layout-specific parsing is isolated in `parsers/`; the shared model, schema validation, and Excel writer do not depend on PDF layout. Add a future layout by implementing `BaseResultParser` and registering its content signature in `parser_factory.py`.
