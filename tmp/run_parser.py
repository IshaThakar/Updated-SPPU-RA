import sys
from pathlib import Path
sys.path.insert(0, str(Path('.').resolve()))
from parser_engine import ResultParsingEngine

root = Path('pdfs')
for name in ['FE 2024 pattern.pdf', 'FE 2025.pdf']:
    path = root / name
    print('===', name, '===')
    result = ResultParsingEngine().parse(path)
    print('students', len(result.students))
    print('schema semesters', list(result.schema.keys())[:3])
    print('first student', result.students[0].seat_no, result.students[0].prn, result.students[0].name)
    print('semesters', list(result.students[0].semesters.keys())[:2])
    print()
