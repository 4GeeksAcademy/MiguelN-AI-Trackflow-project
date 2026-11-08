"""Formal technical evaluation of the monthly revenue forecast (tech lead's ticket).

`scripts/sales_forecast.py` trains the model; this module decides whether it
can be trusted. It answers the ticket's three questions with evidence:

1. Is the model under-fitted, over-fitted or reasonably well fitted?
2. How stable is its error when the portion of data it trains on changes?
3. If there is a problem, which specific corrective action fixes it?

Only the 8 training years are used (2016-2023 of `data/raw/trackflow_sales.csv`,
`consolidated` rows, target `revenue_eur`). The 2 test years are never read, so
they remain an honest final check once a corrective action is approved.

How the error is measured. The training error is the model's in-sample fit,
and every training row's features are measured months, so it is a
one-month-ahead error by nature. Validation is scored the same way, one month
ahead from measured history, so the training/validation gap measures
over-fitting and nothing else. The error of the real use, a recursive forecast
that feeds on its own predictions, is reported next to it for every fold.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline

from scripts import sales_forecast as sf

# ------------------------------------------------------------------ settings

# Five expanding folds with 6-month validation windows. After the 36-month
# warm-up the training years leave 60 usable months (2019-01..2023-12); 5 x 6
# validation months leave 30 for the first fold to train on. sklearn's default
# sizing (60 // 6 = 10 validation months) would leave fold 1 with only 10
# training months, too few to say anything about fit.
CV_SPLITS: Final = 5
CV_VALIDATION_MONTHS: Final = 6
MIN_FIRST_FOLD_TRAIN_MONTHS: Final = 24

# Learning curves. On the CV windows, every fold must hold the size, so the
# largest is fold 1's 30 months. On the last training year, up to the 48 usable
# months that precede it.
CURVE_SIZES_CV_WINDOWS: Final = (12, 18, 24, 30)
CURVE_SIZES_FIXED_YEAR: Final = (12, 18, 24, 30, 36, 42, 48)

# Diagnosis thresholds (validation RMSE / training RMSE, both in % of revenue).
OVERFIT_GAP_RATIO: Final = 2.0
UNDERFIT_GAP_RATIO: Final = 1.5
# The learning curve still improves if its last step lowers validation RMSE by
# at least this share.
CONVERGED_TOLERANCE: Final = 0.05
# Candidates whose mean validation RMSE is within this many percentage points
# of the best are tied; the tie goes to the most stable (lowest std).
TIE_TOLERANCE_PP: Final = 0.1

PEAK_MONTHS: Final = (11, 12)
TAMPER_FACTOR: Final = 3.0

# A smaller feature set for the "reduce capacity" candidates: the same signals
# with each one encoded once. Every dropped column states why it can go.
COMPACT_FEATURES: Final = (
    "lag_1_ratio",
    "lag_2_ratio",
    "lag_3_ratio",
    "lag_12_ratio",
    "rolling_mean_3_ratio",
    "trailing_growth_yoy",
    "seasonal_ratio_last_year",
    "seasonal_ratio_2y_mean",
    "month_cos",
)
DROPPED_FROM_COMPACT: Final = {
    "is_peak_season": "zero gain; the seasonal ratios already mark Nov-Dec",
    "is_february": "zero gain; the seasonal ratios already mark February",
    "month_of_year": "a second encoding of the calendar month (month_cos is kept)",
    "month_sin": "a third encoding of the calendar month",
    "rolling_mean_6_ratio": "overlaps rolling_mean_3_ratio and the 12-month base",
    "rolling_std_3_ratio": "month-to-month volatility: the noise the model memorises",
    "rolling_std_6_ratio": "month-to-month volatility: the noise the model memorises",
    "rolling_std_12_ratio": "month-to-month volatility: the noise the model memorises",
}

# Corrective-action candidates: overrides on top of the shipped parameters and
# the feature columns. All are scored on the same folds as the shipped model.
CANDIDATES: Final[dict[str, tuple[dict[str, Any], tuple[str, ...]]]] = {
    "shipped": ({}, sf.FEATURE_COLUMNS),
    "depth_1": ({"max_depth": 1}, sf.FEATURE_COLUMNS),
    "compact_features": ({}, COMPACT_FEATURES),
    "depth_1_compact_features": ({"max_depth": 1}, COMPACT_FEATURES),
}
CANDIDATE_DESCRIPTIONS: Final = {
    "shipped": "As shipped: the tuned parameters, all 17 features",
    "depth_1": "Trees of depth 1 (no feature interactions), all 17 features",
    "compact_features": "Shipped depth, 9 compact features",
    "depth_1_compact_features": "Trees of depth 1 and 9 compact features",
}

SUMMARY_METRICS: Final = (
    "train_mae_eur",
    "train_rmse_eur",
    "train_mae_pct",
    "train_rmse_pct",
    "validation_mae_eur",
    "validation_rmse_eur",
    "validation_mae_pct",
    "validation_rmse_pct",
    "validation_bias_pct",
    "validation_recursive_mae_pct",
    "validation_recursive_rmse_pct",
    "naive_validation_mae_pct",
    "naive_validation_rmse_pct",
)
CURVE_METRICS: Final = (
    "train_rmse_pct",
    "validation_rmse_pct",
    "train_rmse_eur",
    "validation_rmse_eur",
    "train_mae_pct",
    "validation_mae_pct",
)

ROW_COLUMNS: Final = ("base", *sf.FEATURE_COLUMNS, "target_ratio")


class EvaluationError(RuntimeError):
    """A chronology or leakage check failed: the evaluation itself is not trustworthy."""


# --------------------------------------------------------------------- folds


@dataclass(frozen=True, eq=False)
class Fold:
    number: int
    train_months: pd.DatetimeIndex
    validation_months: pd.DatetimeIndex

    def describe(self) -> dict[str, Any]:
        return {
            "fold": self.number,
            "train_first": self.train_months[0].strftime("%Y-%m"),
            "train_last": self.train_months[-1].strftime("%Y-%m"),
            "train_months": len(self.train_months),
            "validation_first": self.validation_months[0].strftime("%Y-%m"),
            "validation_last": self.validation_months[-1].strftime("%Y-%m"),
            "validation_months": len(self.validation_months),
        }


def usable_months(revenue: pd.Series) -> pd.DatetimeIndex:
    """Months with a complete feature history (the 36-month warm-up excluded)."""
    x, _ = sf.training_rows(revenue)
    return pd.DatetimeIndex(x.index)


def temporal_folds(
    revenue: pd.Series,
    n_splits: int = CV_SPLITS,
    validation_months: int = CV_VALIDATION_MONTHS,
    max_train_size: int | None = None,
) -> list[Fold]:
    """Time-aware folds from sklearn's `TimeSeriesSplit`, as months.

    Each fold trains on months that all come before its validation window, and
    each validation window comes after the previous one; nothing is shuffled.
    Without `max_train_size` the training window expands from the first usable
    month; with it, training is the `max_train_size` months right before the
    validation window.
    """
    months = usable_months(revenue)
    splitter = TimeSeriesSplit(
        n_splits=n_splits, test_size=validation_months, max_train_size=max_train_size
    )
    try:
        splits = list(splitter.split(np.arange(len(months))))
    except ValueError as exc:
        raise sf.SalesDataError(
            f"{len(months)} usable months are not enough for {n_splits} folds of "
            f"{validation_months} validation months: {exc}"
        ) from exc
    folds = [
        Fold(number, pd.DatetimeIndex(months[train]), pd.DatetimeIndex(months[validation]))
        for number, (train, validation) in enumerate(splits, start=1)
    ]
    if max_train_size is None and len(folds[0].train_months) < MIN_FIRST_FOLD_TRAIN_MONTHS:
        raise sf.SalesDataError(
            f"fold 1 would train on {len(folds[0].train_months)} months; "
            f"at least {MIN_FIRST_FOLD_TRAIN_MONTHS} are needed"
        )
    assert_chronological(folds)
    return folds


def assert_chronological(folds: Sequence[Fold]) -> None:
    """Refuse folds that mix, shuffle or reorder months.

    Inside each fold, training then validation must be one contiguous,
    increasing run of months (so training ends right before validation, with no
    gap and no overlap). Across folds, each validation window must start after
    the previous one ends: no month of a later fold comes before one of an
    earlier fold.
    """
    previous_end: pd.Timestamp | None = None
    for fold in folds:
        for name, months in (
            ("training", fold.train_months),
            ("validation", fold.validation_months),
        ):
            if len(months) == 0:
                raise EvaluationError(f"fold {fold.number}: empty {name} window")
            if not (months.is_monotonic_increasing and months.is_unique):
                raise EvaluationError(f"fold {fold.number}: {name} months are out of order")
        expected = pd.date_range(fold.train_months[0], fold.validation_months[-1], freq="MS")
        if not fold.train_months.append(fold.validation_months).equals(expected):
            raise EvaluationError(
                f"fold {fold.number}: training and validation are not one contiguous, "
                "ordered run of months"
            )
        if previous_end is not None and fold.validation_months[0] <= previous_end:
            raise EvaluationError(
                f"fold {fold.number}: validation starts before the previous fold's ends"
            )
        previous_end = fold.validation_months[-1]


# ------------------------------------------------------------ rows and leakage


def fold_rows(revenue: pd.Series, fold: Fold) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Training and validation rows of one fold, built only from what existed then.

    Training rows are built from the series cut at the fold's last training
    month, so no validation month (or anything later) exists while they are
    computed. Validation rows are one month ahead: the row for month t reads
    measured months up to t-1. Both carry `base`, the features, the
    `target_ratio` label and `revenue_eur`.
    """
    seen = revenue.loc[: fold.train_months[-1]]
    train = sf.build_features(seen).loc[fold.train_months, list(ROW_COLUMNS)]
    validation = sf.build_features(revenue.loc[: fold.validation_months[-1]]).loc[
        fold.validation_months, list(ROW_COLUMNS)
    ]
    if train.isna().any().any():
        raise sf.SalesDataError(f"fold {fold.number}: training rows fall inside the warm-up")
    train = train.assign(**{sf.TARGET: revenue.loc[fold.train_months].to_numpy()})
    validation = validation.assign(**{sf.TARGET: revenue.loc[fold.validation_months].to_numpy()})
    return train, validation


