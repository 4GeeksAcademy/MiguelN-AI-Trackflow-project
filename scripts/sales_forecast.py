"""Monthly revenue forecasting for TrackFlow (sales prediction ticket).

Pure building blocks used by `scripts/train_sales_forecast.py`. Column names,
market values and the expected patterns come from
`contexts/trackflow-sales-briefing.md`:

- columns `month, revenue_eur, shipments_processed, avg_revenue_per_shipment_eur, market`;
- `market` in `us`, `spain`, `consolidated`; the target is `revenue_eur` of `consolidated`;
- 120 consecutive months (2016-01 .. 2025-12), all revenue positive;
- growth 3-9 %/year, Nov-Dec +25-35 %, February -10-15 % against the yearly average.

Why the model predicts a ratio instead of EUR: tree ensembles (XGBoost, Random
Forest) cannot predict outside the range of target values they were trained on,
and the last two years are, by construction, above every training month. The
model therefore learns `revenue / mean of the previous 12 months`, a quantity
whose range does not grow with the business, and EUR are rebuilt by multiplying
back. Every feature for month t is built from months strictly before t.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor

EXPECTED_COLUMNS: Final = (
    "month",
    "revenue_eur",
    "shipments_processed",
    "avg_revenue_per_shipment_eur",
    "market",
)
MARKETS: Final = frozenset({"us", "spain", "consolidated"})
TARGET_MARKET: Final = "consolidated"
TARGET: Final = "revenue_eur"

TRAIN_YEARS: Final = 8
TEST_YEARS: Final = 2
RANDOM_STATE: Final = 42
US_SHARE_REFERENCE: Final = 0.60

# 36 months of history are needed before the first usable row: 12 for the
# trailing mean, then 24 more for the two previous years' seasonal ratios
# (which also cover the year-on-year growth of that mean).
WARMUP_MONTHS: Final = 36
LAGS: Final = (1, 2, 3, 12)
ROLLING_WINDOWS: Final = (3, 6, 12)

FEATURE_COLUMNS: Final = (
    *(f"lag_{k}_ratio" for k in LAGS),
    "rolling_mean_3_ratio",
    "rolling_mean_6_ratio",
    *(f"rolling_std_{w}_ratio" for w in ROLLING_WINDOWS),
    "trailing_growth_yoy",
    "seasonal_ratio_last_year",
    "seasonal_ratio_2y_mean",
    "month_of_year",
    "month_sin",
    "month_cos",
    "is_peak_season",
    "is_february",
)


class SalesDataError(ValueError):
    """The dataset breaks the briefing's contract in a way cleaning cannot repair."""


@dataclass
class CleaningReport:
    rows_read: int = 0
    empty_rows_dropped: int = 0
    exact_duplicates_dropped: int = 0
    revenue_derived: int = 0
    shipments_derived: int = 0
    avg_revenue_derived: int = 0
    markets_present: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class SplitResult:
    train: pd.DataFrame
    test: pd.DataFrame
    train_years: tuple[int, ...]
    test_years: tuple[int, ...]


# --------------------------------------------------------------------------- data


def load_sales(path: str | Path) -> pd.DataFrame:
    """Read the CSV as text and check the columns match the briefing exactly."""
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    frame.columns = [str(column).strip() for column in frame.columns]
    missing = [c for c in EXPECTED_COLUMNS if c not in frame.columns]
    unexpected = [c for c in frame.columns if c not in EXPECTED_COLUMNS]
    if missing or unexpected:
        raise SalesDataError(
            f"columns do not match the briefing: missing={missing}, unexpected={unexpected}"
        )
    return frame[list(EXPECTED_COLUMNS)]


