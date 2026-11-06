"""Validar la configuración Neon y la aplicación Flask sin usar datos de producción."""
from pathlib import Path
import py_compile
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='crispaly-build-') as cache:
    sources = [*root.glob('*.py'), *root.joinpath('tools').glob('*.py')]
    for index, source in enumerate(sources):
        py_compile.compile(str(source), cfile=str(Path(cache) / f'{index}.pyc'), doraise=True)
print('PASS: módulos Python compilados.', flush=True)
result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=root)
raise SystemExit(result.returncode)
