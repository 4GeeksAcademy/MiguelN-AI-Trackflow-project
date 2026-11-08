"""Sales forecast evaluation: time-aware CV, fold-boundary leakage, learning curves, diagnosis.

The data is the synthetic, briefing-shaped series of `test_sales_forecast.py`:
120 consolidated months 2016-01..2025-12, growth alternating 9 % / 3 %, Nov-Dec
peak, February dip. Only its 8 training years reach the evaluation.

    python -m pytest tests/pipelines/test_sales_forecast_evaluation.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts import evaluate_sales_forecast as cli
from scripts import sales_forecast as sf
from scripts import sales_forecast_evaluation as ev
from tests.pipelines.test_sales_forecast import synthetic_revenue, to_csv_frame

# Small and fast; the rules under test do not depend on the parameters.
PARAMS = {"max_depth": 2, "n_estimators": 50, "learning_rate": 0.1}


@pytest.fixture
def train() -> pd.Series:
    return sf.split_train_test(synthetic_revenue()).train[sf.TARGET]


def is_strictly_increasing(months: pd.DatetimeIndex) -> bool:
    values = list(months)
    return all(earlier < later for earlier, later in zip(values, values[1:], strict=False))


# ------------------------------------------------------ chronology (required)


def test_temporal_folds_preserve_chronological_order(train: pd.Series) -> None:
    """No fold mixes or shuffles months, and no later fold reaches back before an earlier one."""
    folds = ev.temporal_folds(train)

    assert len(folds) >= 5
    first_training_month = folds[0].train_months[0]
    previous_validation_end: pd.Timestamp | None = None
    for fold in folds:
        assert is_strictly_increasing(fold.train_months)
        assert is_strictly_increasing(fold.validation_months)
        assert fold.train_months.max() < fold.validation_months.min()
        # Validation starts the month right after training ends: no gap, no overlap.
        assert fold.validation_months[0] == fold.train_months[-1] + pd.DateOffset(months=1)
        # Expanding window: every fold trains from the same first usable month.
        assert fold.train_months[0] == first_training_month
        if previous_validation_end is not None:
            assert fold.validation_months.min() > previous_validation_end
        previous_validation_end = fold.validation_months.max()

    # Read fold after fold, the validation months form one increasing sequence.
    every_validation_month = [m for fold in folds for m in fold.validation_months]
    assert is_strictly_increasing(pd.DatetimeIndex(every_validation_month))


def test_folds_stay_inside_the_training_years_after_the_warm_up(train: pd.Series) -> None:
    folds = ev.temporal_folds(train)

    assert [len(f.train_months) for f in folds] == [30, 36, 42, 48, 54]
    assert all(len(f.validation_months) == 6 for f in folds)
    assert folds[0].train_months[0] == pd.Timestamp("2019-01-01")
    assert folds[-1].validation_months[-1] == pd.Timestamp("2023-12-01")


def test_assert_chronological_refuses_shuffled_reordered_or_gapped_folds(
    train: pd.Series,
) -> None:
    folds = ev.temporal_folds(train)
    first = folds[0]

    broken = {
        "reversed training": [ev.Fold(1, first.train_months[::-1], first.validation_months)],
        "folds out of order": [folds[1], folds[0]],
        "gap before validation": [ev.Fold(1, first.train_months[:-1], first.validation_months)],
        "overlap": [
            ev.Fold(
                1,
                first.train_months,
                pd.DatetimeIndex(first.train_months[-2:].append(first.validation_months)),
            )
        ],
    }
    for case, bad_folds in broken.items():
        with pytest.raises(ev.EvaluationError):
            ev.assert_chronological(bad_folds)
            pytest.fail(f"accepted: {case}")


# ------------------------------------------------------------------- leakage


def test_features_do_not_read_across_any_fold_boundary(train: pd.Series) -> None:
    """Even features computed on the full series before splitting stay on their side."""
    full = sf.build_features(train)
    columns = ["base", *sf.FEATURE_COLUMNS, "target_ratio"]
    for fold in ev.temporal_folds(train):
        tampered = train.copy()
        tampered[tampered.index > fold.train_months[-1]] *= 5

        pd.testing.assert_frame_equal(
            full.loc[fold.train_months, columns],
            sf.build_features(tampered).loc[fold.train_months, columns],
        )
        train_rows, _ = ev.fold_rows(train, fold)
        pd.testing.assert_frame_equal(train_rows[columns], full.loc[fold.train_months, columns])
        assert all(ev.check_fold_boundary_leakage(train, fold).values())


def test_leakage_check_catches_a_feature_that_reads_the_future(
    train: pd.Series, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: a centred window reads month t+1 and must be flagged."""
    fold = ev.temporal_folds(train)[0]
    original = sf.build_features

    def leaky(revenue: pd.Series) -> pd.DataFrame:
        out = original(revenue)
        centred = revenue.rolling(3, center=True, min_periods=1).mean()
        out["rolling_mean_3_ratio"] = centred / out["base"]
        return out

    monkeypatch.setattr(sf, "build_features", leaky)
    checks = ev.check_fold_boundary_leakage(train, fold)

    assert checks["training_rows_ignore_later_months"] is False
    assert checks["training_rows_match_full_series_build"] is False


