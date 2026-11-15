#!/usr/bin/env bash
# One-time (and repeatable) setup of FireHarmonics on an Ubuntu 24.04 Azure VM.
# Usage on the VM, from the cloned repo:  sudo FH_DOMAIN=<fqdn> NASA_FIRMS_MAP_KEY=<key> bash deploy/azure/setup.sh
set -euo pipefail
: "${FH_DOMAIN:?set FH_DOMAIN to the VM's DNS name, e.g. fireharmonics.westeurope.cloudapp.azure.com}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
APP=/srv/fireharmonics/app
VENV=/srv/fireharmonics/venv
DATA=/srv/fireharmonics/data          # FIRELENS_DATA_DIR: model + FIRMS archive cache
RUNTIME=/srv/fireharmonics/runtime    # backend SQLite job store
ENVFILE=/etc/fireharmonics/backend.env

echo "== packages"
apt-get update -y
apt-get install -y git curl rsync debian-keyring debian-archive-keyring apt-transport-https ca-certificates gnupg
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y && apt-get install -y caddy
fi
if ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 24 ]; then
  curl -fsSL https://deb.nodesource.com/setup_24.x | bash -
  apt-get install -y nodejs
fi
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
fi

echo "== service user and folders"
id fireharmonics >/dev/null 2>&1 || useradd --system --create-home --home-dir /srv/fireharmonics --shell /usr/sbin/nologin fireharmonics
mkdir -p "$APP" "$DATA" "$RUNTIME" /etc/fireharmonics
if [ "$REPO" != "$APP" ]; then
  rsync -a --delete --exclude node_modules --exclude dist --exclude .git "$REPO"/ "$APP"/
fi
# the forecast model must live inside FIRELENS_DATA_DIR
rsync -a "$APP/workspace/backend/FireHarmonics/firelens/data/models/" "$DATA/models/"
chown -R fireharmonics:fireharmonics /srv/fireharmonics

echo "== backend (Python 3.13 venv, locked requirements)"
sudo -u fireharmonics env HOME=/srv/fireharmonics UV_PYTHON_INSTALL_DIR=/srv/fireharmonics/.python uv venv --python 3.13 "$VENV" --allow-existing
sudo -u fireharmonics env HOME=/srv/fireharmonics uv pip install --python "$VENV/bin/python" -r "$APP/workspace/backend/FireHarmonics/requirements-lock.txt"

echo "== frontend build"
cd "$APP/workspace/frontend"
sudo -u fireharmonics env HOME=/srv/fireharmonics npm ci --no-fund --no-audit
sudo -u fireharmonics env HOME=/srv/fireharmonics npm run build

echo "== configuration"
if [ -n "${NASA_FIRMS_MAP_KEY:-}" ] || [ ! -f "$ENVFILE" ]; then
  umask 077
  cat > "$ENVFILE" <<CONF
NASA_FIRMS_MAP_KEY=${NASA_FIRMS_MAP_KEY:-}
FIRELENS_DATA_DIR=$DATA
FIREHARMONICS_DATA_DIR=$RUNTIME
FIRELENS_DOWNLOAD_WORKERS=4
CONF
  chown root:fireharmonics "$ENVFILE"; chmod 640 "$ENVFILE"
fi
install -m 644 "$APP/deploy/azure/fireharmonics-backend.service" /etc/systemd/system/fireharmonics-backend.service
sed "s|{\$FH_DOMAIN}|$FH_DOMAIN|" "$APP/deploy/azure/Caddyfile.template" > /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable --now fireharmonics-backend
systemctl restart fireharmonics-backend
systemctl reload caddy || systemctl restart caddy

echo "== health"
for i in $(seq 1 30); do curl -fsS http://127.0.0.1:8000/health && break; sleep 2; done
echo
echo "Done. Site: https://$FH_DOMAIN   (prewarm archives: sudo bash $APP/deploy/azure/prewarm.sh)"
