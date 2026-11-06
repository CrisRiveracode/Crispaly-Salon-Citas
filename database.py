"""Configuración PostgreSQL y exclusión mutua de operaciones críticas."""
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def database_url(environ):
    raw = environ.get('DATABASE_URL') or environ.get('SALON_DATABASE_URI')
    if not raw:
        if environ.get('RENDER', '').lower() == 'true':
            raise RuntimeError('Configura DATABASE_URL con la URL de PostgreSQL antes de iniciar el servicio. No se usará almacenamiento temporal para los clientes.')
        return 'sqlite:///citas.db'
    try:
        url = make_url(raw)
    except (ArgumentError, ValueError, TypeError):
        raise RuntimeError('La dirección de la base de datos no es válida. Revisa DATABASE_URL sin publicarla.') from None
    if url.drivername in ('postgres', 'postgresql', 'postgresql+psycopg', 'postgresql+psycopg2'):
        url = url.set(drivername='postgresql+psycopg')
        # Conexiones remotas cifradas. Conservar una configuración de confianza más estricta.
        if url.host:
            sslmode = url.query.get('sslmode')
            if sslmode not in (None, 'require', 'verify-ca', 'verify-full'):
                raise RuntimeError('PostgreSQL remoto requiere SSL. Usa sslmode=require o verificación más estricta.')
            if sslmode is None:
                url = url.update_query_dict({'sslmode':'require'})
        return url.render_as_string(hide_password=False)
    if url.drivername == 'sqlite':
        if environ.get('RENDER', '').lower() == 'true':
            raise RuntimeError('En Render configura DATABASE_URL con PostgreSQL. SQLite queda disponible solo para desarrollo y para exportar datos antiguos.')
        return raw
    raise RuntimeError('La aplicación admite PostgreSQL en producción y SQLite para desarrollo.')


def begin_write(db):
    """Serializar reservas/bloqueos/bonos sin depender de BEGIN IMMEDIATE en PostgreSQL.

    El candado transaccional de PostgreSQL se libera automáticamente al confirmar
    o revertir. Las restricciones únicas siguen protegiendo teléfonos y bonos.
    """
    dialect = db.engine.dialect.name
    if dialect == 'sqlite':
        db.session.execute(text('BEGIN IMMEDIATE'))
    elif dialect == 'postgresql':
        db.session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': 783154992})
    else:
        raise RuntimeError('Motor de base de datos no compatible.')
