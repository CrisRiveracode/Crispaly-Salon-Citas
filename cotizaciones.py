"""Solicitudes privadas de cotización y preparación de mensajes para WhatsApp."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
from io import BytesIO
import re
import secrets
from urllib.parse import urlencode, urlsplit
import warnings

from flask import (Blueprint, jsonify, redirect, render_template, request,
                   send_file, session, url_for)
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 25_000_000
ESTADOS = {
    'pendiente': 'Pendiente',
    'en_revision': 'En revisión',
    'respuesta_preparada': 'Respuesta preparada',
    'cerrada': 'Cerrada',
}


def dinero_centavos(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or not 0 <= amount <= 1_000_000:
            raise ValueError()
        cents = amount * 100
        if cents != cents.to_integral_value():
            raise ValueError()
        return int(cents)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError('Escribe un importe válido en pesos, con máximo dos decimales.')


def foto_jpeg(upload):
    raw = upload.read(MAX_IMAGE_BYTES + 1)
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError('Cada foto debe pesar como máximo 5 MB.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as image:
                if image.format not in ('JPEG', 'PNG', 'WEBP'):
                    raise ValueError('Usa fotos JPG, PNG o WebP. Convierte las fotos HEIC antes de subirlas.')
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError('La foto es demasiado grande. Reduce su resolución e intenta de nuevo.')
                image = ImageOps.exif_transpose(image)
                image.thumbnail((1600, 1600))
                output = BytesIO()
                image.convert('RGB').save(output, format='JPEG', quality=85)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        raise ValueError('No pudimos leer una foto. Usa una imagen JPG, PNG o WebP válida.')


def register_cotizaciones(app, db, admin_required, csrf_token, valid_csrf):
    bp = Blueprint('cotizaciones', __name__)
    app.config.setdefault('MAX_CONTENT_LENGTH', 32 * 1024 * 1024)
    # Flask define esta clave con None; la cota también cubre el formulario multipart.
    if app.config['MAX_CONTENT_LENGTH'] is None:
        app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024

    class Cotizacion(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        token = db.Column(db.String(64), unique=True, nullable=False,
                          default=lambda: secrets.token_urlsafe(24))
        creada = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
        nombre = db.Column(db.String(100), nullable=False)
        telefono = db.Column(db.String(15), nullable=False)
        cabello_actual = db.Column(db.Text, nullable=False)
        objetivo = db.Column(db.Text, nullable=False)
        historial = db.Column(db.Text, nullable=False, default='')
        referencia_url = db.Column(db.Text, nullable=False, default='')
        presupuesto = db.Column(db.Integer, nullable=False)  # Centavos MXN.
        estado = db.Column(db.String(30), nullable=False, default='pendiente')
        importe = db.Column(db.Integer)
        respuesta = db.Column(db.Text, nullable=False, default='')
        fotos = db.relationship('FotoCotizacion', cascade='all, delete-orphan',
                                order_by='FotoCotizacion.id', lazy='select')

    class FotoCotizacion(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        cotizacion_id = db.Column(db.Integer, db.ForeignKey('cotizacion.id'), nullable=False)
        tipo = db.Column(db.String(10), nullable=False)
        contenido = db.deferred(db.Column(db.LargeBinary, nullable=False))

    def whatsapp(telefono, mensaje):
        return 'https://wa.me/' + telefono + '?' + urlencode({'text': mensaje})

    def private_page(view):
        protected = admin_required(view)
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not session.get('admin'):
                return redirect(url_for('admin', next=request.path))
            return protected(*args, **kwargs)
        return wrapper

    def buscar(token):
        return db.first_or_404(db.select(Cotizacion).filter_by(token=token))

    def pesos(cents):
        return f'${cents / 100:,.2f} MXN'

    @bp.app_template_filter('pesos')
    def pesos_filter(cents):
        return pesos(cents)

    @bp.route('/cotizar')
    def formulario():
        return render_template('cotizar.html', csrf=csrf_token())

    @bp.route('/cotizaciones', methods=['POST'])
    def crear():
        if not valid_csrf():
            return jsonify(error='Recarga la página e intenta de nuevo.'), 400
        try:
            def campo(nombre, minimo=0, maximo=2000):
                valor = request.form.get(nombre, '').strip()
                if not minimo <= len(valor) <= maximo:
                    raise ValueError('Completa los campos y respeta la longitud indicada.')
                return valor
            nombre = campo('nombre', 2, 100)
            telefono_raw = campo('telefono', 10, 25)
            if not re.fullmatch(r'[+\d\s().-]+', telefono_raw):
                raise ValueError('Escribe un número de WhatsApp válido.')
            telefono = re.sub(r'\D', '', telefono_raw)
            if len(telefono) == 10:
                telefono = '52' + telefono
            if not 10 <= len(telefono) <= 15 or telefono.startswith('0'):
                raise ValueError('Escribe 10 dígitos para México o incluye tu código de país.')
            actual = campo('cabello_actual', 10)
            objetivo = campo('objetivo', 10)
            historial = campo('historial')
            referencia_url = campo('referencia_url', maximo=1000)
            if referencia_url:
                parsed = urlsplit(referencia_url)
                if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError('El enlace de referencia debe empezar con https://.')
            presupuesto = dinero_centavos(request.form.get('presupuesto'))
            if request.form.get('consentimiento') != 'si':
                raise ValueError('Confirma tu autorización para revisar las fotos y contactarte.')
            actuales = [f for f in request.files.getlist('fotos_actuales') if f.filename]
            referencias = [f for f in request.files.getlist('fotos_referencia') if f.filename]
            if not 1 <= len(actuales) <= 3 or len(referencias) > 3:
                raise ValueError('Sube de 1 a 3 fotos de tu cabello y hasta 3 fotos de referencia.')
            # Validar todas las imágenes antes de guardar la solicitud.
            fotos = [(tipo, foto_jpeg(f)) for tipo, archivos in
                     [('actual', actuales), ('referencia', referencias)] for f in archivos]
        except ValueError as error:
            return jsonify(error=str(error)), 400

        cotizacion = Cotizacion(nombre=nombre, telefono=telefono, cabello_actual=actual,
                                objetivo=objetivo, historial=historial, referencia_url=referencia_url,
                                presupuesto=presupuesto)
        cotizacion.fotos = [FotoCotizacion(tipo=tipo, contenido=contenido)
                            for tipo, contenido in fotos]
        db.session.add(cotizacion)
        db.session.commit()
        private_url = app.config['PUBLIC_BASE_URL'].rstrip('/') + url_for('cotizaciones.detalle', token=cotizacion.token)
        def resumen(valor, limite):
            return valor if len(valor) <= limite else valor[:limite - 1] + '…'
        mensaje = (f'Hola, quiero una cotización personalizada en Crispaly Salon.\n'
                   f'Solicitud #{cotizacion.id}\nNombre: {nombre}\nWhatsApp: +{telefono}\n'
                   f'Mi cabello (resumen): {resumen(actual, 150)}\nQuiero realizarme: {resumen(objetivo, 250)}\n'
                   f'Historial de tintes y tratamientos: {resumen(historial, 150) or "Sin especificar"}\n'
                   f'Presupuesto aproximado: {pesos(presupuesto)}\n'
                   f'Fotos, referencias y descripción completa (acceso privado del salón): {private_url}\n'
                   'Entiendo que el precio final depende de la valoración de mi cabello.')
        return jsonify(ok=True, folio=cotizacion.id,
                       whatsapp_url=whatsapp(app.config['SALON_WHATSAPP'], mensaje)), 201

    @bp.route('/admin/cotizaciones')
    @private_page
    def listado():
        estado = request.args.get('estado', '')
        consulta = db.select(Cotizacion)
        if estado in ESTADOS:
            consulta = consulta.filter_by(estado=estado)
        cotizaciones = db.session.scalars(consulta.order_by(Cotizacion.id.desc())).all()
        return render_template('cotizaciones_admin.html', cotizaciones=cotizaciones,
                               estados=ESTADOS, seleccionado=estado, csrf=csrf_token())

    @bp.route('/admin/cotizaciones/<token>')
    @private_page
    def detalle(token):
        return render_template('cotizacion_detalle.html', c=buscar(token), estados=ESTADOS,
                               csrf=csrf_token())

    @bp.route('/admin/cotizaciones/<token>/fotos/<int:foto_id>')
    @admin_required
    def foto(token, foto_id):
        cotizacion = buscar(token)
        imagen = db.first_or_404(db.select(FotoCotizacion).filter_by(
            id=foto_id, cotizacion_id=cotizacion.id))
        return send_file(BytesIO(imagen.contenido), mimetype='image/jpeg',
                         download_name='cabello.jpg', max_age=0)

    @bp.route('/admin/cotizaciones/<token>/estado', methods=['POST'])
    @admin_required
    def estado(token):
        cotizacion = buscar(token)
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or data.get('estado') not in ESTADOS:
            return jsonify(error='Selecciona un estado válido.'), 400
        cotizacion.estado = data['estado']
        db.session.commit()
        return jsonify(ok=True)

    @bp.route('/admin/cotizaciones/<token>/respuesta', methods=['POST'])
    @admin_required
    def responder(token):
        cotizacion = buscar(token)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error='Escribe el importe y las observaciones.'), 400
        try:
            importe = dinero_centavos(data.get('importe'))
            observaciones = data.get('observaciones', '')
            if importe <= 0 or not isinstance(observaciones, str) or not 10 <= len(observaciones.strip()) <= 2000:
                raise ValueError('Escribe un importe mayor a cero y observaciones de 10 a 2000 caracteres.')
        except ValueError as error:
            return jsonify(error=str(error)), 400
        cotizacion.importe = importe
        cotizacion.respuesta = observaciones.strip()
        cotizacion.estado = 'respuesta_preparada'
        db.session.commit()
        mensaje = (f'Hola, {cotizacion.nombre}. Soy de Crispaly Salon.\n'
                   f'Revisé tu solicitud #{cotizacion.id}.\n'
                   f'Cotización estimada: {pesos(importe)}\n{cotizacion.respuesta}\n'
                   'El precio final se confirma al valorar tu cabello en el salón. '
                   'Esta cotización no reserva una cita.\n'
                   f'Agenda aquí: {app.config["PUBLIC_BASE_URL"].rstrip("/")}/')
        return jsonify(ok=True, whatsapp_url=whatsapp(cotizacion.telefono, mensaje))

    @bp.route('/admin/cotizaciones/<token>', methods=['DELETE'])
    @admin_required
    def eliminar(token):
        db.session.delete(buscar(token))
        db.session.commit()
        return jsonify(ok=True)

    @app.errorhandler(413)
    def demasiado_grande(error):
        return jsonify(error='Las fotos superan el límite de carga. Usa hasta 6 fotos de máximo 5 MB cada una.'), 413

    app.register_blueprint(bp)
