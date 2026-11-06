"""Portal de clientes: identidad verificada, historial y visitas realizadas."""
import base64
from datetime import datetime, timedelta, timezone
from functools import wraps
from io import BytesIO
import json
import os
import re
import secrets
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import phonenumbers
from flask import (Blueprint, jsonify, redirect, render_template, request,
                   send_file, session, url_for)
from sqlalchemy import text, func
from sqlalchemy.exc import IntegrityError
from zoneinfo import ZoneInfo
from cotizaciones import dinero_centavos, foto_jpeg


def ahora_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def telefono_normalizado(value):
    if not isinstance(value, str) or len(value) > 30 or not re.fullmatch(r'[+\d\s().-]+', value):
        raise ValueError('Escribe un teléfono válido con código de país, o 10 dígitos para México.')
    digits = re.sub(r'\D', '', value)
    if len(digits) == 12 and digits.startswith('52'):
        value = '+' + digits
    try:
        number = phonenumbers.parse(value, 'MX')
        if not phonenumbers.is_valid_number(number):
            raise ValueError()
        return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    except (phonenumbers.NumberParseException, ValueError):
        raise ValueError('Revisa el teléfono. Para otro país, incluye + y el código de país.')


class VerificacionNoDisponible(Exception):
    pass


class TwilioVerify:
    """Solo Twilio decide si el código es válido. Nunca hay un código local de acceso."""
    def __init__(self):
        self.account = os.environ.get('TWILIO_ACCOUNT_SID', '')
        self.token = os.environ.get('TWILIO_AUTH_TOKEN', '')
        self.service = os.environ.get('TWILIO_VERIFY_SERVICE_SID', '')

    @property
    def configured(self):
        return bool(re.fullmatch(r'AC[\da-fA-F]{32}', self.account)
                    and re.fullmatch(r'VA[\da-fA-F]{32}', self.service) and self.token)

    def _post(self, endpoint, data):
        if not self.configured:
            raise VerificacionNoDisponible()
        authorization = base64.b64encode(f'{self.account}:{self.token}'.encode()).decode()
        req = Request(f'https://verify.twilio.com/v2/Services/{self.service}/{endpoint}',
                      data=urlencode(data).encode(), method='POST',
                      headers={'Authorization': 'Basic ' + authorization,
                               'Content-Type': 'application/x-www-form-urlencoded'})
        try:
            with urlopen(req, timeout=12) as response:
                data = json.load(response)
                if not isinstance(data, dict):
                    raise VerificacionNoDisponible()
                return data
        except HTTPError as error:
            error.close()
            # Twilio expira/elimina las verificaciones consumidas. No es aprobación.
            if endpoint == 'VerificationCheck' and error.code == 404:
                return {'status': 'expired'}
            raise VerificacionNoDisponible() from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise VerificacionNoDisponible() from None

    def send(self, telefono):
        response = self._post('Verifications', {'To': telefono, 'Channel': 'sms'})
        if response.get('status') != 'pending':
            raise VerificacionNoDisponible()

    def check(self, telefono, codigo):
        return self._post('VerificationCheck', {'To': telefono, 'Code': codigo}).get('status') == 'approved'


