#!/usr/bin/env bash
# Pull the latest code from GitHub and redeploy (keeps data, key and Caddy config).
set -euo pipefail
cd /srv/fireharmonics/src && git pull --ff-only
sudo FH_DOMAIN="$(grep -m1 -oE '^[^ {]+' /etc/caddy/Caddyfile)" bash deploy/azure/setup.sh
