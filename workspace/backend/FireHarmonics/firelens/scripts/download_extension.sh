#!/usr/bin/env bash
# Bounded history extension for the 5 harmonisation regions (rate-limit aware via the client).
# VIIRS first (needed for forecasting + 2022 forward test), then MODIS 2012-2017.
set -u
cd "$(dirname "$0")/.."
PY=.venv/Scripts/python; [ -x "$PY" ] || PY=.venv/bin/python
until grep -q "ALL DONE" logs/download_regions.txt 2>/dev/null; do sleep 30; done
REGIONS="california zambia_angola rondonia yakutia punjab"
for r in $REGIONS; do
  echo "=== VIIRS $r $(date)"
  $PY -m firelens download --region "$r" --sources VIIRS_SNPP_SP --start 2012-01-20 --end 2017-12-31 2>&1 | grep -E "Download summary|ERROR|Traceback"
  $PY -m firelens download --region "$r" --sources VIIRS_SNPP_SP --start 2022-01-01 --end 2022-12-31 2>&1 | grep -E "Download summary|ERROR|Traceback"
  $PY -m firelens process  --region "$r" --sources VIIRS_SNPP_SP --start 2012-01-01 --end 2022-12-31 2>&1 | grep -E "Processed|ERROR|Traceback"
done
echo "=== VIIRS DONE $(date)"
for r in $REGIONS; do
  echo "=== MODIS $r $(date)"
  $PY -m firelens download --region "$r" --sources MODIS_SP --start 2012-01-01 --end 2017-12-31 2>&1 | grep -E "Download summary|ERROR|Traceback"
  $PY -m firelens process  --region "$r" --sources MODIS_SP --start 2012-01-01 --end 2021-12-31 2>&1 | grep -E "Processed|ERROR|Traceback"
done
echo "=== ALL DONE $(date)"