def clean_sales(raw: pd.DataFrame) -> tuple[pd.DataFrame, CleaningReport]:
    """Handle empty/null values and types; return every market's rows, sorted.

    Repairs only what the dataset itself can justify: a missing figure among
    revenue, shipments and revenue per shipment is derived from the other two
    (`revenue = shipments x avg`). A target that cannot be derived is an error
    rather than an interpolation, because interpolating would invent revenue.
    """
    report = CleaningReport(rows_read=len(raw))
    frame = raw.copy()
    for column in frame.columns:
        frame[column] = frame[column].astype("string").str.strip()
        frame[column] = frame[column].replace(
            {"": pd.NA, "nan": pd.NA, "NaN": pd.NA, "null": pd.NA}
        )

    empty = frame.isna().all(axis=1)
    report.empty_rows_dropped = int(empty.sum())
    frame = frame.loc[~empty]

    frame["market"] = frame["market"].str.lower()
    unknown = sorted(set(frame["market"].dropna()) - MARKETS)
    if frame["market"].isna().any() or unknown:
        raise SalesDataError(f"market must be one of {sorted(MARKETS)}; found {unknown or 'null'}")

    month = pd.to_datetime(frame["month"], format="%Y-%m-%d", errors="coerce")
    if month.isna().any():
        bad = frame.loc[month.isna(), "month"].tolist()
        raise SalesDataError(f"month must be YYYY-MM-01; unreadable values: {bad[:5]}")
    if (month.dt.day != 1).any():
        raise SalesDataError("month must be the first day of the month (YYYY-MM-01)")

    numeric = {
        column: pd.to_numeric(frame[column], errors="coerce")
        for column in ("revenue_eur", "shipments_processed", "avg_revenue_per_shipment_eur")
    }
    for column, values in numeric.items():
        unreadable = values.isna() & frame[column].notna()
        if unreadable.any():
            raise SalesDataError(
                f"{column} has non-numeric values: {frame.loc[unreadable, column].tolist()[:5]}"
            )

    clean = pd.DataFrame(
        {"month": month, "market": frame["market"].astype(str), **numeric}
    ).reset_index(drop=True)

    revenue, shipments, avg = (
        clean["revenue_eur"],
        clean["shipments_processed"],
        clean["avg_revenue_per_shipment_eur"],
    )
    fill_revenue = revenue.isna() & shipments.notna() & avg.notna()
    clean.loc[fill_revenue, "revenue_eur"] = shipments[fill_revenue] * avg[fill_revenue]
    fill_shipments = shipments.isna() & clean["revenue_eur"].notna() & avg.notna() & (avg > 0)
    clean.loc[fill_shipments, "shipments_processed"] = (
        clean.loc[fill_shipments, "revenue_eur"] / avg[fill_shipments]
    ).round()
    fill_avg = avg.isna() & clean["revenue_eur"].notna() & (clean["shipments_processed"] > 0)
    clean.loc[fill_avg, "avg_revenue_per_shipment_eur"] = (
        clean.loc[fill_avg, "revenue_eur"] / clean.loc[fill_avg, "shipments_processed"]
    )
    report.revenue_derived = int(fill_revenue.sum())
    report.shipments_derived = int(fill_shipments.sum())
    report.avg_revenue_derived = int(fill_avg.sum())

    if clean["revenue_eur"].isna().any():
        months = clean.loc[clean["revenue_eur"].isna(), "month"].dt.strftime("%Y-%m").tolist()
        raise SalesDataError(f"revenue_eur is empty and cannot be derived for: {months[:5]}")

    before = len(clean)
    clean = clean.drop_duplicates()
    report.exact_duplicates_dropped = before - len(clean)
    if clean.duplicated(["market", "month"]).any():
        dup = clean.loc[clean.duplicated(["market", "month"], keep=False), ["market", "month"]]
        raise SalesDataError(
            f"conflicting rows for the same market and month: {dup.head().to_dict('records')}"
        )

    report.markets_present = sorted(clean["market"].unique())
    return clean.sort_values(["market", "month"]).reset_index(drop=True), report


def consolidated_series(clean: pd.DataFrame) -> pd.Series:
    """The model's target: monthly consolidated revenue, validated against the briefing."""
    rows = clean.loc[clean["market"] == TARGET_MARKET]
    if rows.empty:
        raise SalesDataError("no 'consolidated' rows: the target row is missing")
    series = rows.set_index("month")[TARGET].astype(float).sort_index()
    series.index = pd.DatetimeIndex(series.index, freq=None, name="month")

    expected = pd.date_range(series.index.min(), series.index.max(), freq="MS")
    missing = expected.difference(series.index)
    if len(missing):
        raise SalesDataError(f"missing months: {[m.strftime('%Y-%m') for m in missing[:5]]}")
    if (series <= 0).any():
        bad = series[series <= 0].index.strftime("%Y-%m").tolist()
        raise SalesDataError(f"revenue_eur must be positive; non-positive in {bad[:5]}")
    series.index.freq = "MS"
    series.name = TARGET
    return series


