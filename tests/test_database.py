from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest
import os
from unittest.mock import Mock, patch
from database import database_url, load_local_environment, initialize_schema
from sqlalchemy.engine import make_url
from tools.migrate_sqlite import backup_sqlite


class DatabaseConfigTest(unittest.TestCase):
    def test_prioridad_url_render_y_controlador(self):
        url=database_url({'DATABASE_URL':'postgres://test:testpass@db.example.test/salon','SALON_DATABASE_URI':'sqlite:///old.db'})
        self.assertTrue(url.startswith('postgresql+psycopg://'))
        self.assertIn('sslmode=require',url)

    def test_conserva_tls_mas_estricto(self):
        url=database_url({'DATABASE_URL':'postgresql://test:testpass@db.example.test/salon?sslmode=verify-full&sslrootcert=system'})
        self.assertIn('sslmode=verify-full',url)
        self.assertIn('sslrootcert=system',url)
        with self.assertRaises(RuntimeError):database_url({'DATABASE_URL':'postgresql://test:testpass@db.example.test/salon?sslmode=disable'})

    def test_render_no_vuelve_a_sqlite_temporal(self):
        with self.assertRaises(RuntimeError):database_url({'RENDER':'true'})
        with self.assertRaises(RuntimeError):database_url({'RENDER':'true','SALON_DATABASE_URI':'sqlite:///citas.db'})
        self.assertEqual(database_url({}),'sqlite:///citas.db')

    def test_error_de_url_no_expone_credenciales(self):
        with self.assertRaises(RuntimeError) as error:database_url({'DATABASE_URL':'postgresql://test:secreto-prueba@host:puerto/db'})
        self.assertNotIn('secreto-prueba',str(error.exception))

    def test_env_neon_prioridad_y_credenciales_literales(self):
        with TemporaryDirectory() as root:
            root=Path(root)
            (root/'.env').write_text('DATABASE_URL=base\nOTRA_VARIABLE=base\n')
            (root/'.env.local').write_text('DATABASE_URL=local-con-$literal\nOTRA_VARIABLE=local\n')
            with patch.dict(os.environ, {}, clear=True):
                load_local_environment(root)
                self.assertEqual(os.environ['DATABASE_URL'], 'local-con-$literal')
                self.assertEqual(os.environ['OTRA_VARIABLE'], 'local')
            with patch.dict(os.environ, {'DATABASE_URL':'valor-del-servidor'}, clear=True):
                load_local_environment(root)
                self.assertEqual(os.environ['DATABASE_URL'], 'valor-del-servidor')

    def test_esquema_no_se_prepara_en_otra_rama(self):
        db=Mock()
        db.engine.url=make_url('postgresql+psycopg://test:fake@ep-app-pooler.example.test/salon')
        with patch('database.create_engine') as create:
            with self.assertRaises(RuntimeError):
                initialize_schema(db, {'DATABASE_URL_UNPOOLED':'postgresql://test:fake@ep-otro.example.test/salon'})
            create.assert_not_called()

    def test_respaldo_consistente_no_sobreescribe(self):
        with TemporaryDirectory() as root:
            source=Path(root)/'source.db';backup=Path(root)/'backup.sqlite3'
            with sqlite3.connect(source) as connection:
                connection.execute('CREATE TABLE prueba (id INTEGER PRIMARY KEY, valor TEXT)')
                connection.execute('INSERT INTO prueba VALUES (1, ?)',('valor preservado',))
            backup_sqlite(source,backup)
            with sqlite3.connect(backup) as connection:
                self.assertEqual(connection.execute('SELECT valor FROM prueba').fetchone()[0],'valor preservado')
            with self.assertRaises(FileExistsError):backup_sqlite(source,backup)
            self.assertEqual(backup.stat().st_mode & 0o777,0o600)


if __name__=='__main__':unittest.main()
