"""Sales forecast: the 8/2-year split, no leakage, cleaning and metrics.

The data is synthetic and built in memory with the briefing's shape and
patterns (`contexts/trackflow-sales-briefing.md`): 120 consolidated months
2016-01..2025-12, growth alternating 9 % / 3 %, Nov-Dec peak, February dip.

    python -m pytest tests/pipelines/test_sales_forecast.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts import sales_forecast as sf
from scripts import train_sales_forecast as cli

COMPANY_DATASET = cli.DEFAULT_DATA

SEASONAL = {
    1: 1.0,
    2: 0.87,
    3: 0.98,
    4: 1.0,
    5: 1.02,
    6: 0.99,
    7: 1.0,
    8: 0.97,
    9: 1.01,
    10: 1.03,
    11: 1.28,
    12: 1.33,
}


def synthetic_revenue(start: str = "2016-01-01", years: int = 10) -> pd.Series:
    rng = np.random.default_rng(42)
    months = pd.date_range(start, periods=12 * years, freq="MS")
    level, values = 600_000.0, []
    for i, month in enumerate(months):
        if i and month.month == 1:
            level *= 1.09 if (month.year % 2) else 1.03
        values.append(level * SEASONAL[month.month] * (1 + rng.normal(0, 0.015)))
    return pd.Series(
        values, index=pd.DatetimeIndex(months, freq="MS", name="month"), name="revenue_eur"
    )


def to_csv_frame(revenue: pd.Series) -> pd.DataFrame:
    avg = 12.5
    return pd.DataFrame(
        {
            "month": revenue.index.strftime("%Y-%m-%d"),
            "revenue_eur": revenue.round(2).to_numpy(),
            "shipments_processed": (revenue / avg).round().astype(int).to_numpy(),
            "avg_revenue_per_shipment_eur": avg,
            "market": "consolidated",
        }
    )


@pytest.fixture
def revenue() -> pd.Series:
    return synthetic_revenue()


# ------------------------------------------------------------------ 8/2 split


def test_split_uses_first_8_years_for_training_and_last_2_for_test(revenue: pd.Series) -> None:
    split = sf.split_train_test(revenue)

    assert split.train_years == tuple(range(2016, 2024))
    assert split.test_years == (2024, 2025)
    assert len(split.train) == 96
    assert len(split.test) == 24
    assert split.train.index.min() == pd.Timestamp("2016-01-01")
    assert split.train.index.max() == pd.Timestamp("2023-12-01")
    assert split.test.index.min() == pd.Timestamp("2024-01-01")
    assert split.test.index.max() == pd.Timestamp("2025-12-01")


def test_split_sets_are_disjoint_ordered_and_cover_every_month(revenue: pd.Series) -> None:
    split = sf.split_train_test(revenue)

    assert split.train.index.intersection(split.test.index).empty
    assert split.train.index.max() < split.test.index.min()
    assert split.train.index.append(split.test.index).equals(revenue.index)


@pytest.mark.skipif(not COMPANY_DATASET.exists(), reason="company dataset not present")
def test_company_dataset_splits_8_2_without_leakage() -> None:
    """The rule on TrackFlow's real file, not only on synthetic data."""
    clean, _ = sf.clean_sales(sf.load_sales(COMPANY_DATASET))
    revenue_all = sf.consolidated_series(clean)
    split = sf.split_train_test(revenue_all)
    x_train, _ = sf.training_rows(split.train[sf.TARGET])

    assert (revenue_all.index.min(), revenue_all.index.max()) == (
        pd.Timestamp("2016-01-01"),
        pd.Timestamp("2025-12-01"),
    )
    assert split.train_years == tuple(range(2016, 2024))
    assert split.test_years == (2024, 2025)
    assert (len(split.train), len(split.test)) == (96, 24)
    assert split.train.index.max() < split.test.index.min()
    assert x_train.index.intersection(split.test.index).empty


def test_split_ignores_row_order(revenue: pd.Series) -> None:
    shuffled = revenue.sample(frac=1, random_state=0)
    split = sf.split_train_test(shuffled.sort_index())

    assert split.test_years == (2024, 2025)


@pytest.mark.parametrize("years", [9, 11])
def test_split_refuses_a_dataset_that_is_not_ten_years(years: int) -> None:
    with pytest.raises(sf.SalesDataError, match="expected 10 years"):
        sf.split_train_test(synthetic_revenue(years=years))


def test_split_refuses_an_incomplete_year(revenue: pd.Series) -> None:
    with pytest.raises(sf.SalesDataError, match="incomplete calendar years"):
        sf.split_train_test(revenue.drop(pd.Timestamp("2025-12-01")))