def market_mix(clean: pd.DataFrame) -> pd.Series | None:
    """US share of US+Spain revenue per month, or None when the markets are absent."""
    pivot = clean.pivot_table(index="month", columns="market", values=TARGET, aggfunc="sum")
    if not {"us", "spain"} <= set(pivot.columns):
        return None
    both = pivot[["us", "spain"]].dropna()
    return (both["us"] / (both["us"] + both["spain"])).rename("us_share")


# -------------------------------------------------------------------------- split


def split_train_test(
    series: pd.Series, train_years: int = TRAIN_YEARS, test_years: int = TEST_YEARS
) -> SplitResult:
    """First `train_years` calendar years for training, last `test_years` for test.

    The split is by calendar year, never by row count or at random, and it
    refuses data that is not made of complete years, so a missing December
    cannot silently move a month from one side to the other.
    """
    if not isinstance(series.index, pd.DatetimeIndex):
        raise SalesDataError("the series must be indexed by month")
    counts = series.groupby(series.index.year).size()
    incomplete = counts[counts != 12]
    if len(incomplete):
        raise SalesDataError(f"incomplete calendar years: {incomplete.to_dict()}")
    years = tuple(int(y) for y in counts.index)
    if len(years) != train_years + test_years:
        raise SalesDataError(
            f"expected {train_years + test_years} years ({train_years} train + "
            f"{test_years} test); found {len(years)}: {years}"
        )
    train_set, test_set = years[:train_years], years[train_years:]
    frame = series.to_frame()
    train = frame.loc[frame.index.year.isin(train_set)]
    test = frame.loc[frame.index.year.isin(test_set)]
    assert train.index.max() < test.index.min()
    assert train.index.intersection(test.index).empty
    return SplitResult(train=train, test=test, train_years=train_set, test_years=test_set)


# ----------------------------------------------------------------------- features


def build_features(revenue: pd.Series) -> pd.DataFrame:
    """Causal features for every month, plus the target ratio and its base.

    Every column for month t uses revenue up to t-1 only (`shift(1)` before any
    window). `base` is the mean of the previous 12 months; `target_ratio` is the
    only column that reads month t itself and it is the label, not a feature.
    The first `WARMUP_MONTHS` rows are NaN by construction.
    """
    past = revenue.shift(1)
    base = past.rolling(12, min_periods=12).mean()
    out = pd.DataFrame(index=revenue.index)
    out["base"] = base
    for k in LAGS:
        out[f"lag_{k}_ratio"] = revenue.shift(k) / base
    out["rolling_mean_3_ratio"] = past.rolling(3, min_periods=3).mean() / base
    out["rolling_mean_6_ratio"] = past.rolling(6, min_periods=6).mean() / base
    for w in ROLLING_WINDOWS:
        out[f"rolling_std_{w}_ratio"] = past.rolling(w, min_periods=w).std() / base
    out["trailing_growth_yoy"] = base / base.shift(12) - 1
    # Seasonal memory: what this calendar month looked like against its own
    # trailing mean one and two years ago. Averaging two years cancels most of
    # the briefing's alternating 9 % / 3 % growth. Both read months <= t-12.
    ratio = revenue / base
    out["seasonal_ratio_last_year"] = ratio.shift(12)
    out["seasonal_ratio_2y_mean"] = (ratio.shift(12) + ratio.shift(24)) / 2
    month = revenue.index.month
    out["month_of_year"] = month
    out["month_sin"] = np.sin(2 * np.pi * month / 12)
    out["month_cos"] = np.cos(2 * np.pi * month / 12)
    out["is_peak_season"] = month.isin([11, 12]).astype(int)
    out["is_february"] = (month == 2).astype(int)
    out["target_ratio"] = ratio
    return out


def training_rows(revenue: pd.Series) -> tuple[pd.DataFrame, pd.Series]:
    """Feature matrix and ratio target for every month with a complete history."""
    features = build_features(revenue).iloc[WARMUP_MONTHS:]
    features = features.dropna(subset=[*FEATURE_COLUMNS, "target_ratio"])
    return features[list(FEATURE_COLUMNS)], features["target_ratio"]


