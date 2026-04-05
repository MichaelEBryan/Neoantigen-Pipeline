#!/usr/bin/env bash
# setup-vm.sh -- Bootstrap a fresh Ubuntu 22.04 VM for Oxford Cancer Vaccine Design v0.1
# Run as root or with sudo on the target VM.
# Tested on GCP Compute Engine e2-standard-4 (4 vCPU, 16GB).
set -euo pipefail

echo "=== 1/6  System packages ==="
apt-get update -qq
apt-get install -y -qq docker.io docker-compose-v2 git ufw fail2ban

echo "=== 2/6  Docker daemon ==="
systemctl enable docker
systemctl start docker

echo "=== 3/6  Firewall ==="
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "=== 4/6  Create app user ==="
if ! id -u cvdash &>/dev/null; then
  useradd -m -s /bin/bash -G docker cvdash
fi

echo "=== 5/6  Clone repo ==="
APP_DIR=/opt/cvdash
if [ ! -d "$APP_DIR" ]; then
  # Replace with your actual repo URL
  echo "NOTE: Clone your repo to $APP_DIR"
  echo "  git clone git@github.com:YOUR_ORG/CVDash.git $APP_DIR"
  echo "  chown -R cvdash:cvdash $APP_DIR"
  mkdir -p "$APP_DIR"
fi

echo "=== 6/6  Next steps ==="
cat <<'NEXT'

VM is ready. Now:

  1. Clone/copy the repo to /opt/cvdash
  2. Copy deploy/.env.production to /opt/cvdash/.env and fill in real values
  3. Create secrets directory:
       mkdir -p /opt/cvdash/secrets
       # Copy your GCP service account key JSON here
  4. Build and start:
       cd /opt/cvdash
       docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
  5. Run database migrations:
       docker compose exec backend alembic upgrade head
  6. Check health:
       curl http://localhost/api/health
  7. (Optional) Add SSL with Let's Encrypt:
       apt install certbot python3-certbot-nginx
       certbot --nginx -d vaccine.your-domain.ac.uk

NEXT
