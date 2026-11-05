from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import text
import json

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///citas.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

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

with app.app_context():
    db.create_all()

@app.route('/')
def inicio():
    return render_template('index.html')

# Obtener citas ordenadas
@app.route('/citas', methods=['GET'])
def ver_citas():
    citas = Cita.query.order_by(Cita.fecha, Cita.hora).all()
    return jsonify([{
        "id": c.id,
        "nombre": c.nombre,
        "servicios": json.loads(c.servicios),
        "fecha": c.fecha,
        "hora": c.hora
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
        if existe:
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
def eliminar(id):
    cita = Cita.query.get(id)

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
    return jsonify(horas)

if __name__ == '__main__':
    app.run(debug=True)