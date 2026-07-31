import os
import pdfplumber

for name in ['FE 2024 pattern.pdf', 'FE 2025.pdf']:
    path = os.path.join('pdfs', name)
    print(f'\n=== {name} ===')
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages[:4], 1):
            text = page.extract_text() or ''
            print(f'--- page {i} ---')
            print(text[:6000])
            print()
