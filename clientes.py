"""Portal de clientes con contraseña y verificación presencial opcional."""
from datetime import datetime, timedelta, timezone
from functools import wraps
from io import BytesIO
import json
import os
import re
import secrets

import phonenumbers
from flask import (Blueprint, jsonify, redirect, render_template, request,
                   send_file, session, url_for)
from database import begin_write
from sqlalchemy.exc import IntegrityError
from zoneinfo import ZoneInfo
from cotizaciones import dinero_centavos, foto_jpeg
from werkzeug.security import generate_password_hash, check_password_hash


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


def nombre_completo(value):
    if not isinstance(value, str):
        raise ValueError('Escribe tu nombre completo y apellidos.')
    value = ' '.join(value.split())
    if not 5 <= len(value) <= 100 or len(value.split()) < 2:
        raise ValueError('Escribe tu nombre completo y apellidos, de 5 a 100 caracteres.')
    return value


def password_valida(value):
    if not isinstance(value, str) or not 10 <= len(value) <= 128 or not value.strip():
        raise ValueError('Usa una contraseña de 10 a 128 caracteres.')
    return value


def register_clientes(app, db, Cita, admin_required, csrf_token, valid_csrf):
    bp = Blueprint('clientes', __name__)
    dummy_hash = generate_password_hash(secrets.token_urlsafe(32))
    puntos_visita = 10
    try:
        puntos_visita = max(1, min(1000, int(os.environ.get('PUNTOS_POR_VISITA', '10'))))
    except ValueError:
        pass

    class Cliente(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        nombre = db.Column(db.String(100), nullable=False)
        telefono = db.Column(db.String(16), unique=True, nullable=False, index=True)
        # Campo legado de la primera versión: no determina la verificación presencial.
        verificado = db.Column(db.DateTime, nullable=False, default=ahora_utc)
        creado = db.Column(db.DateTime, nullable=False, default=ahora_utc)
        foto = db.deferred(db.Column(db.LargeBinary))

    class CuentaCliente(db.Model):
        cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), primary_key=True)
        password_hash = db.Column(db.Text, nullable=False)
        version = db.Column(db.Integer, nullable=False, default=1)

    class VerificacionPresencial(db.Model):
        cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), primary_key=True)
        fecha = db.Column(db.DateTime, nullable=False, default=ahora_utc)
        puntos = db.Column(db.Integer, nullable=False, default=10)

    class ReservaCliente(db.Model):
        cita_id = db.Column(db.Integer, db.ForeignKey('cita.id'), primary_key=True)
        cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=False, index=True)

    class IntentoAccesoCliente(db.Model):
        telefono = db.Column(db.String(16), primary_key=True)
        ventana = db.Column(db.DateTime, nullable=False, default=ahora_utc)
        intentos = db.Column(db.Integer, nullable=False, default=0)

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
        if not isinstance(identity, int):
            return None
        cuenta = db.session.get(CuentaCliente, identity)
        if not cuenta or session.get('cliente_version') != cuenta.version:
            return None
        return db.session.get(Cliente, identity)

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

    def verificacion(cliente_id):
        return db.session.get(VerificacionPresencial, cliente_id)

    def puntos_totales(cliente_id, historial):
        bono = verificacion(cliente_id)
        return sum(v.puntos for v in historial if not v.anulada) + (bono.puntos if bono else 0)

    def vincular_cita(cita, cliente):
        db.session.add(ReservaCliente(cita_id=cita.id, cliente_id=cliente.id))

    def citas_cliente(cliente):
        # Un teléfono declarado sin verificar nunca da acceso automático a reservas anteriores.
        vinculadas = set(db.session.scalars(db.select(ReservaCliente.cita_id)
                         .filter_by(cliente_id=cliente.id)).all())
        presencial = bool(verificacion(cliente.id))
        resultado = []
        for cita in db.session.scalars(db.select(Cita).order_by(Cita.fecha, Cita.hora)).all():
            if cita.id in vinculadas:
                resultado.append(cita)
            elif presencial:
                try:
                    if telefono_normalizado(cita.contacto) == cliente.telefono:
                        resultado.append(cita)
                except ValueError:
                    pass
        return resultado

    def desvincular_cita(cita):
        ReservaCliente.query.filter_by(cita_id=cita.id).delete(synchronize_session=False)

    app.extensions['clientes'] = {'actual': actual, 'Cliente': Cliente,
                                  'VisitaCliente': VisitaCliente, 'CuentaCliente': CuentaCliente,
                                  'VerificacionPresencial': VerificacionPresencial,
                                  'ReservaCliente': ReservaCliente, 'vincular_cita': vincular_cita,
                                  'desvincular_cita': desvincular_cita}

    def iniciar_sesion(cliente, cuenta):
        session.pop('telefono_pendiente', None)
        session.pop('csrf', None)
        session['cliente_id'] = cliente.id
        session['cliente_version'] = cuenta.version
        session.permanent = True
        csrf_token()

    @bp.route('/clientes/acceso')
    def acceso():
        if actual():
            return redirect(url_for('clientes.portal'))
        return render_template('clientes_acceso.html', csrf=csrf_token())

    @bp.route('/clientes/registro', methods=['POST'])
    def registro():
        if not valid_csrf():
            return jsonify(error='Recarga la página e intenta de nuevo.'), 400
        data = request.get_json(silent=True)
        try:
            if not isinstance(data, dict):
                raise ValueError('Completa los datos del registro.')
            nombre = nombre_completo(data.get('nombre'))
            telefono = telefono_normalizado(data.get('telefono'))
            password = password_valida(data.get('password'))
            if data.get('password_confirmacion') != password:
                raise ValueError('Las contraseñas no coinciden.')
            if data.get('consentimiento') is not True:
                raise ValueError('Acepta el registro de tus datos para crear tu cuenta.')
        except ValueError as error:
            return jsonify(error=str(error)), 400
        password_hash = generate_password_hash(password)
        cliente = Cliente(nombre=nombre, telefono=telefono)
        db.session.add(cliente)
        try:
            db.session.flush()
            cuenta = CuentaCliente(cliente_id=cliente.id, password_hash=password_hash)
            db.session.add(cuenta)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify(error='Este teléfono ya tiene una cuenta. Inicia sesión o solicita ayuda en el salón.'), 409
        iniciar_sesion(cliente, cuenta)
        return jsonify(ok=True, destino='/clientes'), 201

    @bp.route('/clientes/entrar', methods=['POST'])
    def entrar():
        if not valid_csrf():
            return jsonify(error='Recarga la página e intenta de nuevo.'), 400
        data = request.get_json(silent=True)
        try:
            if not isinstance(data, dict):
                raise ValueError('Escribe tu teléfono y contraseña.')
            telefono = telefono_normalizado(data.get('telefono'))
            password = data.get('password', '')
            if not isinstance(password, str) or not 1 <= len(password) <= 128:
                raise ValueError('Escribe tu teléfono y contraseña.')
        except ValueError as error:
            return jsonify(error=str(error)), 400
        now = ahora_utc()
        begin_write(db)
        intento = db.session.get(IntentoAccesoCliente, telefono)
        if not intento:
            intento = IntentoAccesoCliente(telefono=telefono, ventana=now, intentos=0)
            db.session.add(intento)
        if intento.ventana < now - timedelta(minutes=15):
            intento.ventana, intento.intentos = now, 0
        if intento.intentos >= 5:
            db.session.rollback()
            return jsonify(error='Demasiados intentos. Espera 15 minutos o solicita ayuda en el salón.'), 429
        intento.intentos += 1
        db.session.commit()
        cliente = db.session.scalar(db.select(Cliente).filter_by(telefono=telefono))
        cuenta = db.session.get(CuentaCliente, cliente.id) if cliente else None
        correcto = check_password_hash(cuenta.password_hash if cuenta else dummy_hash, password)
        if not cuenta or not correcto:
            return jsonify(error='Teléfono o contraseña incorrectos. Si olvidaste tu acceso, solicita ayuda presencial en el salón.'), 401
        intento.intentos = 0
        db.session.commit()
        iniciar_sesion(cliente, cuenta)
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
                               visitas=historial, puntos=puntos_totales(cliente.id, historial),
                               presencial=verificacion(cliente.id),
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
            return render_template('clientes_perfil.html', cliente=cliente, csrf=csrf_token(), presencial=verificacion(cliente.id))
        try:
            nombre = nombre_completo(request.form.get('nombre'))
        except ValueError as error:
            return jsonify(error=str(error)), 400
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
        session.pop('cliente_version', None)
        session.pop('telefono_pendiente', None)
        session.pop('csrf', None)
        return redirect(url_for('clientes.acceso'))

    @bp.route('/admin/clientes')
    @admin_page
    def listado_admin():
        clientes = db.session.scalars(db.select(Cliente).order_by(Cliente.nombre)).all()
        verificadas = set(db.session.scalars(db.select(VerificacionPresencial.cliente_id)).all())
        return render_template('clientes_admin.html', clientes=clientes, seleccionado=None, verificadas=verificadas,
                               csrf=csrf_token(), puntos_visita=puntos_visita)

    @bp.route('/admin/clientes/<int:cliente_id>')
    @admin_page
    def detalle_admin(cliente_id):
        cliente = db.get_or_404(Cliente, cliente_id)
        historial = visitas(cliente.id)
        return render_template('clientes_admin.html', seleccionado=cliente, clientes=[],
                               visitas=historial, puntos=puntos_totales(cliente.id, historial),
                               presencial=verificacion(cliente.id), tiene_cuenta=bool(db.session.get(CuentaCliente, cliente.id)),
                               csrf=csrf_token(), puntos_visita=puntos_visita,
                               hoy=datetime.now(ZoneInfo('America/Mexico_City')).date())

    @bp.route('/admin/clientes/<int:cliente_id>/verificar-presencial', methods=['POST'])
    @admin_required
    def verificar_presencial(cliente_id):
        begin_write(db)
        db.get_or_404(Cliente, cliente_id)
        bono = verificacion(cliente_id)
        if bono:
            db.session.rollback()
            return jsonify(ok=True, puntos_otorgados=0)
        db.session.add(VerificacionPresencial(cliente_id=cliente_id, puntos=10))
        db.session.commit()
        return jsonify(ok=True, puntos_otorgados=10)

    @bp.route('/admin/clientes/<int:cliente_id>/restablecer-acceso', methods=['POST'])
    @admin_required
    def restablecer_acceso(cliente_id):
        begin_write(db)
        cliente = db.get_or_404(Cliente, cliente_id)
        if not verificacion(cliente_id):
            return jsonify(error='Primero confirma presencialmente la identidad de la clienta.'), 400
        data = request.get_json(silent=True)
        try:
            password = password_valida(data.get('password') if isinstance(data, dict) else None)
            if data.get('password_confirmacion') != password:
                raise ValueError('Las contraseñas no coinciden.')
        except ValueError as error:
            return jsonify(error=str(error)), 400
        intento = db.session.get(IntentoAccesoCliente, cliente.telefono)
        if intento:
            intento.intentos = 0
        cuenta = db.session.get(CuentaCliente, cliente_id)
        if cuenta:
            cuenta.password_hash = generate_password_hash(password)
            cuenta.version += 1
        else:
            db.session.add(CuentaCliente(cliente_id=cliente_id, password_hash=generate_password_hash(password)))
        db.session.commit()
        return jsonify(ok=True)

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
