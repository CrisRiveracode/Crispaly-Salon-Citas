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

## Portal de clientes y SMS

- Portal público: `/clientes`, registro/acceso: `/clientes/acceso`, perfil: `/clientes/perfil`.
- Panel del salón: `/admin/clientes`. Usa la misma contraseña administrativa existente.
- El teléfono es obligatorio, se normaliza al formato internacional E.164 y tiene una restricción única en la base de datos. Distintas formas de escribir el mismo número recuperan una sola cuenta. Verificar un teléfono no garantiza una sola cuenta por persona si tiene varios números.
- No se crea una cuenta hasta que Twilio Verify confirma el código. El acceso posterior también se hace por SMS. No existen códigos maestros ni formas públicas de omitir la verificación.

### Activar SMS reales con Twilio Verify

1. Crear una cuenta de Twilio y habilitar un servicio **Verify** para SMS. La cuenta y el servicio deben admitir el envío al país de los clientes (México inicialmente). Una cuenta de prueba puede limitar los destinatarios a números previamente autorizados. Twilio cobra según sus tarifas; no se presupone que los mensajes sean gratuitos.
2. En Render → Environment, configurar de forma privada `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` y `TWILIO_VERIFY_SERVICE_SID`. No compartir sus valores por chat ni subirlos a Git. Configurar también `SECRET_KEY` con un valor aleatorio fuerte, estable y privado para las sesiones; conservarlo entre despliegues.
3. Guardar y desplegar. El backend necesita HTTPS saliente a `verify.twilio.com`; no hace falta configurar un webhook para este flujo.
4. Revisar en Twilio los permisos geográficos y sus protecciones antifraude. La aplicación limita cada teléfono a un envío por minuto, tres por hora, cinco comprobaciones por código y una ventana de diez minutos. Hay un límite global inicial de **50 intentos de envío al día UTC**, también registrado en la base de datos. Los fallos de envío consumen cuota para evitar reintentos sin control.
5. Probar con un teléfono real: solicitar código, recibir SMS, confirmar y comprobar que volver a entrar con el mismo teléfono recupera la misma cuenta. Este paso no se pudo ejecutar durante el desarrollo porque no había cuenta ni credenciales de Twilio configuradas. Las pruebas automatizadas y del navegador usaron un proveedor simulado en servidores de prueba aislados; ese proveedor no está disponible en la aplicación desplegada.

Si faltan las credenciales, el registro muestra que está pendiente de activación. Reservas y cotizaciones siguen funcionando. No se guarda un usuario sin verificar el teléfono ni se presenta un envío simulado como real.

### Servicios, puntos y retoques

- La foto de perfil es opcional y privada. Se valida y recodifica igual que las fotos de cotizaciones, quitando metadatos; máximo 5 MB, JPG/PNG/WebP. Nombre editable; el cambio de teléfono no está habilitado sin una nueva verificación.
- Las citas programadas se asocian al número de teléfono verificado, incluyendo las reservas anteriores con ese mismo número. Si el cliente está conectado al reservar, el servidor usa su nombre y teléfono verificados.
- Solo el administrador registra los servicios **realizados**, con fecha, importe opcional, indicaciones visibles para el cliente, si fue retoque y fecha sugerida para el siguiente. Las fechas sugeridas no son reservas. El importe registrado no equivale a un pago ni una deuda.
- Se suman **10 puntos por visita realizada** por defecto. `PUNTOS_POR_VISITA` puede establecer un entero de 1 a 1000 para futuras visitas; las anteriores conservan los puntos asignados. Se registra una visita por cliente y día: hay que agrupar todos los servicios de esa visita. La restricción única impide duplicar puntos por doble envío. Todavía no hay canjes ni recompensas económicas.
- Anular un registro retira sus puntos, sin borrar el historial de la corrección. Repetir la anulación no resta más puntos. Una cita reservada por sí sola nunca genera puntos.
- Las ideas de temporada son una selección editorial por estación en México, no tendencias obtenidas de un servicio en tiempo real. Fotografías ilustrativas de Pexels (IDs 3993320, 3993449, 3992874), bajo su licencia de uso: https://www.pexels.com/license/. Se enlazan sus fuentes y no se presentan como trabajos propios del salón. El navegador carga las fotos desde `images.pexels.com`.
- Las tablas de clientes, verificaciones, envíos y visitas se crean al arrancar, sin alterar ni borrar las citas y cotizaciones existentes.

**Persistencia:** antes de registrar clientes reales, usar almacenamiento persistente y respaldos de la base de datos. Ahí se guardan teléfonos verificados, fotos, historial y puntos. El sistema de archivos temporal de Render no los conserva entre despliegues. El acceso por SMS demuestra control del número en ese momento; los números de teléfono pueden reciclarse y las cuentas inactivas deben revisarse antes de reutilizar su historial.
