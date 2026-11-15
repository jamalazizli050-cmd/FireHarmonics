#!/usr/bin/env bash
# Download the FIRMS yearly country archives (MODIS, S-NPP VIIRS, NOAA-20 VIIRS) for ALL countries into
# FIRELENS_DATA_DIR, then rebuild the regional priors of the continuity chain (restart the backend afterwards).
# Runs in the background; files already downloaded are skipped. Log: /srv/fireharmonics/prewarm.log
#   bash prewarm.sh                              # everything
#   bash prewarm.sh --products=viirs-jpss1       # only what is new (NOAA-20)
set -euo pipefail
cd /srv/fireharmonics/app/workspace/backend/FireHarmonics/firelens
ARGS="--all ${1:-}"
sudo -u fireharmonics bash -c "set -a; . /etc/fireharmonics/backend.env; set +a; nohup bash -c '/srv/fireharmonics/venv/bin/python tools/prewarm_archives.py $ARGS && /srv/fireharmonics/venv/bin/python tools/build_harmonization_priors.py' > /srv/fireharmonics/prewarm.log 2>&1 &"
echo "started; follow with: tail -f /srv/fireharmonics/prewarm.log"
echo "when it prints 'cells written', restart the backend: sudo systemctl restart fireharmonics-backend"