# ------------------------------------------------------------ learning curves


def test_learning_curve_keeps_validation_fixed_and_trains_right_before_it(
    train: pd.Series,
) -> None:
    expanding = ev.temporal_folds(train)
    for size in (12, 24):
        sliding = ev.temporal_folds(train, max_train_size=size)
        for wide, narrow in zip(expanding, sliding, strict=True):
            assert narrow.validation_months.equals(wide.validation_months)
            assert len(narrow.train_months) == size
            assert narrow.train_months[-1] == wide.train_months[-1]

    curve = ev.learning_curve_cv_windows(train, PARAMS, sizes=(12, 24))
    assert curve["train_months"].tolist() == [12, 24]
    assert (curve["folds"] == 5).all()

    year = ev.learning_curve_fixed_year(train, PARAMS, sizes=(12, 48))
    assert year["validation_year"].tolist() == [2023, 2023]
    assert year["train_first"].tolist() == ["2022-01", "2019-01"]


def test_learning_curve_refuses_a_size_the_smallest_fold_cannot_hold(train: pd.Series) -> None:
    with pytest.raises(sf.SalesDataError):
        ev.learning_curve_cv_windows(train, PARAMS, sizes=(31,))


# ------------------------------------------------------------------- metrics


def test_cross_validation_reports_mae_and_rmse_for_training_and_validation(
    train: pd.Series,
) -> None:
    result = ev.cross_validate(train, ev.temporal_folds(train), PARAMS)

    assert len(result.per_fold) == 5
    for split in ("train", "validation"):
        for metric in ("mae_eur", "rmse_eur", "mae_pct", "rmse_pct"):
            key = f"{split}_{metric}"
            assert result.summary[key]["mean"] == pytest.approx(result.per_fold[key].mean())
            assert result.summary[key]["std"] == pytest.approx(result.per_fold[key].std(ddof=1))
    folds = result.per_fold
    assert (folds["validation_rmse_eur"] >= folds["validation_mae_eur"]).all()
    assert result.gap_ratio == pytest.approx(
        result.mean("validation_rmse_pct") / result.mean("train_rmse_pct")
    )


def test_error_metrics_report_eur_share_of_revenue_and_signed_bias() -> None:
    idx = pd.date_range("2023-01-01", periods=2, freq="MS")
    actual = pd.Series([100.0, 200.0], index=idx)

    over = ev.error_metrics(actual, pd.Series([110.0, 220.0], index=idx))
    under = ev.error_metrics(actual, pd.Series([90.0, 180.0], index=idx))

    assert over["mae_eur"] == pytest.approx(15.0)
    assert over["rmse_eur"] == pytest.approx(np.sqrt((10**2 + 20**2) / 2))
    assert over["mae_pct"] == pytest.approx(100 * 15 / 150)
    assert over["bias_pct"] == pytest.approx(10.0)
    assert under["bias_pct"] == pytest.approx(-10.0)


