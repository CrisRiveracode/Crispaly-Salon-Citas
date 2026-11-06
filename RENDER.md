# Render

- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app --bind 0.0.0.0:$PORT`
- Administración: `/admin`.
- En Environment, configurar `ADMIN_PASSWORD` con una contraseña privada de al menos 12 caracteres. No guardarla en Git ni compartirla por chat. Guardar y desplegar los cambios. El panel queda cerrado si falta esta variable o es demasiado corta.
- Opcional: configurar `SECRET_KEY` con un secreto aleatorio para las sesiones. Si se omite, se deriva de la contraseña; cambiar la contraseña invalida las sesiones cuando no hay una clave explícita.
- El panel permite consultar las citas y los contactos, cancelar citas y bloquear/desbloquear horas o días completos. No se puede bloquear un horario con citas existentes sin cancelarlas primero.
- Los horarios usan la zona America/Mexico_City. Las nuevas tablas se crean al arrancar; no se elimina la tabla de citas.

## PostgreSQL en Render (producción)

La aplicación usa `DATABASE_URL` como primera opción. Acepta las URL `postgres://` y `postgresql://` que entrega Render y usa el controlador psycopg 3. Para conexiones remotas exige cifrado SSL y conserva modos más estrictos como `verify-full` si ya están configurados. Las conexiones y sus parámetros no deben publicarse.

1. Crear **New → PostgreSQL**, en la misma región del servicio web. Revisar el precio, si tiene fecha de vencimiento, persistencia y respaldos del plan elegido. No se presupone que un plan gratuito dure indefinidamente o tenga respaldos automáticos.
2. Copiar **Internal Database URL**. En el servicio web → **Environment**, guardar una variable **DATABASE_URL** con ese valor privado. No ponerla en Git, comandos compartidos ni capturas.
3. Conservar `ADMIN_PASSWORD` y una `SECRET_KEY` privada estable. Si existía `SALON_DATABASE_URI` de SQLite, eliminarla para no dejar una configuración obsoleta; `DATABASE_URL` tiene prioridad.
4. Usar el comando de compilación y arranque del inicio de este documento y desplegar el commit que incorpora PostgreSQL. Las tablas nuevas se crean automáticamente al arrancar, sin borrar datos existentes.
5. En una base inicialmente vacía, registrar una cuenta de prueba, iniciar sesión, crear una cita y comprobarla. Reiniciar el servicio web y volver a iniciar sesión para confirmar la persistencia. Verificar también el plan de respaldos y realizar una restauración de prueba antes de depender de datos reales.

En Render, la aplicación **no arranca si falta DATABASE_URL o se intenta usar SQLite**. Esto evita que un despliegue cree silenciosamente otro archivo temporal vacío. Un despliegue fallido no demuestra que PostgreSQL esté conectado: hay que corregir la variable y comprobar el servicio.

El proyecto mantiene SQLite únicamente para desarrollo local y exportación. `pool_pre_ping` verifica conexiones del pool y se configura un tiempo máximo para establecer la conexión. Los bloqueos transaccionales de PostgreSQL evitan reservas simultáneas incompatibles y bonos duplicados, manteniendo también las restricciones únicas.

### Copiar datos SQLite existentes (solo si se quieren conservar)

El usuario autorizó comenzar sin los registros del SQLite temporal actual. Por ello no se ha ejecutado una migración de producción. El archivo SQLite de esta máquina de desarrollo está vacío y **no es un respaldo de Render**.

Para una futura migración con datos:

1. Antes de desplegar, detener cambios de datos y conservar el archivo SQLite del servicio actual mediante Shell/SSH o el mecanismo que permita su plan de Render. No reiniciar ni desplegar primero: el archivo temporal podría desaparecer. Conservar el archivo fuera del sistema temporal. Si no hay acceso al archivo ni respaldos, no se puede afirmar que los registros sean recuperables.
2. Preparar un PostgreSQL vacío. Para ejecutar desde una computadora externa a Render, usar su URL externa con SSL y las reglas de acceso necesarias; la URL interna solo funciona dentro de la red privada de Render. Mantener `DATABASE_URL` en un entorno privado, no en argumentos de comandos.
3. Con las dependencias del proyecto instaladas, ejecutar:

   ```sh
   python tools/migrate_sqlite.py --source /ruta/segura/citas.db --backup /ruta/segura/respaldo-nuevo.sqlite3
   ```

