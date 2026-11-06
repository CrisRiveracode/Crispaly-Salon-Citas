"""Copiar un respaldo SQLite a PostgreSQL vacío sin borrar el origen ni el destino."""
from contextlib import closing
import argparse
import os
from pathlib import Path
import sqlite3
import sys
from urllib.parse import quote

from sqlalchemy import MetaData, create_engine, func, select, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class MigrationError(Exception):
    pass


def backup_sqlite(source, backup):
    source, backup = Path(source).resolve(), Path(backup).resolve()
    if not source.is_file():
        raise MigrationError('No existe el archivo SQLite de origen. No se creará una base vacía en su lugar.')
    if source == backup:
        raise MigrationError('El respaldo debe estar en otro archivo.')
    backup.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    with closing(sqlite3.connect('file:' + quote(str(source)) + '?mode=ro', uri=True)) as origin:
        with closing(sqlite3.connect(str(backup))) as snapshot:
            origin.backup(snapshot)
            if snapshot.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise MigrationError('El respaldo no pasó la comprobación de integridad.')
    return backup


def migrate(source, backup, target_engine, metadata):
    if target_engine.dialect.name != 'postgresql':
        raise MigrationError('El destino debe ser PostgreSQL.')
    if '-pooler.' in (target_engine.url.host or ''):
        raise MigrationError('Para migrar usa DATABASE_URL_UNPOOLED, la conexión directa de Neon.')
    snapshot = backup_sqlite(source, backup)
    source_engine = create_engine('sqlite://', creator=lambda: sqlite3.connect(
        'file:' + quote(str(snapshot)) + '?mode=ro', uri=True), hide_parameters=True)
    source_metadata = MetaData()
    source_metadata.reflect(bind=source_engine)
    counts = {}
    try:
        with source_engine.connect() as origin, target_engine.begin() as target:
            target.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key':783154992})
            names = ', '.join(target_engine.dialect.identifier_preparer.quote(t.name)
                              for t in metadata.sorted_tables)
            target.exec_driver_sql('LOCK TABLE ' + names + ' IN ACCESS EXCLUSIVE MODE')
            for table in metadata.sorted_tables:
                if target.scalar(select(func.count()).select_from(table)):
                    raise MigrationError('El destino ya contiene registros. La migración no sobreescribe ni mezcla datos.')
            for name, table in source_metadata.tables.items():
                if name not in metadata.tables and origin.scalar(select(func.count()).select_from(table)):
                    raise MigrationError('El origen contiene una tabla con datos que esta versión no reconoce. Hay que revisarla antes de migrar.')
            if not sum(origin.scalar(select(func.count()).select_from(t))
                       for name,t in source_metadata.tables.items() if name in metadata.tables):
                raise MigrationError('El origen está vacío. No se han recuperado datos del servicio de Render.')
            for table in metadata.sorted_tables:
                source_table = source_metadata.tables.get(table.name)
                if source_table is None:
                    counts[table.name] = 0
                    continue
                extras = set(source_table.columns.keys()) - set(table.columns.keys())
                if extras:
                    raise MigrationError('Una tabla de origen tiene columnas desconocidas. Se requiere revisión antes de copiarla.')
                result = origin.execute(select(source_table)).mappings()
                copied = 0
                while rows := result.fetchmany(50):
                    target.execute(table.insert(), [dict(row) for row in rows])
                    copied += len(rows)
                if target.scalar(select(func.count()).select_from(table)) != copied:
                    raise MigrationError('Los recuentos de la copia no coinciden.')
                counts[table.name] = copied
                for column in table.primary_key.columns:
                    if column.type.python_type is not int:
                        continue
                    sequence = target.scalar(text('SELECT pg_get_serial_sequence(:table, :column)'),
                                             {'table':table.name, 'column':column.name})
                    if sequence:
                        maximum = target.scalar(select(func.max(column)))
                        target.execute(text('SELECT setval(CAST(:sequence AS regclass), :value, :used)'),
                                       {'sequence':sequence, 'value':max(1,maximum or 1), 'used':maximum is not None})
        return counts
    finally:
        source_engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='Archivo SQLite conservado del servicio anterior')
    parser.add_argument('--backup', required=True, help='Ruta nueva para un respaldo privado; no debe existir')
    args = parser.parse_args()
    from database import load_local_environment
    load_local_environment(Path(__file__).resolve().parents[1])
    if not os.environ.get('DATABASE_URL'):
        parser.error('Configura DATABASE_URL de forma privada; no se acepta por la línea de comandos.')
    try:
        from app import app, db
        with app.app_context():
            from database import database_url
            direct = os.environ.get('DATABASE_URL_UNPOOLED')
            engine = create_engine(database_url({'DATABASE_URL':direct}), hide_parameters=True, connect_args={'connect_timeout':10, 'prepare_threshold':None}) if direct else db.engine
            try:
                counts = migrate(args.source, args.backup, engine, db.metadata)
            finally:
                if direct:
                    engine.dispose()
        print('Copia completada y recuentos verificados: ' + str(sum(counts.values())) + ' registros.')
        print('El respaldo y el archivo de origen se conservaron. No se modificó el SQLite original.')
    except (MigrationError, FileExistsError) as error:
        print('No se realizó la copia: ' + (str(error) if isinstance(error, MigrationError)
              else 'La ruta del respaldo ya existe; elige una nueva.'), file=sys.stderr)
        return 1
    except Exception as error:
        print('Migración interrumpida (' + type(error).__name__ + '). Revisa la conexión y el esquema; no compartas credenciales. Los datos insertados en esta operación se revierten.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
