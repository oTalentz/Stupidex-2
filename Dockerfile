FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY pyproject.toml ./

ENV PYTHONPATH=/app/src
ENV STUPIDEX_HOST=0.0.0.0
ENV STUPIDEX_PORT=5000
ENV STUPIDEX_DEBUG=0

EXPOSE 5000

CMD ["gunicorn", "stupidex.web:app", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "1", \
     "--timeout", "120", \
     "--threads", "8", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
