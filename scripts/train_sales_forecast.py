"""Train and evaluate TrackFlow's monthly revenue forecast (sales prediction ticket).

    uv run python scripts/train_sales_forecast.py
    uv run python scripts/train_sales_forecast.py --data data/raw/trackflow_sales.csv \
        --output-dir data/eval/sales_forecast

Trains XGBoost on the first 8 calendar years, forecasts the last 2 recursively
(the model never sees a measured 2024-2025 month, not even as a lag), and writes
to the output directory:

- `metrics.json`: data profile, cleaning report, split, chosen parameters,
  MSE / PSI / Gini / K2 (R²) for the model and for a seasonal-naive baseline;
- `forecast_test_years.csv`: actual, forecast, 80% range and baseline per month;
- `forecast_test_years.png`: the forecast and its band against the 2 test years.

Exit codes: 0 success, 2 the dataset breaks the briefing's contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts import sales_forecast as sf

# The path the briefing names (contexts/trackflow-sales-briefing.md, section 2).
DEFAULT_DATA = ROOT_DIR / "data" / "raw" / "trackflow_sales.csv"
DEFAULT_OUTPUT = ROOT_DIR / "data" / "eval" / "sales_forecast"

FLOW_BLUE = "#2563EB"
SOFT_BLUE = "#82A2E5"
CREAM_ORANGE = "#FF9142"
MUTED = "#6B7280"


def repo_relative(path: Path) -> str:
    """Repo-relative when possible, so committed outputs carry no local home path."""
    try:
        return str(path.resolve().relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def run(data_path: Path, output_dir: Path, tune: bool = True) -> dict[str, Any]:
    raw = sf.load_sales(data_path)
    clean, cleaning = sf.clean_sales(raw)
    revenue = sf.consolidated_series(clean)
    split = sf.split_train_test(revenue)
    train = split.train[sf.TARGET]
    test = split.test[sf.TARGET]

    model, fit_info = sf.fit_model(train, tune=tune)
    forecast = sf.forecast_recursive(model, train, test.index)
    baseline = sf.seasonal_naive_forecast(train, test.index)
    errors = sf.backtest_relative_errors(train, fit_info["params"])
    band = sf.variability_band(forecast, errors)

    x_train, y_train = sf.training_rows(train)
    fitted_ratio = model.predict(x_train)
    test_ratio_actual = sf.build_features(revenue).loc[test.index, "target_ratio"]
    test_base = sf.build_features(pd.concat([train, forecast]))["base"].loc[test.index]
    forecast_ratio = forecast / test_base

    model_metrics = sf.regression_metrics(test, forecast)
    baseline_metrics = sf.regression_metrics(test, baseline)
    coverage = float(((test >= band["lower_eur"]) & (test <= band["upper_eur"])).mean())

    psi_inputs: dict[str, tuple[Any, Any]] = {
        "seasonal_ratio_train_vs_test": (y_train, test_ratio_actual),
        "model_ratio_fitted_train_vs_forecast_test": (fitted_ratio, forecast_ratio),
    }
    psi_values: dict[str, Any] = {}
    mix = sf.market_mix(clean)
    if mix is None:
        psi_values["us_spain_mix_train_vs_test"] = None
        psi_values["us_spain_mix_note"] = (
            "Only 'consolidated' rows present; the US/Spain (~60/40) mix cannot be measured."
        )
    else:
        train_mix = mix[mix.index.year.isin(split.train_years)]
        test_mix = mix[mix.index.year.isin(split.test_years)]
        psi_inputs["us_spain_mix_train_vs_test"] = (train_mix, test_mix)
        psi_values["us_share_mean_train"] = float(train_mix.mean())
        psi_values["us_share_mean_test"] = float(test_mix.mean())
    for name, (expected, actual) in psi_inputs.items():
        psi_values[name] = sf.psi(expected, actual)
    psi_values["labels"] = {name: sf.psi_label(psi_values[name]) for name in psi_inputs}
    psi_values["p_value_vs_no_shift"] = {
        name: sf.psi_shift_p_value(expected, actual)
        for name, (expected, actual) in psi_inputs.items()
    }
    psi_values["p_value_note"] = (
        "Share of 2000 random train/test re-splits of the pooled values with a PSI at least "
        "as large. >= 0.05: the shift is within sampling noise for this many months, "
        "whatever the rule-of-thumb label says."
    )

    importances = model.named_steps["model"].feature_importances_
    feature_importance = dict(
        sorted(
            zip(sf.FEATURE_COLUMNS, (float(v) for v in importances), strict=True),
            key=lambda item: item[1],
            reverse=True,
        )
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    table = band.assign(actual_eur=test, baseline_eur=baseline)
    table.index = table.index.strftime("%Y-%m")
    table.round(2).to_csv(output_dir / "forecast_test_years.csv", index_label="month")
    plot_forecast(train, test, band, baseline, output_dir / "forecast_test_years.png")

    result: dict[str, Any] = {
        "dataset": repo_relative(data_path),
        "data_profile": sf.data_profile(revenue),
        "cleaning": cleaning.as_dict(),
        "split": {
            "train_years": list(split.train_years),
            "test_years": list(split.test_years),
            "train_months": len(train),
            "test_months": len(test),
            "train_rows_after_warmup": len(x_train),
        },
        "algorithm": "XGBoost (XGBRegressor) on revenue / trailing-12-month mean",
        "fit": fit_info,
        "forecast_mode": "recursive, 24 months, no measured test month used",
        "metrics_test": {"model": model_metrics, "seasonal_naive_baseline": baseline_metrics},
        "psi": psi_values,
        "variability_band": {
            "method": (
                "80% range: forecast x (1 +/- P80 of absolute relative error) from walk-forward "
                "recursive backtests inside the training years"
            ),
            "backtest_origins": sorted(int(y) for y in errors["origin_year"].unique()),
            "half_width_pct_by_horizon_year": {
                int(h): round(100 * g.abs().quantile(0.80), 2)
                for h, g in errors.groupby("horizon_year")["relative_error"]
            },
            "backtest_mean_error_pct_by_horizon_year": {
                int(h): round(100 * g.mean(), 2)
                for h, g in errors.groupby("horizon_year")["relative_error"]
            },
            "test_months_inside_band_pct": round(100 * coverage, 1),
        },
        "feature_importance_gain": feature_importance,
    }
    (output_dir / "metrics.json").write_text(json.dumps(result, indent=2, default=str))
    return result


def plot_forecast(
    train: pd.Series, test: pd.Series, band: pd.DataFrame, baseline: pd.Series, path: Path
) -> None:
    context = train.iloc[-24:]
    fig = Figure(figsize=(12, 5.5))
    ax = fig.subplots()
    ax.plot(context.index, context, color=MUTED, lw=1.5, label="Actual (last 2 training years)")
    ax.fill_between(
        band.index,
        band["lower_eur"],
        band["upper_eur"],
        color=SOFT_BLUE,
        alpha=0.35,
        label="Variability range (80%)",
    )
    ax.plot(
        test.index, test, color=FLOW_BLUE, lw=2.2, marker="o", ms=4, label="Actual (test years)"
    )
    ax.plot(
        band.index,
        band["forecast_eur"],
        color=CREAM_ORANGE,
        lw=2.2,
        marker="o",
        ms=4,
        label="XGBoost forecast (recursive)",
    )
    ax.plot(
        baseline.index,
        baseline,
        color=MUTED,
        lw=1.2,
        ls="--",
        label="Baseline: same month last year x growth",
    )
    ax.axvline(test.index.min(), color=MUTED, lw=1, ls=":")
    ax.text(test.index.min(), ax.get_ylim()[1], "  test years start", va="top", color=MUTED)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"€{v / 1e3:,.0f}k"))
    ax.set_title(
        f"TrackFlow monthly revenue: forecast vs actual, {test.index.min().year}-"
        f"{test.index.max().year}"
    )
    ax.set_ylabel("Revenue (EUR, consolidated)")
    ax.grid(alpha=0.25)
    ax.legend(loc="upper left", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=150)


def summary(result: dict[str, Any]) -> str:
    m = result["metrics_test"]["model"]
    b = result["metrics_test"]["seasonal_naive_baseline"]
    band = result["variability_band"]
    psi = result["psi"]
    train_years, test_years = result["split"]["train_years"], result["split"]["test_years"]
    psi_text = ", ".join(
        f"{name}={psi[name]:.3f} ({label}; p={psi['p_value_vs_no_shift'][name]:.2f}"
        f"{', within noise' if psi['p_value_vs_no_shift'][name] >= 0.05 else ''})"
        for name, label in psi["labels"].items()
    )
    lines = [
        f"Train {train_years[0]}-{train_years[-1]}, test {test_years[0]}-{test_years[-1]}",
        f"Params: {result['fit']['params']}",
        (
            f"MSE   {m['mse_eur2']:,.0f} EUR^2   RMSE {m['rmse_eur']:,.0f} EUR "
            f"= {m['rmse_pct_of_mean_monthly_revenue']:.2f}% of mean monthly revenue "
            f"(baseline {b['rmse_pct_of_mean_monthly_revenue']:.2f}%)"
        ),
        f"MAPE  {m['mape_pct']:.2f}%   (baseline {b['mape_pct']:.2f}%)",
        f"K2/R2 {m['k2_score_r2']:.3f}   (baseline {b['k2_score_r2']:.3f})",
        f"Gini  {m['gini_normalized']:.3f}   (baseline {b['gini_normalized']:.3f})",
        f"PSI   {psi_text}",
        f"Band  {band['test_months_inside_band_pct']}% of test months inside the 80% range",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--no-tune", action="store_true", help="skip parameter search, use the default set"
    )
    args = parser.parse_args(argv)
    try:
        result = run(args.data, args.output_dir, tune=not args.no_tune)
    except (sf.SalesDataError, FileNotFoundError) as exc:
        print(f"dataset error: {exc}", file=sys.stderr)
        return 2
    print(summary(result))
    print(f"Outputs written to {args.output_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
