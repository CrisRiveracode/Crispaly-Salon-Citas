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

## Portal de clientes sin SMS

- Portal público: `/clientes`, registro/acceso: `/clientes/acceso`, perfil: `/clientes/perfil`.
- Panel del salón: `/admin/clientes`. Usa la contraseña administrativa existente.
- El registro requiere nombre completo y apellidos, teléfono único y contraseña de 10 a 128 caracteres con confirmación. No requiere Twilio ni envía SMS; no hay cargos de verificación. Las credenciales anteriores de Twilio no se usan en esta versión.
- Los teléfonos se normalizan a E.164: `3312345678`, `+52 33 1234 5678` y `523312345678` identifican la misma cuenta. La restricción única en la base de datos impide duplicados incluso con solicitudes simultáneas. No se demuestra la propiedad del número al registrar la cuenta.
- Las contraseñas se guardan como hashes scrypt; nunca en texto plano. El ingreso admite cinco intentos por teléfono cada quince minutos. Establecer `SECRET_KEY` con un secreto aleatorio estable en Render y conservarlo entre despliegues.

### Verificación presencial y bono

- El salón entra en `/admin/clientes`, abre el perfil y confirma presencialmente que nombre y teléfono corresponden a la clienta.
- «Confirmar identidad presencial y dar 10 puntos extra» otorga **10 puntos adicionales una sola vez**. La verificación tiene una fila única por cliente y se confirma en una transacción: repetir el clic o enviar solicitudes simultáneas no repite el bono.
- El cliente puede usar su cuenta antes de verificarla. El portal muestra si la verificación está pendiente y desglosa el bono una vez concedido.
- Para recuperar una cuenta, primero se confirma la identidad presencialmente y después se establece una contraseña nueva en el panel. La nueva contraseña invalida las sesiones anteriores. Nunca se recupera acceso solo por escribir un número.
- Las cuentas de la versión anterior se conservan. Si aún no tienen contraseña, no se pueden reclamar con un nuevo registro: el salón debe confirmar presencialmente al titular y establecer su contraseña. Las antiguas sesiones SMS deben volver a iniciar sesión.
- Se añaden tablas para contraseñas, confirmaciones presenciales, asociaciones de reservas e intentos de acceso. No se modifican ni eliminan las tablas existentes. El campo legado `cliente.verificado` no determina el estado presencial; las nuevas cuentas no se presentan como verificadas por ese campo.

### Servicios, puntos, citas y retoques

- Foto de perfil opcional y privada: hasta 5 MB, JPG/PNG/WebP, recodificada sin metadatos EXIF. El nombre completo es editable. Cambiar el teléfono requiere atención del salón y no está habilitado desde el perfil.
- Las reservas realizadas mientras el cliente tiene sesión iniciada quedan asociadas a su cuenta. El servidor toma su nombre y teléfono del perfil, sin permitir sustituirlos con datos enviados desde el navegador.
- No se muestran reservas anteriores solo porque alguien escribió el teléfono al registrarse. Tras la confirmación presencial, el portal también puede mostrar las reservas con ese mismo teléfono. Cancelar una cita elimina su asociación a la cuenta para evitar errores si SQLite reutiliza identificadores.
- Solo el administrador registra servicios **realizados**: fecha, importe opcional, indicaciones visibles para el cliente, si fue retoque y fecha sugerida del siguiente. Las fechas sugeridas no crean reservas. El importe no equivale a un pago ni una deuda.
- Se suman **10 puntos por visita realizada** por defecto, además del bono presencial. `PUNTOS_POR_VISITA` permite un entero entre 1 y 1000 para visitas futuras; las anteriores conservan sus puntos. Se reconoce una visita por cliente y día, agrupando todos sus servicios. No hay canjes todavía.
- Anular una visita retira sus puntos, conserva el historial y no afecta el bono presencial. Una cita programada por sí sola no genera puntos.
- Ideas por estación en México: selección editorial, no tendencias de un servicio en tiempo real. Fotos ilustrativas de Pexels (IDs 3993320, 3993449, 3992874), bajo https://www.pexels.com/license/. Se enlazan sus fuentes y no se presentan como trabajos propios. El navegador las carga desde `images.pexels.com`.

**Persistencia:** usar almacenamiento persistente y respaldos antes de registrar clientes reales. Ahí se conservan cuentas, hashes, fotos, historial y puntos. El sistema de archivos temporal de Render no conserva esos datos entre despliegues.
