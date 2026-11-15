#!/usr/bin/env bash
# Download + process MODIS_SP and VIIRS_SNPP_SP for the contrasting harmonisation regions.
set -u
cd "$(dirname "$0")/.."
PY=.venv/Scripts/python
[ -x "$PY" ] || PY=.venv/bin/python
for r in zambia_angola rondonia yakutia punjab; do
  echo "=== $r $(date)"
  $PY -m firelens download --region "$r" --start 2018-01-01 --end 2021-12-31 2>&1 | grep -E "Download summary|ERROR|pausing|Traceback"
  $PY -m firelens process  --region "$r" --start 2018-01-01 --end 2021-12-31 2>&1 | grep -E "Processed|ERROR|Traceback"
done
echo "=== ALL DONE $(date)"
