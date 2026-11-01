# One image, three processes: api (with the built SPA), worker, and the MCP server.
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY src/ src/
COPY fixtures/ fixtures/
COPY --from=web /web/dist static/
RUN useradd --create-home --uid 10001 gatekeeper
USER gatekeeper
EXPOSE 8000 8808
CMD ["uvicorn", "gatekeeper.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
