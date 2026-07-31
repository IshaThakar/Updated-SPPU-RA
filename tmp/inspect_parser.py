from pathlib import Path
from parser_engine import ResultParsingEngine

root = Path('pdfs')
for name in ['FE 2024 pattern.pdf', 'FE 2025.pdf']:
    path = root / name
    print('===', name, '===')
    result = ResultParsingEngine().parse(path)
    print('students', len(result.students))
    print('schema semesters', list(result.schema.keys())[:5])
    print('first student', result.students[0].seat_no, result.students[0].prn, result.students[0].name)
    for sem in list(result.students[0].semesters.keys())[:2]:
        print(' ', sem, list(result.students[0].semesters[sem].keys())[:5])
        for label, subject in list(result.students[0].semesters[sem].items())[:2]:
            print('   ', label, subject.fields)
    print()
