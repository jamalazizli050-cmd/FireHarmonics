"""Run the next-month fire-occurrence forecast (validated domain: California)."""
from datetime import date
import json

from firelens.config import load_settings
from firelens.logging_utils import setup_logging
from firelens import ml_pipeline

S = load_settings(require_key=False)
setup_logging("INFO", S.data_dir.parent / "logs" / "forecast.log")
res = ml_pipeline.run_forecast(
    S, ["california"], date(2012, 1, 1), date(2022, 12, 31),
    train_end=2018, calib_year=2019, test_years=[2020, 2021, 2022],
    diagnostic_regions=["zambia_angola", "rondonia", "yakutia", "punjab"])
print(json.dumps({k: v for k, v in res.items() if k != "final_model_files"}, indent=1, default=str)[:6000])
print("FORECAST DONE")
