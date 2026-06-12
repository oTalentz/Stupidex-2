FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 stupidex \
    && mkdir -p /data \
    && chown -R stupidex:stupidex /app /data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY pyproject.toml ./

ENV PYTHONPATH=/app/src
ENV STUPIDEX_HOST=0.0.0.0
ENV STUPIDEX_PORT=5000
ENV STUPIDEX_DEBUG=0
ENV STUPIDEX_DATA_DIR=/data
ENV STUPIDEX_ENABLE_SHELL=1

# GitHub OAuth for private repository cloning (optional)
# Uncomment and set these to enable GitHub integration:
# ENV GITHUB_CLIENT_ID=your_client_id
# ENV GITHUB_CLIENT_SECRET=your_client_secret
# ENV GITHUB_REDIRECT_URI=https://your-domain.com/api/integrations/github/callback
# ENV FRONTEND_URL=https://your-domain.com

USER stupidex

EXPOSE 5000

CMD ["gunicorn", "stupidex.web:app", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "1", \
     "--timeout", "120", \
     "--threads", "8", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
