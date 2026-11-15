#!/usr/bin/env bash
# Download the FIRMS yearly country archives for ALL countries into FIRELENS_DATA_DIR (runs in the background).
# Afterwards any location's 2002-2025 history comes from local disk. Log: /srv/fireharmonics/prewarm.log
set -euo pipefail
cd /srv/fireharmonics/app/workspace/backend/FireHarmonics/firelens
sudo -u fireharmonics bash -c 'set -a; . /etc/fireharmonics/backend.env; set +a; nohup /srv/fireharmonics/venv/bin/python tools/prewarm_archives.py --all > /srv/fireharmonics/prewarm.log 2>&1 &'
echo "started; follow with: tail -f /srv/fireharmonics/prewarm.log"
