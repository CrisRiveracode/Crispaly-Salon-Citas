from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import text
import json
import os
import secrets
import hashlib
import hmac
from functools import wraps
from datetime import timedelta
from collections import deque
from time import monotonic
from threading import Lock

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('SALON_DATABASE_URI', 'sqlite:///citas.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

admin_password = os.environ.get('ADMIN_PASSWORD', '')
app.secret_key = os.environ.get('SECRET_KEY') or (
    hashlib.sha256(('salon-admin:' + admin_password).encode()).digest()
    if admin_password else secrets.token_bytes(32)
)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('RENDER', '').lower() == 'true',
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)
login_failures = deque()
login_lock = Lock()


def csrf_token():
    if 'csrf' not in session:
        session['csrf'] = secrets.token_hex(32)
    return session['csrf']


def valid_csrf():
    supplied = request.headers.get('X-CSRF-Token') or request.form.get('csrf', '')
    return bool(session.get('csrf')) and hmac.compare_digest(
        session['csrf'].encode(), supplied.encode()
    )


def admin_required(view):
    @wraps(view)
    def protected(*args, **kwargs):
        if len(admin_password) < 12 or not session.get('admin'):
            return jsonify(error="Inicia sesión en el panel de administración"), 401
        if request.method != 'GET' and not valid_csrf():
            return jsonify(error="Sesión caducada. Recarga el panel e intenta de nuevo."), 400
        return view(*args, **kwargs)
    return protected


@app.after_request
def private_responses(response):
    if request.path.startswith('/admin') or request.path == '/citas':
        response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    return response


db = SQLAlchemy(app)

class Cita(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100))
    servicios = db.Column(db.Text)  # JSON string (lista)
    fecha = db.Column(db.String(20))
    hora = db.Column(db.String(10))
    contacto = db.Column(db.String(100))
    tipo_confirmacion = db.Column(db.String(20))
    estado = db.Column(db.String(20), default='pendiente')

class Bloqueo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.String(10), nullable=False)
    hora = db.Column(db.String(5), nullable=False)  # * = día completo
    __table_args__ = (db.UniqueConstraint('fecha', 'hora'),)


with app.app_context():
    db.create_all()

@app.route('/')
def inicio():
    return render_template('index.html')

# Obtener citas ordenadas
@app.route('/citas', methods=['GET'])
@admin_required
def ver_citas():
    citas = Cita.query.order_by(Cita.fecha, Cita.hora).all()
    return jsonify([{
        "id": c.id,
        "nombre": c.nombre,
        "servicios": json.loads(c.servicios),
        "fecha": c.fecha,
        "hora": c.hora,
        "contacto": c.contacto,
        "estado": c.estado
    } for c in citas])

# Crear cita y reservar el horario de forma atómica en SQLite.
@app.route('/crear_cita', methods=['POST'])
def crear_cita():
    data = request.get_json(silent=True)
    campos = ('nombre', 'fecha', 'hora', 'contacto')
    if not isinstance(data, dict) or any(
        not isinstance(data.get(c), str) or not data[c].strip() for c in campos
    ):
        return jsonify(error="Completa nombre, contacto, fecha y hora"), 400
    servicios = data.get('servicios')
    if not isinstance(servicios, list) or not servicios or any(
        s not in ('Corte', 'Tinte', 'Balayage', 'Depilación') for s in servicios
    ):
        return jsonify(error="Selecciona un servicio válido"), 400
    if len(data['nombre']) > 100 or len(data['contacto']) > 100:
        return jsonify(error="Nombre o contacto demasiado largo"), 400
    try:
        momento = datetime.strptime(data['fecha'] + ' ' + data['hora'], '%Y-%m-%d %H:%M')
        ahora = datetime.now(ZoneInfo('America/Mexico_City')).replace(tzinfo=None)
        cierre = 15 if momento.weekday() == 5 else 19
        if (momento.strftime('%Y-%m-%d %H:%M') != data['fecha'] + ' ' + data['hora']
                or momento <= ahora or momento.weekday() == 6
                or momento.minute != 0 or not 10 <= momento.hour < cierre):
            raise ValueError()
    except ValueError:
        return jsonify(error="Elige una fecha futura y un horario disponible del salón"), 400

    try:
        db.session.execute(text('BEGIN IMMEDIATE'))
        existe = Cita.query.filter_by(fecha=data['fecha'], hora=data['hora']).first()
        bloqueado = Bloqueo.query.filter(
            Bloqueo.fecha == data['fecha'], Bloqueo.hora.in_(['*', data['hora']])
        ).first()
        if existe or bloqueado:
            db.session.rollback()
            return jsonify(error="Este horario ya está ocupado. Elige otro."), 409
        nueva = Cita(
            nombre=data['nombre'].strip(), servicios=json.dumps(servicios),
            fecha=data['fecha'], hora=data['hora'], contacto=data['contacto'].strip(),
            tipo_confirmacion='whatsapp', estado='confirmada'
        )
        db.session.add(nueva)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return jsonify(ok=True, id=nueva.id), 201

