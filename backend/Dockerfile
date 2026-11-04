FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-production.txt ./
RUN --mount=type=cache,target=/root/.cache/pip pip install --timeout=120 --retries=10 -r requirements-production.txt
COPY alembic.ini ./
COPY app ./app
RUN useradd --create-home --uid 10001 dealguard && chown -R dealguard:dealguard /app
USER dealguard
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
