FROM python:3.12-slim-bookworm AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential pkg-config default-libmysqlclient-dev && rm -rf /var/lib/apt/lists/*
COPY requirements*.txt /deps/
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    pip wheel --wheel-dir /wheels -r /deps/requirements-mysql.txt

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends libmariadb3 ca-certificates && rm -rf /var/lib/apt/lists/* && groupadd --gid 10001 classroom && useradd --uid 10001 --gid classroom --no-create-home classroom
COPY --from=build /wheels /wheels
COPY requirements*.txt /deps/
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r /deps/requirements-mysql.txt && rm -rf /wheels
WORKDIR /app
COPY --chown=classroom:classroom . /app
RUN DEBUG=true python manage.py collectstatic --noinput
USER classroom
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", "--timeout", "30", "--max-requests", "1000", "--max-requests-jitter", "100", "--error-logfile", "-"]
