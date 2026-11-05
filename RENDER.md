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