# Eliminar cita
@app.route('/eliminar/<int:id>', methods=['DELETE'])
@admin_required
def eliminar(id):
    cita = db.session.get(Cita, id)

    if not cita:
        return jsonify({"error": "No existe"}), 404

    db.session.delete(cita)
    db.session.commit()

    return jsonify({"mensaje": "Eliminada"})

# Horarios ocupados por fecha
@app.route('/horarios_ocupados/<fecha>', methods=['GET'])
def horarios_ocupados(fecha):
    citas = Cita.query.filter_by(fecha=fecha).all()
    horas = [c.hora for c in citas]
    bloqueos = Bloqueo.query.filter_by(fecha=fecha).all()
    if any(b.hora == '*' for b in bloqueos):
        return jsonify([f'{h:02}:00' for h in range(10, 19)])
    return jsonify(sorted(set(horas + [b.hora for b in bloqueos])))

@app.route('/admin')
def admin():
    if len(admin_password) < 12:
        return render_template('admin.html', configurado=False), 503
    return render_template('admin.html', configurado=True,
                           autenticado=bool(session.get('admin')), csrf=csrf_token())


@app.route('/admin/login', methods=['POST'])
def admin_login():
    if len(admin_password) < 12:
        return redirect(url_for('admin'))
    if not valid_csrf():
        return render_template('admin.html', configurado=True, csrf=csrf_token(),
                               error="Recarga la página e intenta de nuevo."), 400
    with login_lock:
        now = monotonic()
        while login_failures and now - login_failures[0] > 300:
            login_failures.popleft()
        if len(login_failures) >= 10:
            return render_template('admin.html', configurado=True, csrf=csrf_token(),
                                   error="Demasiados intentos. Espera cinco minutos."), 429
        if not hmac.compare_digest(request.form.get('password', '').encode(),
                                   admin_password.encode()):
            login_failures.append(now)
            return render_template('admin.html', configurado=True, csrf=csrf_token(),
                                   error="Contraseña incorrecta."), 401
    session.clear()
    session.permanent = True
    session['admin'] = True
    csrf_token()
    return redirect(url_for('admin'))


@app.route('/admin/logout', methods=['POST'])
@admin_required
def admin_logout():
    session.clear()
    return redirect(url_for('admin'))


@app.route('/admin/bloqueos', methods=['GET', 'POST'])
@admin_required
def admin_bloqueos():
    if request.method == 'GET':
        return jsonify([dict(id=b.id, fecha=b.fecha, hora=b.hora)
                        for b in Bloqueo.query.order_by(Bloqueo.fecha, Bloqueo.hora).all()])
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Selecciona una fecha y un horario"), 400
    fecha, hora = data.get('fecha'), data.get('hora')
    try:
        dia = datetime.strptime(fecha, '%Y-%m-%d').date()
        hoy = datetime.now(ZoneInfo('America/Mexico_City')).date()
        cierre = 15 if dia.weekday() == 5 else 19
        horas = [f'{h:02}:00' for h in range(10, cierre)]
        if fecha != dia.isoformat() or dia < hoy or hora not in ['*'] + horas:
            raise ValueError()
    except (ValueError, TypeError):
        return jsonify(error="Selecciona una fecha válida y un horario del salón"), 400
    try:
        db.session.execute(text('BEGIN IMMEDIATE'))
        citas = Cita.query.filter_by(fecha=fecha)
        if hora != '*':
            citas = citas.filter_by(hora=hora)
        if citas.first():
            db.session.rollback()
            return jsonify(error="Ya hay citas en ese horario. Cancélalas antes de bloquearlo."), 409
        if Bloqueo.query.filter_by(fecha=fecha, hora=hora).first():
            db.session.rollback()
            return jsonify(error="Ese horario ya está bloqueado"), 409
        bloqueo = Bloqueo(fecha=fecha, hora=hora)
        db.session.add(bloqueo)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return jsonify(ok=True, id=bloqueo.id), 201


@app.route('/admin/bloqueos/<int:id>', methods=['DELETE'])
@admin_required
def quitar_bloqueo(id):
    bloqueo = db.session.get(Bloqueo, id)
    if not bloqueo:
        return jsonify(error="No existe el bloqueo"), 404
    db.session.delete(bloqueo)
    db.session.commit()
    return jsonify(ok=True)


if __name__ == '__main__':
    app.run(debug=True)