"""Test-wide defaults.

The descriptive-backend tests must never start the real FireLens engine: it would
contact NASA and write into firelens/data. They therefore run with the engine
disabled; tests in test_ml_integration.py inject engines explicitly.
"""
import os

os.environ.setdefault("FIREHARMONICS_ML_ENGINE", "disabled")
