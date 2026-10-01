FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock && groupadd -r meken && useradd -r -g meken meken
COPY --chown=meken:meken app ./app
COPY --chown=meken:meken migrations ./migrations
COPY --chown=meken:meken alembic.ini ./
COPY --chown=meken:meken data ./data
USER meken
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--timeout-keep-alive", "10", "--limit-concurrency", "200", "--timeout-graceful-shutdown", "30"]
