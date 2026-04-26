# ============================================================
# Dockerfile — Elmazraa Waste Intelligence
# 3 services FastAPI gérés par supervisord
# Railway expose uniquement PORT=8000
# ============================================================

FROM python:3.11-slim

# Variables d'environnement
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONUTF8=1

# Installer supervisord + dépendances système
RUN apt-get update && apt-get install -y \
    supervisor \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copier et installer les dépendances Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copier tout le projet
COPY . .

# Créer les dossiers nécessaires
RUN mkdir -p outputs/uploads outputs/reports outputs/images outputs/chromadb \
    && mkdir -p /var/log/supervisor

# Copier la config supervisord
COPY supervisord.conf /etc/supervisor/conf.d/waste.conf

# Railway injecte PORT=8000 automatiquement
EXPOSE 8000

# Démarrer supervisord (gère les 3 processus)
CMD ["supervisord", "-n", "-c", "/etc/supervisor/conf.d/waste.conf"]