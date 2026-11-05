import importlib.util
from pathlib import Path
import tempfile
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
        with patch('flask.Flask', factory):
            spec.loader.exec_module(self.module)
        self.client = self.module.app.test_client()
        self.payload = dict(nombre='Prueba', contacto='3312345678',
                            servicios=['Corte'], fecha='2099-12-30', hora='10:00')

    def tearDown(self):
        with self.module.app.app_context():
            self.module.db.session.remove()
            self.module.db.engine.dispose()
        self.temp.cleanup()

    def test_reservar_consultar_y_eliminar(self):
        response = self.client.post('/crear_cita', json=self.payload)
        self.assertEqual(response.status_code, 201)
        cita_id = response.json['id']
        self.assertEqual(self.client.get('/citas').json[0]['servicios'], ['Corte'])
        self.assertEqual(self.client.get('/horarios_ocupados/2099-12-30').json, ['10:00'])
        self.assertEqual(self.client.delete(f'/eliminar/{cita_id}').status_code, 200)
        self.assertEqual(self.client.get('/citas').json, [])

    def test_no_permite_duplicados(self):
        self.assertEqual(self.client.post('/crear_cita', json=self.payload).status_code, 201)
        self.assertEqual(self.client.post('/crear_cita', json=self.payload).status_code, 409)
        self.assertEqual(len(self.client.get('/citas').json), 1)

    def test_rechaza_datos_invalidos(self):
        for overrides in ({'nombre': ''}, {'contacto': ''}, {'servicios': ['Otro']},
                          {'fecha': 'ayer'}, {'fecha': '2020-01-01'},
                          {'hora': '23:00'}, {'hora': '10:30'}, {'fecha': '2099-12-27'}):
            with self.subTest(overrides=overrides):
                self.assertEqual(self.client.post('/crear_cita',
                    json={**self.payload, **overrides}).status_code, 400)
        self.assertEqual(self.client.post('/crear_cita', json=[]).status_code, 400)
        self.assertEqual(self.client.get('/citas').json, [])

    def test_pagina_incluye_formulario_conectado(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="reserva"', response.data)
        self.assertIn(b"fetch('/crear_cita'", response.data)


if __name__ == '__main__':
    unittest.main()
