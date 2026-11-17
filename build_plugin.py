"""Build a QGIS Install-from-ZIP package using only the standard library."""
from configparser import ConfigParser
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


root = Path(__file__).resolve().parent
plugin = root / 'cogo_workbench'
metadata = ConfigParser()
metadata.read(plugin / 'metadata.txt', encoding='utf-8')
version = metadata['general']['version']
target = root / 'dist' / f'cogo_workbench-{version}.zip'
target.parent.mkdir(exist_ok=True)
with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
    for path in sorted(plugin.iterdir()):
        if path.is_file() and path.suffix in ('.py', '.txt'):
            archive.write(path, f'cogo_workbench/{path.name}')
    archive.write(root / 'README.md', 'cogo_workbench/README.md')
    for path in sorted((root / 'examples').iterdir()):
        if path.is_file() and path.suffix in ('.csv', '.txt'):
            archive.write(path, f'cogo_workbench/examples/{path.name}')
with ZipFile(target) as archive:
    assert archive.testzip() is None
    assert {'cogo_workbench/__init__.py', 'cogo_workbench/metadata.txt',
            'cogo_workbench/dock.py', 'cogo_workbench/legal.py',
            'cogo_workbench/examples/legal_rectangle.txt'}.issubset(archive.namelist())
print(target)