# -------------------------------------------------------------------------- model

# Algorithm choice (ticket: "justify XGBoost or Random Forest"):
# - Data size: 96 training months, 60 usable after the 36-month warm-up. Both
#   algorithms fit this; XGBoost's shrinkage, depth limits and L2 penalty give
#   more control against over-fitting a small set than averaging deep trees.
# - What the stakeholder needs: the ticket asks for an "acceptable margin of
#   error" before money is committed, i.e. accuracy, while explainability is
#   handled by reporting the error in EUR and as a % of monthly revenue, by the
#   feature importances, and by a seasonal-naive baseline Finance can verify by
#   hand. The developer chose accuracy (XGBoost) over Random Forest's simpler story.
# - Tuning time: the grid is deliberately small (8 combinations), each scored
#   by recursive backtests inside the 8 training years only, so tuning takes
#   seconds and never sees 2024-2025.
PARAM_GRID: Final = [
    {"max_depth": depth, "n_estimators": trees, "learning_rate": rate}
    for depth in (2, 3)
    for trees in (200, 400)
    for rate in (0.03, 0.1)
]
DEFAULT_PARAMS: Final = {"max_depth": 3, "n_estimators": 200, "learning_rate": 0.1}


def make_pipeline() -> Pipeline:
    # Trees are scale-invariant, so scaling does not change XGBoost's splits; it
    # is kept (fitted on training rows only) to put ratios, growth rates and
    # month numbers on one scale and to keep the pipeline swappable.
    return Pipeline(
        [
            ("scale", StandardScaler()),
            (
                "model",
                XGBRegressor(
                    objective="reg:squarederror",
                    subsample=0.8,
                    colsample_bytree=0.8,
                    min_child_weight=2,
                    reg_lambda=1.0,
                    random_state=RANDOM_STATE,
                    n_jobs=1,
                ),
            ),
        ]
    )


def fit_model(revenue_train: pd.Series, tune: bool = True) -> tuple[Pipeline, dict[str, Any]]:
    """Choose parameters by recursive backtest inside training, then refit on all of it.

    Candidates are scored the way the model is used: a 24-month recursive
    forecast from three origins inside the training years, scored by mean
    absolute relative error. Ordinary one-step cross-validation would score a
    task the model never performs, on folds of about 12 rows.
    """
    x, y = training_rows(revenue_train)
    candidates = PARAM_GRID if tune else [DEFAULT_PARAMS]
    scored: list[tuple[float, dict[str, Any]]] = []
    for params in candidates:
        errors = backtest_relative_errors(revenue_train, params)
        scored.append((float(errors["relative_error"].abs().mean()), params))
    best_score, best = min(scored, key=lambda item: item[0])
    pipeline = make_pipeline().set_params(**{f"model__{k}": v for k, v in best.items()})
    pipeline.fit(x, y)
    return pipeline, {
        "params": best,
        "selection": "lowest mean absolute relative error in recursive backtests",
        "backtest_mape_pct": round(100 * best_score, 3),
        "candidates": [{"params": p, "backtest_mape_pct": round(100 * s, 3)} for s, p in scored],
    }


def forecast_recursive(
    model: Pipeline,
    history: pd.Series,
    months: Sequence[pd.Timestamp] | pd.DatetimeIndex,
    feature_columns: Sequence[str] = FEATURE_COLUMNS,
) -> pd.Series:
    """Forecast each month from history plus the model's own earlier forecasts.

    This is what forecasting "the coming months" means: at month 24 of the
    horizon the model knows nothing measured after the last history month.
    `feature_columns` must be the columns `model` was fitted on; the default is
    the shipped model's full set.
    """
    extended = history.astype(float).copy()
    predictions: dict[pd.Timestamp, float] = {}
    for month in months:
        month = pd.Timestamp(month)
        extended.loc[month] = np.nan
        row = build_features(extended).loc[[month]]
        ratio = float(model.predict(row[list(feature_columns)])[0])
        value = ratio * float(row["base"].iloc[0])
        extended.loc[month] = value
        predictions[month] = value
    return pd.Series(predictions, name="forecast_eur").rename_axis("month")


