import importlib.util
from pathlib import Path
import tempfile
import os
import uuid
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch
from flask import Flask


class ReservasTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        source = Path(__file__).resolve().parents[1] / 'app.py'
        spec = importlib.util.spec_from_file_location('salon_test', source)
        self.module = importlib.util.module_from_spec(spec)
        def factory(*args, **kwargs):
            return Flask(*args, **kwargs, instance_path=self.temp.name,
                         root_path=str(source.parent))
        self.pg_engine = None
        settings = {'ADMIN_PASSWORD':'solo-pruebas-123456','RENDER':'false',
                    'DATABASE_URL':'','DATABASE_URL_UNPOOLED':'','SALON_DATABASE_URI':'sqlite:///citas.db'}
        if os.environ.get('TEST_DATABASE_URL'):
            self.pg_engine = create_engine(os.environ['TEST_DATABASE_URL'], hide_parameters=True)
            self.pg_schema = 'test_' + uuid.uuid4().hex
            with self.pg_engine.begin() as connection:
                connection.execute(text('CREATE SCHEMA ' + self.pg_schema))
            settings['DATABASE_URL'] = make_url(os.environ['TEST_DATABASE_URL']).update_query_dict(
                {'options':'-csearch_path=' + self.pg_schema}).render_as_string(hide_password=False)
        with patch('flask.Flask', factory), patch.dict(os.environ, settings), patch('database.load_local_environment'):
            spec.loader.exec_module(self.module)
        self.client = self.module.app.test_client()
        self.admin = self.module.app.test_client()
        self.admin.get('/admin')
        with self.admin.session_transaction() as session:
            token = session['csrf']
        self.admin.post('/admin/login', data={'password': 'solo-pruebas-123456', 'csrf': token})
        with self.admin.session_transaction() as session:
            self.headers = {'X-CSRF-Token': session['csrf']}
        self.payload = dict(nombre='Prueba', contacto='3312345678',
                            servicios=['Corte'], fecha='2099-12-30', hora='10:00')

    def tearDown(self):
        with self.module.app.app_context():
            self.module.db.session.remove()
            self.module.db.engine.dispose()
        if self.pg_engine:
            with self.pg_engine.begin() as connection:
                connection.execute(text('DROP SCHEMA ' + self.pg_schema + ' CASCADE'))
            self.pg_engine.dispose()
        self.temp.cleanup()

    def test_reservar_consultar_y_eliminar(self):
        response = self.client.post('/crear_cita', json=self.payload)
        self.assertEqual(response.status_code, 201)
        cita_id = response.json['id']
        self.assertEqual(self.admin.get('/citas').json[0]['servicios'], ['Corte'])
        self.assertEqual(self.client.get('/horarios_ocupados/2099-12-30').json, ['10:00'])
        self.assertEqual(self.admin.delete(f'/eliminar/{cita_id}', headers=self.headers).status_code, 200)
        self.assertEqual(self.admin.get('/citas').json, [])

    def test_no_permite_duplicados(self):
        self.assertEqual(self.client.post('/crear_cita', json=self.payload).status_code, 201)
        self.assertEqual(self.client.post('/crear_cita', json=self.payload).status_code, 409)
        self.assertEqual(len(self.admin.get('/citas').json), 1)

    def test_rechaza_datos_invalidos(self):
        for overrides in ({'nombre': ''}, {'contacto': ''}, {'servicios': ['Otro']},
                          {'fecha': 'ayer'}, {'fecha': '2020-01-01'},
                          {'hora': '23:00'}, {'hora': '10:30'}, {'fecha': '2099-12-27'}):
            with self.subTest(overrides=overrides):
                self.assertEqual(self.client.post('/crear_cita',
                    json={**self.payload, **overrides}).status_code, 400)
        self.assertEqual(self.client.post('/crear_cita', json=[]).status_code, 400)
        self.assertEqual(self.admin.get('/citas').json, [])

    def test_pagina_incluye_formulario_conectado(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="reserva"', response.data)
        self.assertIn(b"fetch('/crear_cita'", response.data)

    def test_datos_y_cancelacion_son_privados(self):
        self.assertEqual(self.client.get('/citas').status_code, 401)
        self.assertEqual(self.client.delete('/eliminar/1').status_code, 401)
        self.assertEqual(self.client.get('/admin/bloqueos').status_code, 401)
        self.assertEqual(self.client.post('/admin/bloqueos', json={}).status_code, 401)
        self.assertEqual(self.admin.delete('/eliminar/1').status_code, 400)
        self.assertEqual(self.admin.post('/admin/logout').status_code, 400)

    def test_bloquear_y_desbloquear_hora(self):
        data = dict(fecha=self.payload['fecha'], hora=self.payload['hora'])
        response = self.admin.post('/admin/bloqueos', json=data, headers=self.headers)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.client.get('/horarios_ocupados/' + data['fecha']).json, ['10:00'])
        self.assertEqual(self.client.post('/crear_cita', json=self.payload).status_code, 409)
        self.assertEqual(self.admin.delete('/admin/bloqueos/' + str(response.json['id']),
                                          headers=self.headers).status_code, 200)
        self.assertEqual(self.client.post('/crear_cita', json=self.payload).status_code, 201)

    def test_bloquear_dia_y_no_ocultar_citas_existentes(self):
        response = self.admin.post('/admin/bloqueos', json=dict(fecha=self.payload['fecha'], hora='*'),
                                   headers=self.headers)
        self.assertEqual(response.status_code, 201)
        for hora in ('10:00', '15:00', '18:00'):
            self.assertEqual(self.client.post('/crear_cita', json={**self.payload, 'hora': hora}).status_code, 409)
        self.admin.delete('/admin/bloqueos/' + str(response.json['id']), headers=self.headers)
        self.client.post('/crear_cita', json=self.payload)
        self.assertEqual(self.admin.post('/admin/bloqueos', json=dict(fecha=self.payload['fecha'], hora='*'),
                                        headers=self.headers).status_code, 409)

    def test_login_logout_y_csrf(self):
        self.client.get('/admin')
        with self.client.session_transaction() as session:
            token = session['csrf']
        self.assertEqual(self.client.post('/admin/login', data={'password': 'solo-pruebas-123456'}).status_code, 400)
        self.assertEqual(self.client.post('/admin/login', data={'password': 'incorrecta', 'csrf': token}).status_code, 401)
        self.assertEqual(self.client.get('/citas').status_code, 401)
        self.assertEqual(self.client.post('/admin/login', data={'password': 'solo-pruebas-123456', 'csrf': token}).status_code, 302)
        self.assertEqual(self.client.get('/citas').status_code, 200)
        self.assertEqual(self.admin.post('/admin/logout', headers=self.headers).status_code, 302)
        self.assertEqual(self.admin.get('/citas').status_code, 401)

    def test_sin_password_panel_permanece_cerrado(self):
        self.module.admin_password = ''
        self.assertEqual(self.client.get('/admin').status_code, 503)
        self.assertEqual(self.admin.get('/citas').status_code, 401)

    def test_dos_reservas_simultaneas(self):
        def reservar(_):
            with self.module.app.test_client() as client:
                return client.post('/crear_cita', json=self.payload).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(reservar, range(2))), [201, 409])

    def test_limita_intentos_de_login(self):
        self.client.get('/admin')
        with self.client.session_transaction() as session:
            token = session['csrf']
        for _ in range(10):
            self.assertEqual(self.client.post('/admin/login', data={'password': 'incorrecta', 'csrf': token}).status_code, 401)
        self.assertEqual(self.client.post('/admin/login', data={'password': 'incorrecta', 'csrf': token}).status_code, 429)


if __name__ == '__main__':
    unittest.main()
