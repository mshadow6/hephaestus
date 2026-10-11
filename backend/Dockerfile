FROM python:3.12-slim

WORKDIR /app

ARG TERRAFORM_VERSION=1.9.8
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl unzip ca-certificates git openssh-client sshpass && \
    curl -fsSL -o /tmp/terraform.zip "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip" && \
    unzip /tmp/terraform.zip -d /usr/local/bin && \
    rm /tmp/terraform.zip && \
    apt-get purge -y unzip && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt ansible-core==2.17.*

COPY app ./app
COPY worker.py .
COPY alembic.ini .
COPY alembic ./alembic

# --proxy-headers fait confiance à X-Forwarded-Proto/-For envoyés par Caddy (seul
# conteneur qui peut atteindre le backend, voir docker-compose.yml — plus de "ports:"
# directs dessus) : sans ça, request.url.scheme restait "http" même avec le HTTPS activé
# côté Caddy (la connexion interne Caddy -> backend est toujours en clair), ce qui
# cassait silencieusement le redirect_uri OIDC de Keycloak (généré en http:// au lieu de
# https://, rejeté par le realm car ne correspondant pas à l'URL enregistrée).
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
