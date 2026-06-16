FROM node:24-slim AS ui-builder

WORKDIR /ui

COPY src/ui/package*.json ./
RUN npm ci

COPY src/ui ./
RUN npm run build

FROM python:3.13-slim

WORKDIR /app

ENV PYTHONPATH=/app

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates git openssh-client \
  && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY alembic.ini ./
COPY src/backend/sysml-backend/alembic ./src/backend/sysml-backend/alembic
COPY src/backend/sysml-backend/sysml_backend ./src/backend/sysml-backend/sysml_backend
COPY --from=ui-builder /ui/dist ./src/backend/sysml-backend/sysml_backend/static
RUN pip install --no-cache-dir .

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=2).read()" || exit 1

CMD ["python", "-m", "sysml_backend"]