def test_seasonal_naive_reference_reads_only_earlier_months(train: pd.Series) -> None:
    months = pd.date_range("2023-01-01", periods=6, freq="MS")
    reference = ev.seasonal_naive_one_step(train, months)

    tampered = train.copy()
    tampered[tampered.index >= months[0]] *= 5
    first = ev.seasonal_naive_one_step(tampered, months[:1])

    assert reference.iloc[0] == pytest.approx(first.iloc[0])


# ------------------------------------------------- diagnosis and the action


@pytest.mark.parametrize(
    ("cv_gap", "curve_gap", "beats_naive", "label"),
    [
        (3.4, 3.4, True, "overfitting"),
        (3.4, 3.4, False, "overfitting"),
        (3.0, 1.8, True, "well fitted"),
        (1.2, 1.1, False, "underfitting"),
        (1.2, 1.1, True, "well fitted"),
        (1.7, 1.6, False, "well fitted"),
    ],
)
def test_classify_fit(cv_gap: float, curve_gap: float, beats_naive: bool, label: str) -> None:
    assert ev.classify_fit(cv_gap, curve_gap, beats_naive) == label


def _scored(mean: float, std: float) -> ev.CrossValidation:
    return ev.CrossValidation(
        per_fold=pd.DataFrame(), summary={"validation_rmse_pct": {"mean": mean, "std": std}}
    )


def test_corrective_action_is_the_lowest_error_and_ties_go_to_the_most_stable() -> None:
    assert ev.select_corrective_action({"a": _scored(4.5, 1.0), "b": _scored(4.0, 0.9)}) == "b"
    assert ev.select_corrective_action({"a": _scored(4.00, 0.9), "b": _scored(4.08, 0.4)}) == "b"
    assert ev.select_corrective_action({"a": _scored(4.00, 0.9), "b": _scored(4.20, 0.4)}) == "a"


def test_compact_feature_set_accounts_for_every_dropped_column() -> None:
    assert set(ev.COMPACT_FEATURES) | set(ev.DROPPED_FROM_COMPACT) == set(sf.FEATURE_COLUMNS)
    assert not set(ev.COMPACT_FEATURES) & set(ev.DROPPED_FROM_COMPACT)
    assert set(ev.CANDIDATES) == set(ev.CANDIDATE_DESCRIPTIONS)


# ----------------------------------------------------------------- end to end


def test_evaluation_never_reads_the_test_years(tmp_path: Path) -> None:
    """Multiplying every 2024-2025 value by 3 must not change a single number."""
    original = to_csv_frame(synthetic_revenue())
    tampered = original.copy()
    test_rows = tampered["month"] >= "2024-01-01"
    tampered.loc[test_rows, "revenue_eur"] *= 3
    tampered.loc[test_rows, "shipments_processed"] *= 3

    results = {}
    for name, frame in (("original", original), ("tampered", tampered)):
        frame.to_csv(tmp_path / f"{name}.csv", index=False)
        result = cli.run(tmp_path / f"{name}.csv", tmp_path / name, tune=False)
        result.pop("dataset")
        results[name] = result

    assert results["original"] == results["tampered"]
    out = tmp_path / "original"
    assert (out / cli.REPORT_NAME).stat().st_size > 0
    assert (out / cli.CHART_NAME).stat().st_size > 0
    assert (out / cli.JSON_NAME).stat().st_size > 0
    report = (out / cli.REPORT_NAME).read_text()
    assert f"## Verdict: **{results['original']['diagnosis']['label']}**" in report
    assert results["original"]["test_years_read"] is False
    assert results["original"]["corrective_action"]["applied"] is False


def test_cli_exits_3_when_a_leakage_check_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "sales.csv"
    to_csv_frame(synthetic_revenue()).to_csv(path, index=False)
    monkeypatch.setattr(
        ev,
        "check_fold_boundary_leakage",
        lambda revenue, fold: {"training_rows_ignore_later_months": False},
    )

    code = cli.main(["--data", str(path), "--output-dir", str(tmp_path / "out"), "--no-tune"])

    assert code == 3
    assert not (tmp_path / "out" / cli.REPORT_NAME).exists()
