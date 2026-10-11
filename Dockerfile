# Receita do servidor (usada pelo Coolify na VPS). Monta o portal sempre do mesmo jeito.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    EM_CONTAINER=1 \
    PRIVATE_MEDIA_ROOT=/data/arquivos_privados

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
# CSS/JS/ícones prontos já na montagem (a chave aqui é só para este passo, não é usada no servidor).
RUN DEBUG=True SECRET_KEY=somente-para-montar-a-imagem python manage.py collectstatic --noinput \
    && chmod +x docker-entrypoint.sh

EXPOSE 8000
CMD ["./docker-entrypoint.sh"]
