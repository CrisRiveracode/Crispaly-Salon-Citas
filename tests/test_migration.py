from datetime import date
from io import BytesIO
from pathlib import Path
import os
import unittest
from PIL import Image
from sqlalchemy import create_engine, select, func
from werkzeug.security import generate_password_hash
import test_reservas
from tools.migrate_sqlite import migrate, MigrationError


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'),'La migración requiere PostgreSQL de pruebas, no una base de producción.')
class MigrationTest(unittest.TestCase):
    setUp=test_reservas.ReservasTest.setUp
    tearDown=test_reservas.ReservasTest.tearDown

    def source(self, populated=True):
        path=Path(self.temp.name)/'source.db'
        engine=create_engine('sqlite:///'+str(path))
        metadata=self.module.db.metadata
        metadata.create_all(engine)
        photo=BytesIO();Image.new('RGB',(30,20),'brown').save(photo,'JPEG')
        if populated:
            with engine.begin() as c:
                c.execute(metadata.tables['cliente'].insert(),dict(id=7,nombre='Clienta Migrada',telefono='+523312345678',foto=photo.getvalue()))
                c.execute(metadata.tables['cuenta_cliente'].insert(),dict(cliente_id=7,password_hash=generate_password_hash('Clave-migrada-2026'),version=1))
                c.execute(metadata.tables['cita'].insert(),dict(id=42,nombre='Clienta Migrada',servicios='["Corte"]',fecha='2099-12-30',hora='10:00',contacto='+523312345678',estado='confirmada'))
                c.execute(metadata.tables['reserva_cliente'].insert(),dict(cita_id=42,cliente_id=7))
                c.execute(metadata.tables['verificacion_presencial'].insert(),dict(cliente_id=7,puntos=10))
                c.execute(metadata.tables['visita_cliente'].insert(),dict(id=5,cliente_id=7,fecha=date(2026,1,1),servicio='Retoque migrado',importe=95000,es_retoque=True,puntos=10))
                c.execute(metadata.tables['cotizacion'].insert(),dict(id=3,token='migrationtoken',nombre='Clienta Migrada',telefono='523312345678',cabello_actual='Cabello oscuro',objetivo='Quiero un balayage',presupuesto=150000))
                c.execute(metadata.tables['foto_cotizacion'].insert(),dict(id=9,cotizacion_id=3,tipo='actual',contenido=photo.getvalue()))
        engine.dispose()
        return path,photo.getvalue()

    def test_migra_cuentas_fotos_puntos_reservas_y_secuencias(self):
        source,photo=self.source()
        with self.module.app.app_context():
            counts=migrate(source,Path(self.temp.name)/'backup.sqlite3',self.module.db.engine,self.module.db.metadata)
            self.assertEqual(counts['cliente'],1)
            self.assertEqual(counts['foto_cotizacion'],1)
        self.client.get('/clientes/acceso')
        with self.client.session_transaction() as session:headers={'X-CSRF-Token':session['csrf']}
        login=self.client.post('/clientes/entrar',json={'telefono':'3312345678','password':'Clave-migrada-2026'},headers=headers)
        self.assertEqual(login.status_code,200)
        portal=self.client.get('/clientes')
        self.assertIn(b'20 puntos',portal.data)
        self.assertIn(b'Retoque migrado',portal.data)
        self.assertIn(b'10:00',portal.data)
        self.assertEqual(self.client.get('/clientes/perfil/foto').data,photo)
        self.assertEqual(self.admin.get('/admin/cotizaciones/migrationtoken/fotos/9').data,photo)
        self.assertEqual(self.client.post('/crear_cita',json={**self.payload,'hora':'12:00'}).status_code,201)
        with self.module.app.app_context():
            self.assertGreater(self.module.Cita.query.order_by(self.module.Cita.id.desc()).first().id,42)
        with self.client.session_transaction() as session:headers={'X-CSRF-Token':session['csrf']}
        response=self.client.post('/clientes/registro',json=dict(nombre='Otra Clienta Migrada',telefono='5512345678',password='Otra-clave-2026',password_confirmacion='Otra-clave-2026',consentimiento=True),headers=headers)
        self.assertEqual(response.status_code,201)
        with self.client.session_transaction() as session:self.assertGreater(session['cliente_id'],7)
        with self.module.app.app_context():
            with self.assertRaises(MigrationError):migrate(source,Path(self.temp.name)/'second.sqlite3',self.module.db.engine,self.module.db.metadata)
            self.assertEqual(self.module.Cita.query.count(),2)

    def test_origen_vacio_no_se_presenta_como_datos_recuperados(self):
        source,_=self.source(populated=False)
        with self.module.app.app_context():
            with self.assertRaises(MigrationError):migrate(source,Path(self.temp.name)/'backup.sqlite3',self.module.db.engine,self.module.db.metadata)
            self.assertEqual(self.module.db.session.scalar(select(func.count()).select_from(self.module.db.metadata.tables['cliente'])),0)


if __name__=='__main__':unittest.main()
