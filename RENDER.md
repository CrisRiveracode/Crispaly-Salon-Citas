# Render

- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app --bind 0.0.0.0:$PORT`
- Administración: `/admin`.
- En Environment, configurar `ADMIN_PASSWORD` con una contraseña privada de al menos 12 caracteres. No guardarla en Git ni compartirla por chat. Guardar y desplegar los cambios. El panel queda cerrado si falta esta variable o es demasiado corta.
- Opcional: configurar `SECRET_KEY` con un secreto aleatorio para las sesiones. Si se omite, se deriva de la contraseña; cambiar la contraseña invalida las sesiones cuando no hay una clave explícita.
- El panel permite consultar las citas y los contactos, cancelar citas y bloquear/desbloquear horas o días completos. No se puede bloquear un horario con citas existentes sin cancelarlas primero.
- Los horarios usan la zona America/Mexico_City. Las nuevas tablas se crean al arrancar; no se elimina la tabla de citas.

## Conservar las citas

SQLite necesita un disco persistente. El sistema de archivos temporal de Render pierde cambios en reinicios o despliegues. Para conservar citas y bloqueos, adjuntar un disco persistente en `/var/data` y configurar `SALON_DATABASE_URI=sqlite:////var/data/citas.db` antes de recibir citas reales. Los discos persistentes requieren un plan compatible de Render. Si ya hay citas en la base local, respaldarlas y trasladarlas al disco antes de cambiar la ruta; cambiar esta variable no migra los datos.

## Pruebas

`python -m unittest discover -s tests -v`

## Cotizaciones personalizadas

- Formulario público: `/cotizar`. Revisión privada: `/admin/cotizaciones`.
- Usa WhatsApp normal: la clienta guarda la solicitud y pulsa «Enviar por WhatsApp». No se envía ningún mensaje automáticamente. El chat lleva un resumen y un enlace privado a la solicitud; las fotos no se adjuntan al mensaje. El salón inicia sesión para abrirlas.
- El número de destino usa el WhatsApp que ya estaba en la página: `523315370476`. Se puede cambiar con `SALON_WHATSAPP` (código de país y número, solo dígitos).
- `PUBLIC_BASE_URL` es opcional; por defecto es `https://crispaly-salon-citas.onrender.com`. Actualizarlo si se cambia el dominio para que los enlaces del mensaje sigan funcionando.
- Se requieren entre 1 y 3 fotos actuales; se pueden añadir hasta 3 fotos de referencia. Formatos JPEG, PNG y WebP, máximo 5 MB por archivo y 32 MB por solicitud. Las fotos se validan, se reducen a 1600 píxeles y se recodifican como JPEG sin metadatos EXIF. HEIC requiere conversión previa.
- Los datos y las fotos están en la misma base de datos, sin enlaces públicos a las imágenes. Las nuevas tablas se crean al arrancar sin modificar las tablas existentes. El disco persistente y sus respaldos deben tener capacidad para las fotos; sin persistencia, se pierden solicitudes y fotos en reinicios o despliegues.
- El panel permite filtrar por estado, preparar el importe y observaciones, abrir un mensaje dirigido a la clienta y eliminar solicitudes con sus fotos. «Respuesta preparada» no significa que WhatsApp haya entregado el mensaje.
- La solicitud no reserva una cita, no cobra y no confirma un precio. El número del cliente es declarado por ella; esta versión no verifica que sea su propietario.
