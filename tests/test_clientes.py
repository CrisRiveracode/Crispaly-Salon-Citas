from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, datetime
from io import BytesIO
import unittest
from PIL import Image
from werkzeug.security import check_password_hash
import clientes
import test_reservas

PASSWORD='Una-clave-segura-2026'


class ClientesTest(unittest.TestCase):
    def setUp(self):
        test_reservas.ReservasTest.setUp(self)
        self.models=self.module.app.extensions['clientes']
        self.actualizar_csrf()

    tearDown=test_reservas.ReservasTest.tearDown

    def actualizar_csrf(self):
        self.client.get('/clientes/acceso')
        with self.client.session_transaction() as session:
            self.customer_headers={'X-CSRF-Token':session['csrf']}

    def datos(self, **cambios):
        return {**dict(nombre='Ana María Rivera',telefono='3312345678',password=PASSWORD,
                        password_confirmacion=PASSWORD,consentimiento=True),**cambios}

    def registrar(self):
        response=self.client.post('/clientes/registro',json=self.datos(),headers=self.customer_headers)
        self.assertEqual(response.status_code,201)
        with self.client.session_transaction() as session:
            self.customer_headers={'X-CSRF-Token':session['csrf']}
            return session['cliente_id']

    def entrar(self, telefono='3312345678', password=PASSWORD):
        response=self.client.post('/clientes/entrar',json={'telefono':telefono,'password':password},headers=self.customer_headers)
        with self.client.session_transaction() as session:
            self.customer_headers={'X-CSRF-Token':session['csrf']}
        return response

    def test_registro_sin_sms_y_password_protegida(self):
        identidad=self.registrar()
        self.assertEqual(self.client.get('/clientes').status_code,200)
        self.assertIn(b'0 puntos',self.client.get('/clientes').data)
        with self.module.app.app_context():
            cuenta=self.module.db.session.get(self.models['CuentaCliente'],identidad)
            cliente=self.module.db.session.get(self.models['Cliente'],identidad)
            self.assertTrue(check_password_hash(cuenta.password_hash,PASSWORD))
            self.assertNotEqual(cuenta.password_hash,PASSWORD)
            self.assertEqual(cliente.telefono,'+523312345678')
            self.assertEqual(self.models['VerificacionPresencial'].query.count(),0)
        self.assertNotIn(b'753159',self.client.get('/clientes/acceso').data)

    def test_telefono_no_repetible_en_distintos_formatos(self):
        self.registrar()
        for numero in ['3312345678','+52 33 1234 5678','523312345678']:
            response=self.client.post('/clientes/registro',json=self.datos(telefono=numero),headers=self.customer_headers)
            self.assertEqual(response.status_code,409)
        with self.module.app.app_context():
            self.assertEqual(self.models['Cliente'].query.count(),1)
            self.assertEqual(self.models['CuentaCliente'].query.count(),1)

    def test_nombre_completo_telefono_y_password_obligatorios(self):
        for cambio in ({'nombre':''},{'nombre':'Ana'},{'telefono':''},{'telefono':'0000'},
                       {'password':'corta','password_confirmacion':'corta'},
                       {'password_confirmacion':'No coincide'},{'consentimiento':False}):
            with self.subTest(cambio=cambio):
                self.assertEqual(self.client.post('/clientes/registro',json=self.datos(**cambio),headers=self.customer_headers).status_code,400)
        self.assertEqual(self.client.post('/clientes/registro',json=self.datos()).status_code,400)
        with self.module.app.app_context():self.assertEqual(self.models['Cliente'].query.count(),0)

    def test_login_password_y_limites(self):
        self.registrar()
        self.client.post('/clientes/salir',headers=self.customer_headers)
        self.actualizar_csrf()
        self.assertEqual(self.entrar(password='equivocada').status_code,401)
        self.assertEqual(self.entrar('+52 33 1234 5678').status_code,200)
        self.client.post('/clientes/salir',headers=self.customer_headers)
        self.actualizar_csrf()
        for _ in range(5):self.assertEqual(self.entrar(password='equivocada').status_code,401)
        self.assertEqual(self.entrar().status_code,429)
        self.assertEqual(self.client.get('/clientes').status_code,302)

    def test_verificacion_presencial_diez_puntos_una_sola_vez(self):
        identidad=self.registrar();url=f'/admin/clientes/{identidad}/verificar-presencial'
        self.assertEqual(self.client.post(url,headers=self.customer_headers).status_code,401)
        self.assertEqual(self.admin.post(url).status_code,400)
        primera=self.admin.post(url,headers=self.headers)
        segunda=self.admin.post(url,headers=self.headers)
        self.assertEqual(primera.json['puntos_otorgados'],10)
        self.assertEqual(segunda.json['puntos_otorgados'],0)
        with self.module.app.app_context():self.assertEqual(self.models['VerificacionPresencial'].query.count(),1)
        self.assertIn(b'10 puntos',self.client.get('/clientes').data)

    def test_verificacion_presencial_simultanea_no_duplica_bono(self):
        identidad=self.registrar()
        def verificar(_):
            admin=self.module.app.test_client()
            with admin.session_transaction() as session:session.update(admin=True,csrf='prueba')
            response=admin.post(f'/admin/clientes/{identidad}/verificar-presencial',headers={'X-CSRF-Token':'prueba'})
            self.assertEqual(response.status_code,200)
            return response.json['puntos_otorgados']
        with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(sorted(pool.map(verificar,range(2))),[0,10])

    def test_reservas_previas_solo_despues_de_confirmacion_presencial(self):
        self.client.post('/crear_cita',json=self.payload)
        self.client.post('/crear_cita',json={**self.payload,'hora':'11:00','contacto':'5512345678'})
        identidad=self.registrar()
        self.assertNotIn(b'10:00',self.client.get('/clientes').data)
        self.admin.post(f'/admin/clientes/{identidad}/verificar-presencial',headers=self.headers)
        self.assertIn(b'10:00',self.client.get('/clientes').data)
        self.assertNotIn(b'11:00',self.client.get('/clientes').data)

    def test_reservas_desde_cuenta_se_asocian_sin_verificacion(self):
        self.registrar()
        response=self.client.post('/crear_cita',json={**self.payload,'nombre':'Nombre falso','contacto':'5512345678'})
        self.assertEqual(response.status_code,201)
        self.assertIn(b'10:00',self.client.get('/clientes').data)
        with self.module.app.app_context():
            cita=self.module.Cita.query.first()
            self.assertEqual(cita.nombre,'Ana María Rivera')
            self.assertEqual(cita.contacto,'+523312345678')
        # La cancelación elimina la asociación: reutilizar un id SQLite no expone otra reserva.
        self.admin.delete('/eliminar/'+str(response.json['id']),headers=self.headers)
        anonimo=self.module.app.test_client()
        anonimo.post('/crear_cita',json={**self.payload,'hora':'12:00','contacto':'5512345678'})
        self.assertNotIn(b'12:00',self.client.get('/clientes').data)

    def test_visitas_puntos_y_bono_presencial_se_suman_por_separado(self):
        identidad=self.registrar();hoy=datetime.now(clientes.ZoneInfo('America/Mexico_City')).date()
        base=f'/admin/clientes/{identidad}/visitas'
        datos={'fecha':str(hoy),'servicio':'Retoque de raíz','es_retoque':True,'importe':'950.50','proximo_retoque':str(hoy+timedelta(days=40))}
        self.assertEqual(self.admin.post(base,json=datos,headers=self.headers).status_code,201)
        self.assertEqual(self.admin.post(base,json=datos,headers=self.headers).status_code,409)
        self.admin.post(f'/admin/clientes/{identidad}/verificar-presencial',headers=self.headers)
        pagina=self.client.get('/clientes').data
        self.assertIn(b'20 puntos',pagina)
        self.assertIn(b'$950.50',pagina)
        self.assertIn((hoy+timedelta(days=40)).strftime('%d/%m/%Y').encode(),pagina)
        with self.module.app.app_context():visita_id=self.models['VisitaCliente'].query.first().id
        self.admin.post(base+f'/{visita_id}/anular',headers=self.headers)
        self.assertIn(b'10 puntos',self.client.get('/clientes').data)
        self.assertIn(b'Registro anulado',self.client.get('/clientes').data)

    def test_foto_privada_y_nombre_completo_al_editar(self):
        self.registrar();foto=BytesIO();Image.new('RGB',(80,90),'red').save(foto,'PNG');foto.seek(0)
        with self.client.session_transaction() as session:csrf=session['csrf']
        self.assertEqual(self.client.post('/clientes/perfil',data={'csrf':csrf,'nombre':'Ana Rivera','foto':(foto,'perfil.png')}).status_code,200)
        self.assertEqual(self.client.post('/clientes/perfil',data={'csrf':csrf,'nombre':'Ana'}).status_code,400)
        self.assertEqual(self.client.get('/clientes/perfil/foto').mimetype,'image/jpeg')
        self.assertEqual(self.module.app.test_client().get('/clientes/perfil/foto').status_code,302)
        self.assertEqual(self.client.post('/clientes/perfil/foto/eliminar',headers=self.customer_headers).status_code,200)
        self.assertEqual(self.client.get('/clientes/perfil/foto').status_code,404)

    def test_recuperacion_presencial_cierra_sesiones_previas(self):
        identidad=self.registrar();url=f'/admin/clientes/{identidad}/restablecer-acceso'
        nueva={'password':'Otra-password-segura','password_confirmacion':'Otra-password-segura'}
        self.assertEqual(self.admin.post(url,json=nueva,headers=self.headers).status_code,400)
        self.admin.post(f'/admin/clientes/{identidad}/verificar-presencial',headers=self.headers)
        self.assertEqual(self.admin.post(url,json=nueva,headers=self.headers).status_code,200)
        self.assertEqual(self.client.get('/clientes').status_code,302)
        self.actualizar_csrf()
        self.assertEqual(self.entrar().status_code,401)
        self.assertEqual(self.entrar(password=nueva['password']).status_code,200)

    def test_cliente_legado_sin_password_se_conserva_y_recupera_en_salon(self):
        with self.module.app.app_context():
            cliente=self.models['Cliente'](nombre='Clienta SMS Anterior',telefono='+523312345678')
            self.module.db.session.add(cliente);self.module.db.session.commit();identidad=cliente.id
        self.assertEqual(self.client.post('/clientes/registro',json=self.datos(),headers=self.customer_headers).status_code,409)
        self.assertEqual(self.entrar().status_code,401)
        self.admin.post(f'/admin/clientes/{identidad}/verificar-presencial',headers=self.headers)
        self.assertEqual(self.admin.post(f'/admin/clientes/{identidad}/restablecer-acceso',json={
            'password':PASSWORD,'password_confirmacion':PASSWORD},headers=self.headers).status_code,200)
        self.assertEqual(self.entrar().status_code,200)
        self.assertIn(b'Clienta SMS Anterior',self.client.get('/clientes').data)

    def test_normalizacion_telefono(self):
        for telefono in ['3312345678','+52 33 1234 5678','523312345678']:
            self.assertEqual(clientes.telefono_normalizado(telefono),'+523312345678')
        for telefono in ['correo@example.com','000','no es numero']:
            with self.assertRaises(ValueError):clientes.telefono_normalizado(telefono)


if __name__=='__main__':unittest.main()
