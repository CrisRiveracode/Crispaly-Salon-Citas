from io import BytesIO
import re
import unittest
from urllib.parse import parse_qs, urlsplit
from PIL import Image
import test_reservas


class CotizacionesTest(unittest.TestCase):
    setUp = test_reservas.ReservasTest.setUp
    tearDown = test_reservas.ReservasTest.tearDown

    def datos(self, **cambios):
        self.client.get('/cotizar')
        with self.client.session_transaction() as session:
            csrf = session['csrf']
        foto = BytesIO()
        image = Image.new('RGB', (80, 60), 'brown')
        exif = Image.Exif(); exif[315] = 'Dato privado de la cámara'
        image.save(foto, 'JPEG', exif=exif)
        foto.seek(0)
        return dict(csrf=csrf, nombre='Clienta Prueba', telefono='3312345678',
                    cabello_actual='Cabello largo, oscuro y con puntas secas.',
                    objetivo='Quiero un balayage caramelo natural.', historial='Tinte hace seis meses.',
                    presupuesto='1500.50', consentimiento='si', fotos_actuales=(foto, 'cabello.jpg'),
                    **cambios)

    def crear(self, cambios=None):
        data = self.datos()
        data.update(cambios or {})
        response = self.client.post('/cotizaciones', data=data, content_type='multipart/form-data')
        response.request.input_stream.close()
        return response

    def detalle_path(self, response):
        text = parse_qs(urlsplit(response.json['whatsapp_url']).query)['text'][0]
        return re.search(r'https://crispaly-salon-citas.onrender.com(/admin/cotizaciones/[\w-]+)', text).group(1)

    def test_solicitud_completa_privada_y_respuesta(self):
        response = self.crear()
        self.assertEqual(response.status_code, 201)
        parts = urlsplit(response.json['whatsapp_url'])
        self.assertEqual(parts.netloc, 'wa.me')
        self.assertEqual(parts.path, '/523315370476')
        mensaje = parse_qs(parts.query)['text'][0]
        self.assertIn('$1,500.50 MXN', mensaje)
        self.assertIn('balayage', mensaje)
        path = self.detalle_path(response)
        self.assertEqual(self.client.get(path).status_code, 302)
        detalle = self.admin.get(path)
        self.assertEqual(detalle.status_code, 200)
        self.assertIn(b'Clienta Prueba', detalle.data)
        photo_path = re.search(rb'src="([^"]+/fotos/\d+)"', detalle.data).group(1).decode()
        self.assertEqual(self.client.get(photo_path).status_code, 401)
        foto = self.admin.get(photo_path)
        self.assertEqual(foto.status_code, 200)
        self.assertEqual(foto.mimetype, 'image/jpeg')
        self.assertEqual(foto.headers['Cache-Control'], 'no-store')
        image = Image.open(BytesIO(foto.data))
        self.assertEqual(image.size, (80, 60))
        self.assertFalse(image.getexif())
        reply = self.admin.post(path+'/respuesta', json=dict(importe='1800.25', observaciones='Incluye matiz y tratamiento hidratante.'), headers=self.headers)
        self.assertEqual(reply.status_code, 200)
        reply_url = urlsplit(reply.json['whatsapp_url'])
        self.assertEqual(reply_url.path, '/523312345678')
        reply_text = parse_qs(reply_url.query)['text'][0]
        self.assertIn('$1,800.25 MXN', reply_text)
        self.assertIn('no reserva una cita', reply_text)
        self.assertIn(b'Respuesta preparada', self.admin.get(path).data)
        self.assertEqual(self.admin.delete(path, headers=self.headers).status_code, 200)
        self.assertEqual(self.admin.get(photo_path).status_code, 404)
        with self.module.app.app_context():
            self.assertEqual(self.module.db.session.execute(self.module.text('SELECT COUNT(*) FROM foto_cotizacion')).scalar(), 0)

    def test_validaciones_y_no_guardado_parcial(self):
        for cambio in ({'nombre':''}, {'telefono':'texto-no-numero'}, {'presupuesto':'-1'},
                       {'presupuesto':'NaN'}, {'presupuesto':'Infinity'}, {'presupuesto':'1.001'},
                       {'objetivo':'corto'}, {'consentimiento':''}, {'referencia_url':'javascript:alert(1)'},
                       {'fotos_actuales':(BytesIO(b'<script>no es foto</script>'), 'foto.jpg')},
                       {'fotos_actuales':(BytesIO(b'x'*(5*1024*1024+1)), 'grande.jpg')}):
            with self.subTest(cambio=list(cambio)):
                self.assertEqual(self.crear(cambio).status_code, 400)
        datos = self.datos(); datos.pop('fotos_actuales')
        self.assertEqual(self.client.post('/cotizaciones', data=datos).status_code, 400)
        with self.module.app.app_context():
            self.assertEqual(self.module.db.session.execute(self.module.text('SELECT COUNT(*) FROM cotizacion')).scalar(), 0)
            self.assertEqual(self.module.db.session.execute(self.module.text('SELECT COUNT(*) FROM foto_cotizacion')).scalar(), 0)

    def test_fotos_de_referencia_y_limite(self):
        data = self.datos()
        data['fotos_referencia'] = [(BytesIO(self._png()), 'ejemplo.png') for _ in range(3)]
        response = self.client.post('/cotizaciones', data=data)
        self.assertEqual(response.status_code, 201)
        self.assertIn(b'4 fotos', self.admin.get('/admin/cotizaciones').data)
        response = self.crear({'fotos_actuales':[(BytesIO(self._png()), 'cabello.png') for _ in range(4)]})
        self.assertEqual(response.status_code, 400)

    def _png(self):
        output = BytesIO(); Image.new('RGB', (50, 50), 'red').save(output, 'PNG')
        return output.getvalue()

    def test_filtro_estado_y_proteccion_csrf(self):
        response = self.crear(); path = self.detalle_path(response)
        self.assertEqual(self.client.post(path+'/estado', json={'estado':'cerrada'}).status_code, 401)
        self.assertEqual(self.admin.post(path+'/estado', json={'estado':'cerrada'}).status_code, 400)
        self.assertEqual(self.admin.post(path+'/estado', json={'estado':'inexistente'}, headers=self.headers).status_code, 400)
        self.assertEqual(self.admin.post(path+'/estado', json={'estado':'en_revision'}, headers=self.headers).status_code, 200)
        self.assertIn(b'Clienta Prueba', self.admin.get('/admin/cotizaciones?estado=en_revision').data)
        self.assertNotIn(b'Clienta Prueba', self.admin.get('/admin/cotizaciones?estado=cerrada').data)
        self.assertEqual(self.admin.delete(path).status_code, 400)
        self.assertEqual(self.admin.post(path+'/respuesta', json={'importe':'0','observaciones':'Muy corto'}, headers=self.headers).status_code, 400)

    def test_volver_al_enlace_tras_login_y_sin_redireccion_externa(self):
        path = self.detalle_path(self.crear())
        response = self.client.get(path, follow_redirects=True)
        self.assertIn(b'name="next"', response.data)
        with self.client.session_transaction() as session:
            csrf = session['csrf']
        login = self.client.post('/admin/login', data={'password':'solo-pruebas-123456', 'csrf':csrf, 'next':path})
        self.assertEqual(login.location, path)
        with self.client.session_transaction() as session:
            csrf = session['csrf']
        login = self.client.post('/admin/login', data={'password':'solo-pruebas-123456', 'csrf':csrf, 'next':'https://evil.invalid'})
        self.assertEqual(login.location, '/admin')

    def test_limite_carga_y_csrf_publico(self):
        datos = self.datos(); datos['csrf'] = 'incorrecto'
        self.assertEqual(self.client.post('/cotizaciones', data=datos).status_code, 400)
        self.module.app.config['MAX_CONTENT_LENGTH'] = 100
        self.assertEqual(self.client.post('/cotizaciones', data=self.datos()).status_code, 413)
        self.assertEqual(self.client.get('/cotizar').headers['Cache-Control'], 'no-store')


if __name__ == '__main__':
    unittest.main()