def register_clientes(app, db, Cita, admin_required, csrf_token, valid_csrf):
    bp = Blueprint('clientes', __name__)
    app.extensions['sms_verify'] = TwilioVerify()
    puntos_visita = 10
    try:
        puntos_visita = max(1, min(1000, int(os.environ.get('PUNTOS_POR_VISITA', '10'))))
    except ValueError:
        pass

    class Cliente(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        nombre = db.Column(db.String(100), nullable=False)
        telefono = db.Column(db.String(16), unique=True, nullable=False, index=True)
        verificado = db.Column(db.DateTime, nullable=False, default=ahora_utc)
        creado = db.Column(db.DateTime, nullable=False, default=ahora_utc)
        foto = db.deferred(db.Column(db.LargeBinary))

    class VerificacionTelefono(db.Model):
        telefono = db.Column(db.String(16), primary_key=True)
        referencia = db.Column(db.String(64), unique=True)
        nombre = db.Column(db.String(100), nullable=False)
        enviado = db.Column(db.DateTime, nullable=False)
        ventana = db.Column(db.DateTime, nullable=False)
        envios = db.Column(db.Integer, nullable=False, default=0)
        intentos = db.Column(db.Integer, nullable=False, default=0)

    class EnvioSms(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        fecha = db.Column(db.DateTime, nullable=False, default=ahora_utc, index=True)

    app.config.setdefault('SMS_LIMITE_DIARIO', 50)

    class VisitaCliente(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=False, index=True)
        fecha = db.Column(db.Date, nullable=False)
        servicio = db.Column(db.String(200), nullable=False)
        notas = db.Column(db.Text, nullable=False, default='')
        importe = db.Column(db.Integer, nullable=False, default=0)
        es_retoque = db.Column(db.Boolean, nullable=False, default=False)
        proximo_retoque = db.Column(db.Date)
        puntos = db.Column(db.Integer, nullable=False)
        anulada = db.Column(db.Boolean, nullable=False, default=False)
        creada = db.Column(db.DateTime, nullable=False, default=ahora_utc)
        __table_args__ = (db.UniqueConstraint('cliente_id', 'fecha'),)

    def actual():
        identity = session.get('cliente_id')
        return db.session.get(Cliente, identity) if isinstance(identity, int) else None

    def require_customer(view):
        @wraps(view)
        def protected(*args, **kwargs):
            if actual() is None:
                if request.method == 'GET':
                    return redirect(url_for('clientes.acceso'))
                return jsonify(error='Inicia sesión para continuar.'), 401
            if request.method != 'GET' and not valid_csrf():
                return jsonify(error='Recarga la página e intenta de nuevo.'), 400
            return view(*args, **kwargs)
        return protected

    def admin_page(view):
        protected = admin_required(view)
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get('admin'):
                return redirect(url_for('admin', next=request.path))
            return protected(*args, **kwargs)
        return wrapped

    def visitas(cliente_id):
        return db.session.scalars(db.select(VisitaCliente).filter_by(cliente_id=cliente_id)
                                  .order_by(VisitaCliente.fecha.desc(), VisitaCliente.id.desc())).all()

    def citas_cliente(cliente):
        # Incluye las reservas anteriores que registraron este mismo teléfono.
        resultado = []
        for cita in db.session.scalars(db.select(Cita).order_by(Cita.fecha, Cita.hora)).all():
            try:
                if telefono_normalizado(cita.contacto) == cliente.telefono:
                    resultado.append(cita)
            except ValueError:
                continue
        return resultado

    app.extensions['clientes'] = {'actual': actual, 'Cliente': Cliente,
                                  'VisitaCliente': VisitaCliente,
                                  'VerificacionTelefono': VerificacionTelefono}

    @bp.route('/clientes/acceso')
    def acceso():
        if actual():
            return redirect(url_for('clientes.portal'))
        referencia = session.get('telefono_pendiente')
        pending = db.session.scalar(db.select(VerificacionTelefono).filter_by(referencia=referencia)) if referencia else None
        activo = bool(pending and ahora_utc() < pending.enviado + timedelta(minutes=10))
        return render_template('clientes_acceso.html', csrf=csrf_token(),
                               disponible=app.extensions['sms_verify'].configured,
                               pendiente=activo, telefono=pending.telefono if activo else '')

    @bp.route('/clientes/verificacion/enviar', methods=['POST'])
    def enviar_codigo():
        if not valid_csrf():
            return jsonify(error='Recarga la página e intenta de nuevo.'), 400
        provider = app.extensions['sms_verify']
        if not provider.configured:
            return jsonify(error='El registro por SMS está pendiente de activación. Puedes seguir reservando o solicitar una cotización.'), 503
        data = request.get_json(silent=True)
        try:
            if not isinstance(data, dict):
                raise ValueError('Completa tu nombre y teléfono.')
            nombre = data.get('nombre', '')
            if not isinstance(nombre, str) or not 2 <= len(nombre.strip()) <= 100:
                raise ValueError('Escribe tu nombre, de 2 a 100 caracteres.')
            if data.get('consentimiento') is not True:
                raise ValueError('Autoriza el envío del código para verificar tu teléfono.')
            telefono = telefono_normalizado(data.get('telefono'))
        except ValueError as error:
            return jsonify(error=str(error)), 400
        now = ahora_utc()
        db.session.execute(text('BEGIN IMMEDIATE'))
        usados = db.session.scalar(db.select(func.count()).select_from(EnvioSms).where(
            EnvioSms.fecha >= now.replace(hour=0, minute=0, second=0, microsecond=0)))
        if usados >= app.config['SMS_LIMITE_DIARIO']:
            db.session.rollback()
            return jsonify(error='Se alcanzó el límite diario de códigos del salón. Intenta mañana.'), 429
        challenge = db.session.get(VerificacionTelefono, telefono)
        if challenge and challenge.enviado > now - timedelta(seconds=60):
            db.session.rollback()
            return jsonify(error='Espera un minuto antes de pedir otro código.'), 429
        if challenge and challenge.ventana > now - timedelta(hours=1) and challenge.envios >= 3:
            db.session.rollback()
            return jsonify(error='Alcanzaste el límite de tres envíos por hora. Intenta más tarde.'), 429
        if not challenge:
            challenge = VerificacionTelefono(telefono=telefono, nombre=nombre.strip(),
                                              enviado=now, ventana=now, envios=0, intentos=0)
            db.session.add(challenge)
        if challenge.ventana <= now - timedelta(hours=1):
            challenge.ventana = now
            challenge.envios = 0
        challenge.envios += 1
        challenge.enviado = now
        challenge.nombre = nombre.strip()
        challenge.intentos = 0
        reference = secrets.token_urlsafe(32)
        challenge.referencia = reference
        db.session.add(EnvioSms(fecha=now))
        db.session.commit()
        try:
            provider.send(telefono)
        except VerificacionNoDisponible:
            challenge.referencia = None
            db.session.commit()
            return jsonify(error='No pudimos enviar el SMS. Revisa el número e intenta de nuevo en un minuto.'), 503
        session['telefono_pendiente'] = reference
        return jsonify(ok=True, telefono=telefono, vence_en=600)

    @bp.route('/clientes/verificacion/comprobar', methods=['POST'])
    def comprobar_codigo():
        if not valid_csrf():
            return jsonify(error='Recarga la página e intenta de nuevo.'), 400
        reference = session.get('telefono_pendiente')
        data = request.get_json(silent=True)
        codigo = data.get('codigo', '') if isinstance(data, dict) else ''
        if not isinstance(codigo, str) or not re.fullmatch(r'\d{4,10}', codigo):
            return jsonify(error='Escribe el código numérico del SMS.'), 400
        db.session.execute(text('BEGIN IMMEDIATE'))
        challenge = db.session.scalar(db.select(VerificacionTelefono).filter_by(referencia=reference)) if reference else None
        if not challenge or challenge.enviado < ahora_utc() - timedelta(minutes=10):
            db.session.rollback()
            return jsonify(error='El código venció. Solicita uno nuevo.'), 400
        if challenge.intentos >= 5:
            db.session.rollback()
            return jsonify(error='Demasiados intentos. Solicita un nuevo código.'), 429
        challenge.intentos += 1
        telefono, nombre = challenge.telefono, challenge.nombre
        db.session.commit()
        try:
            approved = app.extensions['sms_verify'].check(telefono, codigo)
        except VerificacionNoDisponible:
            return jsonify(error='No pudimos verificar el código. Intenta nuevamente.'), 503
        if not approved:
            return jsonify(error='El código no es válido o ya venció.'), 400
        db.session.execute(text('BEGIN IMMEDIATE'))
        challenge = db.session.get(VerificacionTelefono, telefono)
        if challenge.referencia != reference or challenge.enviado < ahora_utc() - timedelta(minutes=10):
            db.session.rollback()
            return jsonify(error='Esta verificación ya no es válida. Solicita un nuevo código.'), 400
        cliente = db.session.scalar(db.select(Cliente).filter_by(telefono=telefono))
        if not cliente:
            cliente = Cliente(nombre=nombre, telefono=telefono)
            db.session.add(cliente)
        challenge.referencia = None
        db.session.commit()
        session.pop('telefono_pendiente', None)
        session.pop('csrf', None)
        session['cliente_id'] = cliente.id
        session.permanent = True
        csrf_token()
        return jsonify(ok=True, destino='/clientes')

    def inspiracion():
        mes = datetime.now(ZoneInfo('America/Mexico_City')).month
        temporada = ('invierno' if mes in (12, 1, 2) else 'primavera' if mes in (3, 4, 5)
                     else 'verano' if mes in (6, 7, 8) else 'otoño')
        return temporada, [
            {'titulo':'Tonos cálidos y luminosos', 'descripcion':'Explora chocolate, caramelo y reflejos que aporten luz. Podemos valorar qué tono se adapta a tu base.',
             'foto':'3993320', 'alt':'Trabajo de estilismo en un salón'},
            {'titulo':'Un corte con movimiento', 'descripcion':'Capas y un acabado natural para renovar tu look conservando tu estilo. Trae tus referencias para personalizarlo.',
             'foto':'3993449', 'alt':'Herramientas y atención profesional de cabello'},
            {'titulo':'Cuidado antes del siguiente cambio', 'descripcion':'Antes de un retoque, revisa cómo están tus puntas y comparte tus tratamientos recientes con tu estilista.',
             'foto':'3992874', 'alt':'Cuidado profesional del cabello en salón'},
        ]

    @bp.route('/clientes')
    @require_customer
    def portal():
        cliente = actual()
        historial = visitas(cliente.id)
        realizadas = [v for v in historial if not v.anulada]
        now = datetime.now(ZoneInfo('America/Mexico_City')).replace(tzinfo=None)
        programadas = []
        for cita in citas_cliente(cliente):
            try:
                if datetime.fromisoformat(cita.fecha + 'T' + cita.hora) >= now:
                    programadas.append({'id':cita.id, 'fecha':cita.fecha, 'hora':cita.hora,
                                        'servicios': json.loads(cita.servicios)})
            except (ValueError, TypeError):
                continue
        temporada, ideas = inspiracion()
        return render_template('clientes_portal.html', cliente=cliente, csrf=csrf_token(),
                               visitas=historial, puntos=sum(v.puntos for v in realizadas),
                               numero_visitas=len(realizadas), puntos_visita=puntos_visita,
                               ultimo=realizadas[0] if realizadas else None,
                               ultimo_retoque=next((v for v in realizadas if v.es_retoque), None),
                               siguiente=next((v.proximo_retoque for v in realizadas if v.proximo_retoque), None),
                               citas=programadas, temporada=temporada, ideas=ideas)

    @bp.route('/clientes/perfil', methods=['GET', 'POST'])
    @require_customer
    def perfil():
        cliente = actual()
        if request.method == 'GET':
            return render_template('clientes_perfil.html', cliente=cliente, csrf=csrf_token())
        nombre = request.form.get('nombre', '').strip()
        if not 2 <= len(nombre) <= 100:
            return jsonify(error='El nombre debe tener de 2 a 100 caracteres.'), 400
        foto = request.files.get('foto')
        try:
            contenido = foto_jpeg(foto) if foto and foto.filename else None
        except ValueError as error:
            return jsonify(error=str(error)), 400
        cliente.nombre = nombre
        if contenido is not None:
            cliente.foto = contenido
        db.session.commit()
        return jsonify(ok=True)

    @bp.route('/clientes/perfil/foto')
    @require_customer
    def foto_perfil():
        cliente = actual()
        if not cliente.foto:
            return '', 404
        return send_file(BytesIO(cliente.foto), mimetype='image/jpeg', max_age=0)

    @bp.route('/clientes/perfil/foto/eliminar', methods=['POST'])
    @require_customer
    def quitar_foto():
        actual().foto = None
        db.session.commit()
        return jsonify(ok=True)

    @bp.route('/clientes/salir', methods=['POST'])
    @require_customer
    def salir():
        session.pop('cliente_id', None)
        session.pop('telefono_pendiente', None)
        session.pop('csrf', None)
        return redirect(url_for('clientes.acceso'))

    @bp.route('/admin/clientes')
    @admin_page
    def listado_admin():
        clientes = db.session.scalars(db.select(Cliente).order_by(Cliente.nombre)).all()
        return render_template('clientes_admin.html', clientes=clientes, seleccionado=None,
                               csrf=csrf_token(), puntos_visita=puntos_visita)

    @bp.route('/admin/clientes/<int:cliente_id>')
    @admin_page
    def detalle_admin(cliente_id):
        cliente = db.get_or_404(Cliente, cliente_id)
        historial = visitas(cliente.id)
        return render_template('clientes_admin.html', seleccionado=cliente, clientes=[],
                               visitas=historial, puntos=sum(v.puntos for v in historial if not v.anulada),
                               csrf=csrf_token(), puntos_visita=puntos_visita,
                               hoy=datetime.now(ZoneInfo('America/Mexico_City')).date())

    @bp.route('/admin/clientes/<int:cliente_id>/visitas', methods=['POST'])
    @admin_required
    def registrar_visita(cliente_id):
        db.get_or_404(Cliente, cliente_id)
        data = request.get_json(silent=True)
        try:
            if not isinstance(data, dict):
                raise ValueError('Completa los datos del servicio realizado.')
            fecha = datetime.strptime(data.get('fecha', ''), '%Y-%m-%d').date()
            if fecha > datetime.now(ZoneInfo('America/Mexico_City')).date():
                raise ValueError('Solo puedes registrar visitas que ya ocurrieron.')
            servicio = data.get('servicio', '')
            notas = data.get('notas', '')
            if not isinstance(servicio, str) or not 2 <= len(servicio.strip()) <= 200:
                raise ValueError('Describe el servicio, de 2 a 200 caracteres.')
            if not isinstance(notas, str) or len(notas) > 2000:
                raise ValueError('Las indicaciones pueden tener hasta 2000 caracteres.')
            importe = dinero_centavos(data.get('importe', '0'))
            proximo = data.get('proximo_retoque', '')
            proximo = datetime.strptime(proximo, '%Y-%m-%d').date() if proximo else None
            if proximo and proximo <= fecha:
                raise ValueError('El próximo retoque debe ser posterior a la visita.')
            if not isinstance(data.get('es_retoque', False), bool):
                raise ValueError('Indica si el servicio fue un retoque.')
        except (ValueError, TypeError) as error:
            return jsonify(error=str(error) or 'Revisa las fechas y los datos.'), 400
        visita = VisitaCliente(cliente_id=cliente_id, fecha=fecha, servicio=servicio.strip(),
                               notas=notas.strip(), importe=importe, puntos=puntos_visita,
                               es_retoque=data.get('es_retoque', False), proximo_retoque=proximo)
        db.session.add(visita)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify(error='Ya hay una visita registrada para esta clienta en esa fecha. No se duplicaron puntos.'), 409
        return jsonify(ok=True, puntos=puntos_visita), 201

    @bp.route('/admin/clientes/<int:cliente_id>/visitas/<int:visita_id>/anular', methods=['POST'])
    @admin_required
    def anular_visita(cliente_id, visita_id):
        visita = db.first_or_404(db.select(VisitaCliente).filter_by(id=visita_id, cliente_id=cliente_id))
        visita.anulada = True
        db.session.commit()
        return jsonify(ok=True)

    app.register_blueprint(bp)
