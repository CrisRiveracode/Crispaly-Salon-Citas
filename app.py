from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy

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
        "servicio": c.servicio,
        "fecha": c.fecha,
        "hora": c.hora
    } for c in citas])

# Crear cita (evita duplicados)
import json

@app.route('/crear_cita', methods=['POST'])
def crear_cita():
    data = request.json

    # Solo bloquear si ya está confirmada
    existe = Cita.query.filter_by(
        fecha=data['fecha'],
        hora=data['hora'],
        estado='confirmada'
    ).first()

    if existe:
        return jsonify({"error": "Horario ocupado"}), 400

    nueva = Cita(
        nombre=data['nombre'],
        servicios=json.dumps(data['servicios']),
        fecha=data['fecha'],
        hora=data['hora'],
        contacto=data['contacto'],
        tipo_confirmacion=data['tipo_confirmacion'],
        estado='pendiente'
    )

    db.session.add(nueva)
    db.session.commit()

    return jsonify({"ok": True})

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