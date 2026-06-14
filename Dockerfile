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
COPY gunicorn.conf.py ./

ENV PYTHONPATH=/app/src
ENV STUPIDEX_HOST=0.0.0.0
ENV STUPIDEX_PORT=5000
ENV STUPIDEX_DEBUG=0
ENV STUPIDEX_DATA_DIR=/data
ENV STUPIDEX_ENABLE_SHELL=1
ENV STUPIDEX_SERVER=1

# GitHub OAuth for private repository cloning (optional)
# Uncomment and set these to enable GitHub integration:
# ENV GITHUB_CLIENT_ID=your_client_id
# ENV GITHUB_CLIENT_SECRET=your_client_secret
# ENV GITHUB_REDIRECT_URI=https://your-domain.com/api/integrations/github/callback
# ENV FRONTEND_URL=https://your-domain.com

USER stupidex

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=15s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/api/health')" || exit 1

CMD ["gunicorn", "-c", "gunicorn.conf.py", "stupidex.web:app"]