def seasonal_naive_forecast(history: pd.Series, months: pd.DatetimeIndex) -> pd.Series:
    """Baseline Finance can recompute: same month last year x last year's growth."""
    last_year = history.index.max().year
    growth = history[str(last_year)].sum() / history[str(last_year - 1)].sum()
    extended = history.astype(float).copy()
    for month in months:
        extended.loc[month] = extended.loc[month - pd.DateOffset(years=1)] * growth
    return extended.loc[months].rename("baseline_eur")


def backtest_relative_errors(
    revenue_train: pd.Series, params: dict[str, Any], horizon: int = 24, origins: int = 3
) -> pd.DataFrame:
    """Walk-forward recursive backtests inside the training years only.

    For each origin year the model is refitted on what precedes it and forecasts
    the next `horizon` months still inside training. The relative errors
    (`actual / forecast - 1`) are the variability band's raw material, so the
    band is estimated without looking at 2024-2025.
    """
    years = sorted(set(revenue_train.index.year))
    last_origin = years[-1] - horizon // 12
    rows: list[dict[str, Any]] = []
    for origin_year in range(last_origin - origins + 1, last_origin + 1):
        history = revenue_train[: str(origin_year)]
        if len(history) - WARMUP_MONTHS < 12:
            continue
        x, y = training_rows(history)
        pipeline = make_pipeline().set_params(**{f"model__{k}": v for k, v in params.items()})
        pipeline.fit(x, y)
        months = pd.date_range(f"{origin_year + 1}-01-01", periods=horizon, freq="MS")
        forecast = forecast_recursive(pipeline, history, months)
        actual = revenue_train.loc[months]
        for step, month in enumerate(months, start=1):
            rows.append(
                {
                    "origin_year": origin_year,
                    "month": month,
                    "horizon_year": 1 + (step - 1) // 12,
                    "relative_error": float(actual[month] / forecast[month] - 1),
                }
            )
    return pd.DataFrame(rows)


def variability_band(
    forecast: pd.Series, errors: pd.DataFrame, coverage: float = 0.80
) -> pd.DataFrame:
    """Symmetric `coverage` range per horizon year from the backtest's errors.

    Uses the `coverage` quantile of the absolute relative error, not separate
    P10/P90 tails: with three backtest origins, each tail would mostly echo
    whether those three years happened to be over- or under-forecast.
    """
    width = (
        errors.assign(abs_error=errors["relative_error"].abs())
        .groupby("horizon_year")["abs_error"]
        .quantile(coverage)
    )
    horizon_year = 1 + np.arange(len(forecast)) // 12
    half = width.reindex(horizon_year).to_numpy()
    out = forecast.to_frame("forecast_eur")
    out["lower_eur"] = forecast.to_numpy() * (1 - half)
    out["upper_eur"] = forecast.to_numpy() * (1 + half)
    return out


# ------------------------------------------------------------------------ metrics


def gini(actual: Sequence[float] | np.ndarray, predicted: Sequence[float] | np.ndarray) -> float:
    """Gini of `actual` ordered by `predicted` (Kaggle's regression Gini)."""
    a = np.asarray(actual, dtype=float)
    p = np.asarray(predicted, dtype=float)
    order = np.lexsort((np.arange(len(a)), -p))
    cumulative = np.cumsum(a[order]) / a.sum()
    return float(cumulative.sum() / len(a) - (len(a) + 1) / (2 * len(a)))


def normalized_gini(
    actual: Sequence[float] | np.ndarray, predicted: Sequence[float] | np.ndarray
) -> float:
    """1.0 = months ranked exactly as reality, 0 = random ranking, < 0 = inverted."""
    return gini(actual, predicted) / gini(actual, actual)


def psi(
    expected: Sequence[float] | np.ndarray, actual: Sequence[float] | np.ndarray, bins: int = 5
) -> float:
    """Population Stability Index of `actual` against `expected`.

    Bins are `expected`'s quantiles. Five bins, not the usual ten, because the
    test side holds 24 months; ten bins would leave ~2 months per bin and turn
    the index into noise. Rule of thumb: < 0.10 stable, 0.10-0.25 moderate
    shift, > 0.25 significant shift.
    """
    e = np.asarray(expected, dtype=float)
    a = np.asarray(actual, dtype=float)
    edges = np.unique(np.quantile(e, np.linspace(0, 1, bins + 1)))
    edges[0], edges[-1] = -np.inf, np.inf
    e_share = np.histogram(e, edges)[0] / len(e)
    a_share = np.histogram(a, edges)[0] / len(a)
    eps = 1e-6
    e_share = np.clip(e_share, eps, None)
    a_share = np.clip(a_share, eps, None)
    return float(np.sum((a_share - e_share) * np.log(a_share / e_share)))


