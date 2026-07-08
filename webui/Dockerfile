# JobScout webui — frontend HTMX/Jinja2 léger (remplaçant progressif de Streamlit).
# Image slim : pas de Playwright ni pandas ici, ~150 Mo vs ~2 Go pour le backend.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates ./templates
COPY static ./static

EXPOSE 8502

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8502", "--log-level", "info"]
