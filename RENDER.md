# Crispaly Salon · Render + Neon

- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app --bind 0.0.0.0:$PORT`
- Administración: `/admin`.
- En Environment, configurar `ADMIN_PASSWORD` con una contraseña privada de al menos 12 caracteres. No guardarla en Git ni compartirla por chat. Guardar y desplegar los cambios. El panel queda cerrado si falta esta variable o es demasiado corta.
- Opcional: configurar `SECRET_KEY` con un secreto aleatorio para las sesiones. Si se omite, se deriva de la contraseña; cambiar la contraseña invalida las sesiones cuando no hay una clave explícita.
- El panel permite consultar las citas y los contactos, cancelar citas y bloquear/desbloquear horas o días completos. No se puede bloquear un horario con citas existentes sin cancelarlas primero.
- Los horarios usan la zona America/Mexico_City. Las nuevas tablas se crean al arrancar; no se elimina la tabla de citas.

## PostgreSQL en Neon (producción)

La aplicación Flask conserva SQLAlchemy y psycopg 3. No necesita un controlador JavaScript ni migrar el backend a Node. Acepta `postgres://` / `postgresql://` y exige SSL para conexiones remotas. Las URL contienen credenciales: no imprimirlas, subirlas a Git ni compartirlas por chat.

### Conexión desde la consola

1. En Neon, abrir el proyecto `flat-wildflower-41039415` de la organización `org-still-leaf-75693054`, rama `production`, y pulsar **Connect**.
2. Copiar la conexión agrupada (Connection pooling) y guardarla como `DATABASE_URL` en el servicio web de Render → Environment. Si el panel presenta un comando `psql`, copiar únicamente su URL, sin comando ni comillas, conservando los parámetros SSL.
3. Copiar la conexión directa, sin agrupación, y guardarla como `DATABASE_URL_UNPOOLED`. Ambas deben apuntar a la misma rama y base. Se usa la agrupada para consultas y la directa para preparar el esquema y migraciones. El programa rechaza combinar conexiones de distintas ramas o bases.
4. Conservar `ADMIN_PASSWORD` y una `SECRET_KEY` estable. Retirar una variable `SALON_DATABASE_URI` antigua de SQLite. Guardar y desplegar el servicio web en Render.
5. Registrar una cuenta de prueba, crear una cita y comprobarla; reiniciar el servicio web y confirmar que el perfil sigue disponible. Revisar los límites de almacenamiento y restauración del plan Neon. No se presupone almacenamiento ilimitado ni respaldo permanente gratuito.

### Flujo de la CLI Neon

```sh
npm i -g neon@latest
neon login
neon init -y --org-id org-still-leaf-75693054 --project-id flat-wildflower-41039415 --branch production --services postgres
neon deploy
```

`neon init` sin autenticación puede escribir `.neon` y `neon.ts` sin verificar el proyecto. Eso no demuestra conexión. La configuración generada conserva solo PostgreSQL y no reemplaza el sistema de acceso de clientes. `neon deploy` debe completar correctamente y descargar variables a `.env.local` (o `.env`). Nunca copiar el contenido de esos archivos a logs o Git.

La aplicación carga `.env.local` y `.env`, manteniendo prioridad de las variables del proceso sobre los archivos y preservando los valores literales. En Render, configurar las mismas variables mediante su panel; los archivos locales no se publican.

Preparar dependencias locales y comprobar:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
npm ci
npm run build
npm start
```

`npm run build` verifica los tipos de `neon.ts`, compila los módulos Python y ejecuta la suite. `npm start` arranca Gunicorn en `PORT` (5000 por defecto). El build de Render puede seguir siendo `pip install -r requirements.txt`, con el comando Gunicorn del inicio; Node solo es necesario para la CLI y sus comprobaciones locales.

Para reanudar después de un fallo, primero corregir el acceso a `console.neon.tech` y `oauth2.neon.tech` y completar `neon login` en el mismo entorno. Ejecutar después `neon deploy`. La API y el login fallaron durante esta configuración por HTTP 403 del proxy, y no se descargaron variables ni se validó la base remota. Instalar o escribir configuración sin esos pasos no equivale a provisionar ni conectar la base.

En Render, la aplicación no arranca sin PostgreSQL configurado; no crea silenciosamente otra base SQLite temporal. Se revisan las conexiones del pool antes de usarlas, se configura tiempo máximo de conexión y se desactivan sentencias preparadas persistentes para conexiones agrupadas. Los bloqueos transaccionales de PostgreSQL mantienen la protección de reservas y bonos duplicados.

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