# ------------------------------------------------------------------- leakage


def test_features_for_a_month_never_read_that_month_or_later(revenue: pd.Series) -> None:
    features = sf.build_features(revenue)
    cut = pd.Timestamp("2024-01-01")
    altered = revenue.copy()
    altered[altered.index >= cut] *= 5

    altered_features = sf.build_features(altered)
    columns = ["base", *sf.FEATURE_COLUMNS]
    pd.testing.assert_frame_equal(features.loc[:cut, columns], altered_features.loc[:cut, columns])


def test_training_rows_come_only_from_training_years(revenue: pd.Series) -> None:
    split = sf.split_train_test(revenue)
    x, _ = sf.training_rows(split.train[sf.TARGET])

    assert x.index.max() <= pd.Timestamp("2023-12-01")
    assert x.index.intersection(split.test.index).empty
    assert x.index.min() == pd.Timestamp("2019-01-01")
    assert not x.isna().any().any()


def test_forecast_is_identical_whatever_the_test_years_contain(
    revenue: pd.Series, tmp_path: Path
) -> None:
    """End to end: scaling every 2024-2025 value by 3 must not move the forecast."""
    original = to_csv_frame(revenue)
    tampered = original.copy()
    test_rows = tampered["month"] >= "2024-01-01"
    tampered.loc[test_rows, "revenue_eur"] *= 3
    tampered.loc[test_rows, "shipments_processed"] *= 3

    for name, frame in (("original", original), ("tampered", tampered)):
        frame.to_csv(tmp_path / f"{name}.csv", index=False)
        cli.run(tmp_path / f"{name}.csv", tmp_path / name, tune=False)

    a = pd.read_csv(tmp_path / "original" / "forecast_test_years.csv")
    b = pd.read_csv(tmp_path / "tampered" / "forecast_test_years.csv")
    pd.testing.assert_series_equal(a["forecast_eur"], b["forecast_eur"])
    pd.testing.assert_series_equal(a["lower_eur"], b["lower_eur"])
    pd.testing.assert_series_equal(a["baseline_eur"], b["baseline_eur"])


def test_variability_band_is_built_from_training_years_only(revenue: pd.Series) -> None:
    train = sf.split_train_test(revenue).train[sf.TARGET]
    errors = sf.backtest_relative_errors(
        train, {"max_depth": 2, "n_estimators": 50, "learning_rate": 0.1}
    )

    assert errors["month"].max() <= pd.Timestamp("2023-12-01")
    assert sorted(errors["origin_year"].unique()) == [2019, 2020, 2021]


# ------------------------------------------------------------------ cleaning


