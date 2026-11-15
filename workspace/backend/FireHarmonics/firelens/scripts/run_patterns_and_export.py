"""Pattern discovery for all harmonization regions, then the API export.

The period is 2012-01-01..2021-12-31: the span where both Aqua-MODIS and S-NPP VIIRS
were downloaded for every region. The MODIS-equivalent series is computed only for
regions with a saved harmonization model, and is always labelled not validated for
the full domain (see docs/API_INTEGRATION_SPEC.md).
"""
from datetime import date
import json
import sys

from firelens.catalog import HARMONIZATION_REGIONS
from firelens.config import load_settings
from firelens.logging_utils import setup_logging
from firelens import api_export, ml_pipeline

S = load_settings(require_key=False)
setup_logging("INFO", S.data_dir.parent / "logs" / "patterns.log")
START, END = date(2012, 1, 1), date(2021, 12, 31)

status_path = S.reports_dir / "results" / "harmonization" / "validation_status.json"
harm = json.loads(status_path.read_text())
# MODIS-equivalent series only where the count harmonization was validated on held-out data.
validated = [r for r, v in harm.items() if v.get("count", {}).get("status") == "validated"]

if "--export-only" not in sys.argv:
    res = ml_pipeline.run_patterns(S, list(HARMONIZATION_REGIONS), START, END, harmonized_regions=validated)
    print("patterns done:", list(res))
    from firelens import patterns_figures
    print(patterns_figures.make_all(S))

LIMITATIONS = [
    "Target is satellite detection (S-NPP VIIRS, vegetation fires), not fire occurrence on the ground; clouds and "
    "smoke can hide fires.",
    "Model uses only past detection history, location and season. No weather, fuel or vegetation inputs, so it "
    "cannot anticipate anomalous weather-driven fire months.",
    "Validated only for California (held-out years 2020-2022 and held-out 2-degree spatial blocks). Applied to "
    "other regions it was worse than a local seasonal climatology in 3 of 4 regions, so no forecasts are provided "
    "there.",
    "Skill gain over a seasonal-climatology baseline is small (about +0.01 ROC-AUC, about 5% lower Brier score); "
    "most predictability comes from where and when fires usually occur.",
    "The deployed model is refitted on 2013-2021 and calibrated (isotonic) on 2022 with the same settings as the "
    "validated model (trained 2013-2018, calibrated 2019, tested 2020-2022). Calibration may drift if fire regimes "
    "change.",
]
status = {"regions": list(HARMONIZATION_REGIONS), "harmonization": harm,
          "forecast_regions": ["california"], "forecast_limitations": LIMITATIONS}
written = api_export.export_all(S, status)
print("\n".join(written))
print("EXPORT DONE")
