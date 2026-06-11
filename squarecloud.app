RUNTIME=python
MEMORY=2048
VERSION=recommended
SUBDOMAIN=stupidex
START=gunicorn 'stupidex.web:app' --bind 0.0.0.0:80 --workers 1 --timeout 120 --threads 8 --access-logfile - --error-logfile -
AUTORESTART=true
DISPLAY_NAME=Stupidex
DESCRIPTION=Stupidex - agente de código com IA (DeepSeek V4 Flash)
