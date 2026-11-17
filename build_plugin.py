"""Build a QGIS Install-from-ZIP package using only the standard library."""
from configparser import ConfigParser
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


def add(archive, source, name):
    """Store a file as a regular, non-executable file (mode 0644)."""
    info = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    info.create_system = 3  # Unix, so the permission bits are honoured
    info.external_attr = (0o100644 & 0xFFFF) << 16
    archive.writestr(info, Path(source).read_bytes())


root = Path(__file__).resolve().parent
plugin = root / 'cogo_workbench'
metadata = ConfigParser()
metadata.read(plugin / 'metadata.txt', encoding='utf-8')
version = metadata['general']['version']
target = root / 'dist' / f'cogo_workbench-{version}.zip'
target.parent.mkdir(exist_ok=True)
with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
    for path in sorted(plugin.iterdir()):
        if path.is_file() and path.suffix in ('.py', '.txt', '.png'):
            add(archive, path, f'cogo_workbench/{path.name}')
    add(archive, root / 'README.md', 'cogo_workbench/README.md')
    add(archive, root / 'LICENSE', 'cogo_workbench/LICENSE')
    for path in sorted((root / 'examples').iterdir()):
        if path.is_file() and path.suffix in ('.csv', '.txt'):
            add(archive, path, f'cogo_workbench/examples/{path.name}')
with ZipFile(target) as archive:
    assert archive.testzip() is None
    assert all((i.external_attr >> 16) & 0o111 == 0 for i in archive.infolist())
    assert {'cogo_workbench/__init__.py', 'cogo_workbench/metadata.txt',
            'cogo_workbench/dock.py', 'cogo_workbench/legal.py',
            'cogo_workbench/examples/legal_rectangle.txt',
            'cogo_workbench/LICENSE', 'cogo_workbench/icon.png'}.issubset(archive.namelist())
print(target)