4. El script conserva el origen, crea un respaldo SQLite consistente de permisos privados, valida su integridad y copia perfiles, hashes de contraseñas, fotos, citas, cotizaciones, puntos y asociaciones en una transacción PostgreSQL. Revisa los recuentos y reajusta las secuencias para que los nuevos registros no colisionen con identificadores importados. Rechaza un destino con registros o un origen vacío; no sobrescribe ni mezcla datos. Las tablas o columnas desconocidas con información requieren revisión antes de copiar.
5. Revisar las cuentas y registros importados antes de activar la aplicación. Activar respaldos y acordar cuánto tiempo conservar copias privadas. Nunca subir los archivos de datos a GitHub.

## Pruebas

`python -m unittest discover -s tests -v`

Sin PostgreSQL de pruebas se ejecuta la suite local con SQLite y las dos pruebas de migración se omiten expresamente. Con `TEST_DATABASE_URL` apuntando a un PostgreSQL de desarrollo aislado se ejecuta la suite completa y se crea/elimina un esquema aleatorio por prueba. No usar una base de producción para estas pruebas. Las URL no se aceptan como argumentos públicos del comando.

## Cotizaciones personalizadas

- Formulario público: `/cotizar`. Revisión privada: `/admin/cotizaciones`.
- Usa WhatsApp normal: la clienta guarda la solicitud y pulsa «Enviar por WhatsApp». No se envía ningún mensaje automáticamente. El chat lleva un resumen y un enlace privado a la solicitud; las fotos no se adjuntan al mensaje. El salón inicia sesión para abrirlas.
- El número de destino usa el WhatsApp que ya estaba en la página: `523315370476`. Se puede cambiar con `SALON_WHATSAPP` (código de país y número, solo dígitos).
- `PUBLIC_BASE_URL` es opcional; por defecto es `https://crispaly-salon-citas.onrender.com`. Actualizarlo si se cambia el dominio para que los enlaces del mensaje sigan funcionando.
- Se requieren entre 1 y 3 fotos actuales; se pueden añadir hasta 3 fotos de referencia. Formatos JPEG, PNG y WebP, máximo 5 MB por archivo y 32 MB por solicitud. Las fotos se validan, se reducen a 1600 píxeles y se recodifican como JPEG sin metadatos EXIF. HEIC requiere conversión previa.
- Los datos y las fotos están en la misma base de datos, sin enlaces públicos a las imágenes. Las nuevas tablas se crean al arrancar sin modificar las tablas existentes. PostgreSQL y sus respaldos deben tener capacidad para las fotos. No se guardan en el disco temporal del servicio web.
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
- Existen tablas para contraseñas, confirmaciones presenciales, asociaciones de reservas e intentos de acceso. No se modifican ni eliminan las tablas existentes. El campo legado `cliente.verificado` no determina el estado presencial; las nuevas cuentas no se presentan como verificadas por ese campo.

### Servicios, puntos, citas y retoques

- Foto de perfil opcional y privada: hasta 5 MB, JPG/PNG/WebP, recodificada sin metadatos EXIF. El nombre completo es editable. Cambiar el teléfono requiere atención del salón y no está habilitado desde el perfil.
- Las reservas realizadas mientras el cliente tiene sesión iniciada quedan asociadas a su cuenta. El servidor toma su nombre y teléfono del perfil, sin permitir sustituirlos con datos enviados desde el navegador.
- No se muestran reservas anteriores solo porque alguien escribió el teléfono al registrarse. Tras la confirmación presencial, el portal también puede mostrar las reservas con ese mismo teléfono. Cancelar una cita elimina su asociación a la cuenta para evitar errores si SQLite reutiliza identificadores.
- Solo el administrador registra servicios **realizados**: fecha, importe opcional, indicaciones visibles para el cliente, si fue retoque y fecha sugerida del siguiente. Las fechas sugeridas no crean reservas. El importe no equivale a un pago ni una deuda.
- Se suman **10 puntos por visita realizada** por defecto, además del bono presencial. `PUNTOS_POR_VISITA` permite un entero entre 1 y 1000 para visitas futuras; las anteriores conservan sus puntos. Se reconoce una visita por cliente y día, agrupando todos sus servicios. No hay canjes todavía.
- Anular una visita retira sus puntos, conserva el historial y no afecta el bono presencial. Una cita programada por sí sola no genera puntos.
- Ideas por estación en México: selección editorial, no tendencias de un servicio en tiempo real. Fotos ilustrativas de Pexels (IDs 3993320, 3993449, 3992874), bajo https://www.pexels.com/license/. Se enlazan sus fuentes y no se presentan como trabajos propios. El navegador las carga desde `images.pexels.com`.

**Persistencia:** en producción se usa PostgreSQL independiente del servicio web. Configurar y verificar los respaldos del plan elegido: ahí se conservan cuentas, hashes, fotos, historial y puntos. Cambiar de base no recupera archivos temporales que ya se hayan perdido.
