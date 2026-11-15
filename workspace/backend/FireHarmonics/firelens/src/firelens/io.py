"""Parquet I/O with an engine fallback.

pandas' default Parquet engine is pyarrow. On some locked-down Windows
machines (Application Control / Smart App Control) the ``pyarrow._parquet``
extension is blocked even though core pyarrow loads. In that case we fall back
to polars, which bundles its own Parquet implementation and converts to and
from pandas via Arrow. The files are standard Parquet either way.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd


@lru_cache(maxsize=1)
def engine() -> str:
    try:
        import pyarrow.parquet  # noqa: F401
        return "pyarrow"
    except ImportError:
        pass
    try:
        import polars  # noqa: F401
        return "polars"
    except ImportError as e:
        raise ImportError("No usable Parquet engine: install pyarrow (with Parquet support) or polars") from e


def write_parquet(df: pd.DataFrame, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df = df.reset_index(drop=True)
    for c in df.columns:  # Period has no Parquet type; store as ISO strings
        if isinstance(df[c].dtype, pd.PeriodDtype):
            df[c] = df[c].astype(str)
    tmp = path.with_suffix(path.suffix + ".part")
    if engine() == "pyarrow":
        df.to_parquet(tmp, index=False, engine="pyarrow")
    else:
        import polars as pl
        pl.from_pandas(df).write_parquet(tmp, compression="zstd")
    tmp.replace(path)


def read_parquet(path: str | Path, columns: list[str] | None = None) -> pd.DataFrame:
    if engine() == "pyarrow":
        return pd.read_parquet(path, columns=columns, engine="pyarrow")
    import polars as pl
    df = pl.read_parquet(path, columns=columns).to_pandas()
    # Restore the ordered confidence categories (Arrow dictionaries lose ordering).
    if "confidence_class" in df:
        df["confidence_class"] = df["confidence_class"].astype(
            pd.CategoricalDtype(["low", "nominal", "high"], ordered=True))
    return df