def _same(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    return (
        a.index.equals(b.index)
        and list(a.columns) == list(b.columns)
        and bool(
            np.allclose(
                a.to_numpy(dtype=float),
                b.to_numpy(dtype=float),
                rtol=1e-12,
                atol=0.0,
                equal_nan=True,
            )
        )
    )


def check_fold_boundary_leakage(revenue: pd.Series, fold: Fold) -> dict[str, bool]:
    """Prove that no feature of a fold reads across its boundary.

    A lag or rolling feature computed on the full series before splitting can
    still read a later fold. Three checks rule that out:

    - every month after the fold's last training month is multiplied by 3, and
      the training rows built from the full series must not change;
    - the training rows built from the full series must equal the ones built
      from the series cut at the boundary (what `fold_rows` uses);
    - for each validation month t, months t and later are multiplied by 3, and
      its features must not change: it reads only months before t.
    """
    columns = list(ROW_COLUMNS)
    features_only = ["base", *sf.FEATURE_COLUMNS]
    full = sf.build_features(revenue)
    boundary = fold.train_months[-1]
    tampered = revenue.where(revenue.index <= boundary, revenue * TAMPER_FACTOR)
    after_tamper = sf.build_features(tampered)
    train_rows, validation_rows = fold_rows(revenue, fold)

    validation_ok = True
    for month in fold.validation_months:
        later_tampered = revenue.where(revenue.index < month, revenue * TAMPER_FACTOR)
        rebuilt = sf.build_features(later_tampered).loc[[month]][features_only]
        validation_ok = validation_ok and _same(
            rebuilt, validation_rows.loc[[month]][features_only]
        )

    return {
        "training_rows_ignore_later_months": _same(
            full.loc[fold.train_months, columns], after_tamper.loc[fold.train_months, columns]
        ),
        "training_rows_match_full_series_build": _same(
            full.loc[fold.train_months, columns], train_rows[columns]
        ),
        "validation_rows_read_only_earlier_months": validation_ok,
    }


# ------------------------------------------------------------------- metrics


def error_metrics(actual: pd.Series, predicted: pd.Series) -> dict[str, float]:
    """MAE and RMSE in EUR and as % of the set's mean monthly revenue, plus bias.

    `bias_pct` is signed: total forecast minus total actual, over total actual.
    Positive means over-forecast, negative under-forecast.
    """
    base = sf.regression_metrics(actual, predicted)
    mean = base["mean_monthly_revenue_eur"]
    error = predicted.reindex(actual.index) - actual
    return {
        "mae_eur": base["mae_eur"],
        "rmse_eur": base["rmse_eur"],
        "mse_eur2": base["mse_eur2"],
        "mae_pct": 100 * base["mae_eur"] / mean,
        "rmse_pct": base["rmse_pct_of_mean_monthly_revenue"],
        "bias_pct": float(100 * error.sum() / actual.sum()),
    }


def seasonal_naive_one_step(revenue: pd.Series, months: pd.DatetimeIndex) -> pd.Series:
    """A rule anyone can check: same month last year x growth of the last 12 months.

    Growth is the last 12 months against the 12 before them, all before month
    t, so the reference reads exactly what the model may read.
    """
    values: dict[pd.Timestamp, float] = {}
    for month in months:
        history = revenue.loc[: month - pd.DateOffset(months=1)]
        if len(history) < 24:
            raise sf.SalesDataError(f"{month:%Y-%m}: fewer than 24 months of history")
        growth = float(history.iloc[-12:].sum() / history.iloc[-24:-12].sum())
        values[pd.Timestamp(month)] = float(revenue.loc[month - pd.DateOffset(years=1)]) * growth
    return pd.Series(values, name="naive_eur")


def _predict_eur(model: Pipeline, rows: pd.DataFrame, columns: list[str]) -> pd.Series:
    ratio = model.predict(rows[columns])
    return pd.Series(ratio * rows["base"].to_numpy(), index=rows.index, name="forecast_eur")


def _prefixed(prefix: str, metrics: Mapping[str, float]) -> dict[str, float]:
    return {f"{prefix}_{name}": value for name, value in metrics.items()}


def evaluate_fold(
    revenue: pd.Series,
    fold: Fold,
    params: Mapping[str, Any],
    feature_columns: Sequence[str] = sf.FEATURE_COLUMNS,
) -> dict[str, Any]:
    """Fit on the fold's training months and score training and validation.

    The pipeline (scaler + XGBoost) is fitted on the fold's training rows only.
    Returns MAE and RMSE for training and validation, the validation bias
    (overall and for Nov-Dec), the recursive-forecast error over the validation
    window and the seasonal-naive reference.
    """
    columns = list(feature_columns)
    train_rows, validation_rows = fold_rows(revenue, fold)
    model = sf.make_pipeline().set_params(**{f"model__{k}": v for k, v in params.items()})
    model.fit(train_rows[columns], train_rows["target_ratio"])

    fitted = _predict_eur(model, train_rows, columns)
    predicted = _predict_eur(model, validation_rows, columns)
    recursive = sf.forecast_recursive(
        model, revenue.loc[: fold.train_months[-1]], fold.validation_months, columns
    )
    naive = seasonal_naive_one_step(revenue, fold.validation_months)
    actual_train = train_rows[sf.TARGET]
    actual_validation = validation_rows[sf.TARGET]

    recursive_metrics = error_metrics(actual_validation, recursive)
    naive_metrics = error_metrics(actual_validation, naive)
    peak = pd.DatetimeIndex(actual_validation.index).month.isin(PEAK_MONTHS)
    peak_error = (predicted - actual_validation)[peak]
    return {
        **fold.describe(),
        **_prefixed("train", error_metrics(actual_train, fitted)),
        **_prefixed("validation", error_metrics(actual_validation, predicted)),
        "validation_recursive_mae_pct": recursive_metrics["mae_pct"],
        "validation_recursive_rmse_pct": recursive_metrics["rmse_pct"],
        "naive_validation_mae_pct": naive_metrics["mae_pct"],
        "naive_validation_rmse_pct": naive_metrics["rmse_pct"],
        "validation_peak_months": int(peak.sum()),
        "validation_peak_under_forecast": int((peak_error < 0).sum()),
        "validation_peak_bias_pct": (
            float(100 * peak_error.sum() / actual_validation[peak].sum()) if peak.any() else None
        ),
    }


@dataclass(frozen=True, eq=False)
class CrossValidation:
    per_fold: pd.DataFrame
    summary: dict[str, dict[str, float]]

    def mean(self, metric: str) -> float:
        return self.summary[metric]["mean"]

    def std(self, metric: str) -> float:
        return self.summary[metric]["std"]

    @property
    def gap_ratio(self) -> float:
        """Validation RMSE over training RMSE (both % of revenue, fold means)."""
        return self.mean("validation_rmse_pct") / self.mean("train_rmse_pct")

    @property
    def folds_beating_naive(self) -> int:
        frame = self.per_fold
        return int((frame["validation_rmse_pct"] < frame["naive_validation_rmse_pct"]).sum())


def cross_validate(
    revenue: pd.Series,
    folds: Sequence[Fold],
    params: Mapping[str, Any],
    feature_columns: Sequence[str] = sf.FEATURE_COLUMNS,
) -> CrossValidation:
    """Score every fold and summarise each metric as mean +/- std (ddof=1) across folds."""
    per_fold = pd.DataFrame([evaluate_fold(revenue, f, params, feature_columns) for f in folds])
    summary = {
        metric: {
            "mean": float(per_fold[metric].mean()),
            "std": float(per_fold[metric].std(ddof=1)),
        }
        for metric in SUMMARY_METRICS
    }
    return CrossValidation(per_fold=per_fold, summary=summary)


# ------------------------------------------------------------ learning curves


def learning_curve_cv_windows(
    revenue: pd.Series,
    params: Mapping[str, Any],
    sizes: Sequence[int] = CURVE_SIZES_CV_WINDOWS,
    feature_columns: Sequence[str] = sf.FEATURE_COLUMNS,
) -> pd.DataFrame:
    """Training and validation error as the training window grows, on the CV windows.

    The five validation windows stay the same at every size; each fold trains
    on the `size` months right before its window (`TimeSeriesSplit` with
    `max_train_size`), so the only thing that changes is how much history the
    model sees. Mean and std are across the five folds.
    """
    rows: list[dict[str, Any]] = []
    for size in sizes:
        folds = temporal_folds(revenue, max_train_size=size)
        short = [f.number for f in folds if len(f.train_months) != size]
        if short:
            raise sf.SalesDataError(f"{size} training months do not fit before folds {short}")
        scores = pd.DataFrame([evaluate_fold(revenue, f, params, feature_columns) for f in folds])
        row: dict[str, Any] = {"train_months": size, "folds": len(folds)}
        for metric in CURVE_METRICS:
            row[f"{metric}_mean"] = float(scores[metric].mean())
            row[f"{metric}_std"] = float(scores[metric].std(ddof=1))
        rows.append(row)
    return pd.DataFrame(rows)


def learning_curve_fixed_year(
    revenue: pd.Series,
    params: Mapping[str, Any],
    sizes: Sequence[int] = CURVE_SIZES_FIXED_YEAR,
    feature_columns: Sequence[str] = sf.FEATURE_COLUMNS,
) -> pd.DataFrame:
    """Training and validation error as training grows, on the last training year.

    Validation is the last 12 training months, a full year with both February
    and Nov-Dec. Training is the `size` usable months right before it, so the
    curve reaches 48 months, close to the 60 the final model trains on.
    """
    months = usable_months(revenue)
    year = int(months[-1].year)
    validation = pd.DatetimeIndex(months[months.year == year])
    before = pd.DatetimeIndex(months[months < validation[0]])
    rows: list[dict[str, Any]] = []
    for size in sizes:
        if size > len(before):
            raise sf.SalesDataError(
                f"only {len(before)} usable months precede {year}; {size} asked"
            )
        fold = Fold(0, pd.DatetimeIndex(before[-size:]), validation)
        assert_chronological([fold])
        score = evaluate_fold(revenue, fold, params, feature_columns)
        rows.append(
            {
                "train_months": size,
                "train_first": score["train_first"],
                "validation_year": year,
                **{metric: score[metric] for metric in CURVE_METRICS},
            }
        )
    return pd.DataFrame(rows)


# ----------------------------------------------------------------- diagnosis


def classify_fit(cv_gap_ratio: float, curve_gap_ratio: float, beats_naive: bool) -> str:
    """The rule behind the verdict, kept small so it can be read and tested.

    - overfitting: validation error is at least twice the training error both
      in cross-validation and at the largest size of the learning curve;
    - underfitting: validation and training errors are close (gap under 1.5x)
      and the model does not beat the seasonal-naive rule, so it has not even
      learned what a simple rule knows;
    - well fitted: anything else.
    """
    if cv_gap_ratio >= OVERFIT_GAP_RATIO and curve_gap_ratio >= OVERFIT_GAP_RATIO:
        return "overfitting"
    if cv_gap_ratio < UNDERFIT_GAP_RATIO and not beats_naive:
        return "underfitting"
    return "well fitted"


@dataclass(frozen=True)
class Diagnosis:
    label: str
    checks: dict[str, Any]


def diagnose(cv: CrossValidation, fixed_year_curve: pd.DataFrame) -> Diagnosis:
    last = fixed_year_curve.iloc[-1]
    previous = fixed_year_curve.iloc[-2]
    curve_gap = float(last["validation_rmse_pct"] / last["train_rmse_pct"])
    improvement = float(1 - last["validation_rmse_pct"] / previous["validation_rmse_pct"])
    model_rmse = cv.mean("validation_rmse_pct")
    naive_rmse = cv.mean("naive_validation_rmse_pct")
    beats_naive = model_rmse < naive_rmse
    label = classify_fit(cv.gap_ratio, curve_gap, beats_naive)
    return Diagnosis(
        label=label,
        checks={
            "cv_gap_ratio": {
                "value": cv.gap_ratio,
                "overfit_threshold": OVERFIT_GAP_RATIO,
                "underfit_threshold": UNDERFIT_GAP_RATIO,
            },
            "learning_curve_gap_ratio_at_largest_size": {
                "value": curve_gap,
                "train_months": int(last["train_months"]),
                "overfit_threshold": OVERFIT_GAP_RATIO,
            },
            "learning_curve_last_step": {
                "from_train_months": int(previous["train_months"]),
                "to_train_months": int(last["train_months"]),
                "validation_rmse_improvement_pct": 100 * improvement,
                "still_improving": improvement >= CONVERGED_TOLERANCE,
                "threshold_pct": 100 * CONVERGED_TOLERANCE,
            },
            "beats_seasonal_naive": {
                "value": beats_naive,
                "model_validation_rmse_pct": model_rmse,
                "naive_validation_rmse_pct": naive_rmse,
                "folds_beating_naive": cv.folds_beating_naive,
                "folds": len(cv.per_fold),
            },
        },
    )


# --------------------------------------------------------- corrective action


def candidate_settings(
    shipped_params: Mapping[str, Any],
) -> dict[str, tuple[dict[str, Any], tuple[str, ...]]]:
    """Each candidate's full parameter set (shipped + overrides) and its features."""
    return {
        name: ({**shipped_params, **overrides}, columns)
        for name, (overrides, columns) in CANDIDATES.items()
    }


def select_corrective_action(results: Mapping[str, CrossValidation]) -> str:
    """Lowest mean validation RMSE; within `TIE_TOLERANCE_PP` of it, the lowest std."""
    lowest = min(r.mean("validation_rmse_pct") for r in results.values())
    tied = [
        name
        for name, r in results.items()
        if r.mean("validation_rmse_pct") - lowest <= TIE_TOLERANCE_PP
    ]
    return min(
        tied,
        key=lambda name: (
            results[name].std("validation_rmse_pct"),
            results[name].mean("validation_rmse_pct"),
        ),
    )