def test_load_refuses_columns_that_differ_from_the_briefing(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    pd.DataFrame({"date": ["2016-01-01"], "sales": [1.0]}).to_csv(path, index=False)

    with pytest.raises(sf.SalesDataError, match="columns do not match"):
        sf.load_sales(path)


def test_cleaning_drops_empty_rows_and_derives_missing_figures(revenue: pd.Series) -> None:
    frame = to_csv_frame(revenue).astype(str)
    frame.loc[3, "revenue_eur"] = ""
    frame.loc[4, "shipments_processed"] = " "
    frame.loc[5, "avg_revenue_per_shipment_eur"] = "nan"
    frame = pd.concat([frame, pd.DataFrame([dict.fromkeys(frame.columns, "")])], ignore_index=True)
    frame.loc[7, "market"] = " Consolidated "

    clean, report = sf.clean_sales(frame)
    series = sf.consolidated_series(clean)

    assert report.empty_rows_dropped == 1
    assert (report.revenue_derived, report.shipments_derived, report.avg_revenue_derived) == (
        1,
        1,
        1,
    )
    assert len(series) == 120
    assert series.iloc[3] == pytest.approx(revenue.iloc[3], rel=0.01)
    assert not clean.isna().any().any()


def test_cleaning_refuses_a_target_it_cannot_derive(revenue: pd.Series) -> None:
    frame = to_csv_frame(revenue).astype(str)
    frame.loc[10, ["revenue_eur", "shipments_processed"]] = ""

    with pytest.raises(sf.SalesDataError, match="cannot be derived"):
        sf.clean_sales(frame)


def test_a_missing_month_is_refused(revenue: pd.Series) -> None:
    frame = to_csv_frame(revenue).drop(index=30).astype(str)
    clean, _ = sf.clean_sales(frame)

    with pytest.raises(sf.SalesDataError, match="missing months"):
        sf.consolidated_series(clean)


def test_non_positive_revenue_is_refused(revenue: pd.Series) -> None:
    frame = to_csv_frame(revenue).astype(str)
    frame.loc[12, "revenue_eur"] = "-5"
    clean, _ = sf.clean_sales(frame)

    with pytest.raises(sf.SalesDataError, match="must be positive"):
        sf.consolidated_series(clean)


def test_unknown_market_is_refused(revenue: pd.Series) -> None:
    frame = to_csv_frame(revenue).astype(str)
    frame.loc[0, "market"] = "mexico"

    with pytest.raises(sf.SalesDataError, match="market must be one of"):
        sf.clean_sales(frame)


# ------------------------------------------------------------------- metrics


def test_normalized_gini_rewards_ranking_not_scale() -> None:
    actual = np.array([10.0, 20.0, 30.0, 40.0])

    assert sf.normalized_gini(actual, actual) == pytest.approx(1.0)
    assert sf.normalized_gini(actual, actual * 7 + 3) == pytest.approx(1.0)
    assert sf.normalized_gini(actual, -actual) < 0


def test_psi_is_zero_for_the_same_distribution_and_grows_with_a_shift() -> None:
    rng = np.random.default_rng(0)
    base = rng.normal(1, 0.1, 200)

    assert sf.psi(base, base) == pytest.approx(0, abs=1e-9)
    assert sf.psi(base, base + 0.3) > 0.25


def test_psi_p_value_separates_sampling_noise_from_a_real_shift() -> None:
    rng = np.random.default_rng(1)
    train = rng.normal(1, 0.1, 60)

    same = rng.normal(1, 0.1, 24)
    shifted = rng.normal(1.3, 0.1, 24)

    assert sf.psi_shift_p_value(train, same, permutations=500) >= 0.05
    assert sf.psi_shift_p_value(train, shifted, permutations=500) < 0.05


def test_regression_metrics_report_mse_in_eur_and_as_share_of_revenue() -> None:
    idx = pd.date_range("2024-01-01", periods=2, freq="MS")
    actual = pd.Series([100.0, 200.0], index=idx)
    predicted = pd.Series([110.0, 190.0], index=idx)

    metrics = sf.regression_metrics(actual, predicted)

    assert metrics["mse_eur2"] == pytest.approx(100.0)
    assert metrics["rmse_eur"] == pytest.approx(10.0)
    assert metrics["rmse_pct_of_mean_monthly_revenue"] == pytest.approx(100 * 10 / 150)
    assert "k2_score_r2" in metrics and "gini_normalized" in metrics


def test_full_run_writes_metrics_plot_and_beats_nothing_silently(
    revenue: pd.Series, tmp_path: Path
) -> None:
    path = tmp_path / "sales.csv"
    to_csv_frame(revenue).to_csv(path, index=False)

    result = cli.run(path, tmp_path / "out", tune=False)

    assert (tmp_path / "out" / "forecast_test_years.png").stat().st_size > 0
    stored = json.loads((tmp_path / "out" / "metrics.json").read_text())
    assert stored["split"]["test_years"] == [2024, 2025]
    model = result["metrics_test"]["model"]
    assert set(model) >= {"mse_eur2", "k2_score_r2", "gini_normalized"}
    assert result["psi"]["seasonal_ratio_train_vs_test"] >= 0
    assert 0 <= result["psi"]["p_value_vs_no_shift"]["seasonal_ratio_train_vs_test"] <= 1
    assert all(result["data_profile"]["checks"].values())
    # On data with the briefing's patterns the forecast should be well inside 10 %.
    assert model["mape_pct"] < 10


def test_market_rows_add_a_mix_psi_without_mislabelling_the_shares(
    revenue: pd.Series, tmp_path: Path
) -> None:
    consolidated = to_csv_frame(revenue)
    markets = [
        consolidated.assign(market=name, revenue_eur=consolidated["revenue_eur"] * share)
        for name, share in (("us", sf.US_SHARE_REFERENCE), ("spain", 1 - sf.US_SHARE_REFERENCE))
    ]
    path = tmp_path / "sales.csv"
    pd.concat([consolidated, *markets]).to_csv(path, index=False)

    psi = cli.run(path, tmp_path / "out", tune=False)["psi"]

    assert set(psi["labels"]) == {
        "seasonal_ratio_train_vs_test",
        "model_ratio_fitted_train_vs_forecast_test",
        "us_spain_mix_train_vs_test",
    }
    assert psi["us_share_mean_test"] == pytest.approx(sf.US_SHARE_REFERENCE)
