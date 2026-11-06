from datetime import timedelta, datetime
from io import BytesIO
import json
import os
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs
from PIL import Image
import clientes
import test_reservas


class SmsDePrueba:
    configured = True
    def __init__(self):
        self.enviados = []
        self.checks = []
    def send(self, telefono):
        self.enviados.append(telefono)
    def check(self, telefono, codigo):
        self.checks.append((telefono, codigo))
        return codigo == '753159'


class ClientesTest(unittest.TestCase):
    def setUp(self):
        test_reservas.ReservasTest.setUp(self)
        self.proveedor = SmsDePrueba()
        self.module.app.extensions['sms_verify'] = self.proveedor
        self.models = self.module.app.extensions['clientes']
        self.client.get('/clientes/acceso')
        with self.client.session_transaction() as session:
            self.customer_headers = {'X-CSRF-Token':session['csrf']}

    tearDown = test_reservas.ReservasTest.tearDown

    def solicitar(self, telefono='3312345678', nombre='Clienta Nueva'):
        return self.client.post('/clientes/verificacion/enviar',json={
            'telefono':telefono,'nombre':nombre,'consentimiento':True},headers=self.customer_headers)

    def verificar(self, codigo='753159'):
        response = self.client.post('/clientes/verificacion/comprobar',json={'codigo':codigo},headers=self.customer_headers)
        with self.client.session_transaction() as session:
            self.customer_headers = {'X-CSRF-Token':session['csrf']}
        return response

    def registrar(self):
        self.assertEqual(self.solicitar().status_code, 200)
        self.assertEqual(self.verificar().status_code, 200)
        with self.client.session_transaction() as session:
            return session['cliente_id']

    def test_registro_solo_tras_verificar_y_sin_duplicados(self):
        self.solicitar()
        with self.module.app.app_context():
            self.assertEqual(self.models['Cliente'].query.count(),0)
        self.assertEqual(self.verificar('000000').status_code,400)
        self.assertEqual(self.verificar().status_code,200)
        with self.client.session_transaction() as session:
            identidad=session['cliente_id']
        self.client.post('/clientes/salir', headers=self.customer_headers)
        self.client.get('/clientes/acceso')
        with self.client.session_transaction() as session:
            self.customer_headers={'X-CSRF-Token':session['csrf']}
        with self.module.app.app_context():
            challenge=self.models['VerificacionTelefono'].query.first()
            challenge.enviado-=timedelta(seconds=61);self.module.db.session.commit()
        self.assertEqual(self.solicitar('+52 33 1234 5678', 'Otro nombre').status_code,200)
        self.assertEqual(self.verificar().status_code,200)
        with self.client.session_transaction() as session:
            self.assertEqual(session['cliente_id'],identidad)
        with self.module.app.app_context():
            self.assertEqual(self.models['Cliente'].query.count(),1)
            self.assertEqual(self.models['Cliente'].query.first().nombre,'Clienta Nueva')
        self.assertEqual(self.verificar().status_code,400)

    def test_configuracion_sms_ausente_no_hay_bypass(self):
        with patch.dict(os.environ,{},clear=True):
            self.module.app.extensions['sms_verify']=clientes.TwilioVerify()
        self.assertEqual(self.solicitar().status_code,503)
        self.assertIn(b'Estamos preparando tu acceso', self.client.get('/clientes/acceso').data)
        self.assertEqual(self.verificar().status_code,400)
        with self.module.app.app_context():
            self.assertEqual(self.models['Cliente'].query.count(),0)

    def test_limites_codigo_caducidad_y_csrf(self):
        self.assertEqual(self.client.post('/clientes/verificacion/enviar',json={}).status_code,400)
        self.assertEqual(self.solicitar().status_code,200)
        self.assertEqual(self.solicitar().status_code,429)
        for _ in range(5):self.assertEqual(self.verificar('000000').status_code,400)
        self.assertEqual(self.verificar().status_code,429)
        with self.module.app.app_context():
            challenge=self.models['VerificacionTelefono'].query.first()
            challenge.enviado-=timedelta(minutes=11);self.module.db.session.commit()
        self.assertEqual(self.verificar().status_code,400)
        self.assertEqual(self.solicitar().status_code,200)
        with self.module.app.app_context():
            challenge=self.models['VerificacionTelefono'].query.first()
            challenge.enviado-=timedelta(seconds=61);self.module.db.session.commit()
        self.assertEqual(self.solicitar().status_code,200)
        with self.module.app.app_context():
            challenge=self.models['VerificacionTelefono'].query.first()
            challenge.enviado-=timedelta(seconds=61);self.module.db.session.commit()
        self.assertEqual(self.solicitar().status_code,429)

    def test_citas_solo_propias_y_reserva_con_identidad_verificada(self):
        # Una reserva antigua con el mismo número se recupera después de verificarlo.
        self.client.post('/crear_cita',json=self.payload)
        self.client.post('/crear_cita',json={**self.payload,'hora':'11:00','contacto':'5512345678','nombre':'Otra Persona'})
        self.registrar()
        portal=self.client.get('/clientes')
        self.assertEqual(portal.status_code,200)
        self.assertIn(b'10:00',portal.data)
        self.assertNotIn(b'11:00',portal.data)
        self.assertNotIn(b'Otra Persona',portal.data)
        self.assertIn(b'0 puntos',portal.data)
        self.assertEqual(self.client.post('/crear_cita',json={**self.payload,'hora':'12:00','contacto':'5512345678','nombre':'Falso'}).status_code,201)
        with self.module.app.app_context():
            cita=self.module.Cita.query.filter_by(hora='12:00').first()
            self.assertEqual(cita.contacto,'+523312345678')
            self.assertEqual(cita.nombre,'Clienta Nueva')

    def test_visitas_puntos_retoques_y_anulacion(self):
        identidad=self.registrar()
        hoy=datetime.now(clientes.ZoneInfo('America/Mexico_City')).date()
        datos={'fecha':str(hoy),'servicio':'Retoque de raíz','es_retoque':True,'importe':'950.50','proximo_retoque':str(hoy+timedelta(days=40)),'notas':'Usar champú para cabello teñido.'}
        base=f'/admin/clientes/{identidad}/visitas'
        self.assertEqual(self.client.post(base,json=datos,headers=self.customer_headers).status_code,401)
        self.assertEqual(self.admin.post(base,json=datos).status_code,400)
        self.assertEqual(self.admin.post(base,json=datos,headers=self.headers).status_code,201)
        self.assertEqual(self.admin.post(base,json=datos,headers=self.headers).status_code,409)
        self.assertEqual(self.admin.post(base,json={**datos,'fecha':str(hoy+timedelta(days=1))},headers=self.headers).status_code,400)
        portal=self.client.get('/clientes')
        self.assertIn(b'10 puntos',portal.data)
        self.assertIn(b'Retoque de ra',portal.data)
        self.assertIn(b'$950.50',portal.data)
        self.assertIn((hoy+timedelta(days=40)).strftime('%d/%m/%Y').encode(),portal.data)
        with self.module.app.app_context():
            visita_id=self.models['VisitaCliente'].query.first().id
        url=base+f'/{visita_id}/anular'
        self.assertEqual(self.admin.post(url,headers=self.headers).status_code,200)
        self.assertEqual(self.admin.post(url,headers=self.headers).status_code,200)
        self.assertIn(b'0 puntos',self.client.get('/clientes').data)
        self.assertIn(b'Registro anulado',self.client.get('/clientes').data)

    def test_foto_perfil_privada_y_edicion(self):
        self.registrar()
        foto=BytesIO();Image.new('RGB',(80,90),'red').save(foto,'PNG');foto.seek(0)
        with self.client.session_transaction() as session:csrf=session['csrf']
        response=self.client.post('/clientes/perfil',data={'csrf':csrf,'nombre':'Mi nombre actualizado','foto':(foto,'perfil.png')})
        self.assertEqual(response.status_code,200)
        image=self.client.get('/clientes/perfil/foto')
        self.assertEqual(image.mimetype,'image/jpeg')
        self.assertEqual(image.headers['Cache-Control'],'no-store')
        otra=self.module.app.test_client()
        self.assertEqual(otra.get('/clientes/perfil/foto').status_code,302)
        self.assertEqual(self.client.post('/clientes/perfil/foto/eliminar').status_code,400)
        self.assertEqual(self.client.post('/clientes/perfil/foto/eliminar',headers=self.customer_headers).status_code,200)
        self.assertEqual(self.client.get('/clientes/perfil/foto').status_code,404)

    def test_fallo_proveedor_y_presupuesto_diario(self):
        with patch.object(self.proveedor,'send',side_effect=clientes.VerificacionNoDisponible):
            self.assertEqual(self.solicitar().status_code,503)
        self.assertEqual(self.verificar().status_code,400)
        self.module.app.config['SMS_LIMITE_DIARIO']=1
        self.assertEqual(self.solicitar('5512345678').status_code,429)

    def test_normalizacion_telefono(self):
        for telefono in ['3312345678','+52 33 1234 5678','523312345678']:
            self.assertEqual(clientes.telefono_normalizado(telefono),'+523312345678')
        for telefono in ['correo@example.com','000','no es numero']:
            with self.assertRaises(ValueError):clientes.telefono_normalizado(telefono)

    def test_aislamiento_entre_dos_cuentas_verificadas(self):
        primera=self.registrar()
        hoy=datetime.now(clientes.ZoneInfo('America/Mexico_City')).date()
        self.admin.post(f'/admin/clientes/{primera}/visitas',json={
            'fecha':str(hoy),'servicio':'Historial privado primera clienta'},headers=self.headers)
        foto=BytesIO();Image.new('RGB',(20,20),'green').save(foto,'PNG');foto.seek(0)
        with self.client.session_transaction() as session:csrf=session['csrf']
        self.client.post('/clientes/perfil',data={'csrf':csrf,'nombre':'Primera clienta','foto':(foto,'foto.png')})
        otra=self.module.app.test_client();otra.get('/clientes/acceso')
        with otra.session_transaction() as session:headers={'X-CSRF-Token':session['csrf']}
        self.assertEqual(otra.post('/clientes/verificacion/enviar',json={
            'nombre':'Segunda clienta','telefono':'5512345678','consentimiento':True},headers=headers).status_code,200)
        self.assertEqual(otra.post('/clientes/verificacion/comprobar',json={'codigo':'753159'},headers=headers).status_code,200)
        pagina=otra.get('/clientes')
        self.assertNotIn(b'Historial privado primera clienta',pagina.data)
        self.assertIn(b'0 puntos',pagina.data)
        self.assertEqual(otra.get('/clientes/perfil/foto').status_code,404)
        self.assertEqual(self.client.get('/clientes/perfil/foto').status_code,200)
        with otra.session_transaction() as session:csrf=session['csrf']
        self.assertEqual(otra.post('/clientes/perfil',data={'csrf':csrf,'nombre':'Cambio solo propio','cliente_id':primera}).status_code,200)
        with self.module.app.app_context():
            self.assertEqual(self.module.db.session.get(self.models['Cliente'],primera).nombre,'Primera clienta')


class TwilioAdapterTest(unittest.TestCase):
    def test_adaptador_envia_sms_y_exige_estado_aprobado(self):
        with patch.dict(os.environ,{'TWILIO_ACCOUNT_SID':'AC'+'a'*32,'TWILIO_AUTH_TOKEN':'secreto-de-prueba','TWILIO_VERIFY_SERVICE_SID':'VA'+'b'*32}):
            adapter=clientes.TwilioVerify()
        self.assertTrue(adapter.configured)
        with patch('clientes.urlopen') as open_url:
            open_url.return_value.__enter__.return_value=BytesIO(b'{"status":"pending"}')
            adapter.send('+523312345678')
            req=open_url.call_args.args[0]
            self.assertEqual(parse_qs(req.data.decode())['Channel'],['sms'])
            self.assertTrue(req.full_url.startswith('https://verify.twilio.com/'))
        with patch.object(adapter,'_post',return_value={'status':'pending'}):
            self.assertFalse(adapter.check('+523312345678','753159'))
        with patch.object(adapter,'_post',return_value={'status':'approved'}):
            self.assertTrue(adapter.check('+523312345678','753159'))


if __name__=='__main__':unittest.main()
