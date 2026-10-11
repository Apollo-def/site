#!/bin/sh
# Roda a cada vez que o servidor liga: atualiza o banco, confere a segurança e sobe o portal.
set -e
mkdir -p "$PRIVATE_MEDIA_ROOT"
python manage.py migrate --noinput
python manage.py verificar_publicacao
exec gunicorn portal.wsgi --bind 0.0.0.0:8000 --workers 2 --threads 8 --timeout 300 --access-logfile -
