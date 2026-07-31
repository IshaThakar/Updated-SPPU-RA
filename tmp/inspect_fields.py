import sys
from pathlib import Path
sys.path.insert(0, str(Path('.').resolve()))
from parser_engine import ResultParsingEngine

root = Path('pdfs')
for name in ['FE 2024 pattern.pdf','FE 2025.pdf']:
    path = root / name
    result = ResultParsingEngine().parse(path)
    print('===', name, '===')
    student = result.students[0]
    for semester, subjects in student.semesters.items():
        print(semester)
        for label, subject in list(subjects.items())[:4]:
            print(' ', label, subject.fields)
        print('subject count', len(subjects))
        break
    print('schema subjects', list(result.schema['Semester 1'].keys())[:10])
    print()