def regression_metrics(actual: pd.Series, predicted: pd.Series) -> dict[str, float]:
    a = actual.to_numpy(dtype=float)
    p = predicted.reindex(actual.index).to_numpy(dtype=float)
    mse = float(mean_squared_error(a, p))
    rmse = float(np.sqrt(mse))
    mean_revenue = float(a.mean())
    return {
        "mse_eur2": mse,
        "rmse_eur": rmse,
        "rmse_pct_of_mean_monthly_revenue": 100 * rmse / mean_revenue,
        "mse_pct_of_mean_monthly_revenue_squared": 100 * mse / mean_revenue**2,
        "mae_eur": float(mean_absolute_error(a, p)),
        "mape_pct": float(100 * np.mean(np.abs(a - p) / a)),
        "k2_score_r2": float(r2_score(a, p)),
        "gini_normalized": normalized_gini(a, p),
        "mean_monthly_revenue_eur": mean_revenue,
    }


def psi_shift_p_value(
    expected: Sequence[float] | np.ndarray,
    actual: Sequence[float] | np.ndarray,
    bins: int = 5,
    permutations: int = 2000,
) -> float:
    """Share of random re-splits whose PSI is at least the observed one.

    The 0.10 / 0.25 rule of thumb assumes thousands of rows. With 24 test months
    PSI is large by chance alone, so the two samples are pooled, re-split at
    random into the same sizes many times, and the observed PSI is compared
    with that no-shift reference. A high share (>= 0.05) means the shift cannot
    be told apart from sampling noise. Months are treated as exchangeable,
    which ignores their order; it is a size reference, not a time-series test.
    """
    e = np.asarray(expected, dtype=float)
    a = np.asarray(actual, dtype=float)
    observed = psi(e, a, bins)
    pooled = np.concatenate([e, a])
    rng = np.random.default_rng(RANDOM_STATE)
    as_large = 0
    for _ in range(permutations):
        shuffled = rng.permutation(pooled)
        as_large += psi(shuffled[: len(e)], shuffled[len(e) :], bins) >= observed
    return as_large / permutations


def psi_label(value: float) -> str:
    if value < 0.10:
        return "stable"
    if value < 0.25:
        return "moderate shift"
    return "significant shift"


# ------------------------------------------------------------------ data profile


def data_profile(revenue: pd.Series) -> dict[str, Any]:
    """Growth and seasonality actually present, checked against the briefing."""
    yearly = revenue.groupby(revenue.index.year).sum()
    growth = (yearly / yearly.shift(1) - 1).dropna()
    yearly_mean = revenue.groupby(revenue.index.year).transform("mean")
    seasonal_index = (revenue / yearly_mean).groupby(revenue.index.month).mean()
    nov_dec = float(seasonal_index[[11, 12]].mean() - 1)
    february = float(seasonal_index[2] - 1)
    others = seasonal_index.drop([2, 11, 12]) - 1
    return {
        "months": len(revenue),
        "first_month": revenue.index.min().strftime("%Y-%m"),
        "last_month": revenue.index.max().strftime("%Y-%m"),
        "yearly_growth_pct": {int(y): round(100 * g, 2) for y, g in growth.items()},
        "seasonal_index_pct": {int(m): round(100 * (v - 1), 2) for m, v in seasonal_index.items()},
        "checks": {
            # Tolerances are a little wider than the briefing's ranges: the
            # index is measured against a yearly mean that the peak itself lifts.
            "growth_between_0_and_12_pct": bool(((growth > 0) & (growth < 0.12)).all()),
            "nov_dec_uplift_15_to_40_pct": bool(0.15 <= nov_dec <= 0.40),
            "february_drop_5_to_20_pct": bool(-0.20 <= february <= -0.05),
            "other_months_within_10_pct": bool((others.abs() <= 0.10).all()),
        },
    }
