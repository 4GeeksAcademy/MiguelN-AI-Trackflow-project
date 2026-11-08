"""Formal technical evaluation of TrackFlow's monthly revenue forecast (tech lead's ticket).

    .venv/bin/python scripts/evaluate_sales_forecast.py
    .venv/bin/python scripts/evaluate_sales_forecast.py --data data/raw/trackflow_sales.csv \
        --output-dir data/eval

Reads only the 8 training years (2016-2023) of `data/raw/trackflow_sales.csv`.
The 2024-2025 test years are never read. Runs time-aware cross-validation
(TimeSeriesSplit, 5 folds), checks fold chronology and fold-boundary leakage,
builds two learning curves, classifies the fit and scores the
corrective-action candidates. Writes to the output directory:

- `evaluation_report.md`: the technical report for the tech lead;
- `learning_curve.png`: training vs validation error as training grows;
- `sales_forecast/evaluation.json`: every number the report quotes.

Exit codes: 0 success, 2 the dataset breaks the briefing's contract,
3 a chronology or leakage check failed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from matplotlib.figure import Figure

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts import sales_forecast as sf
from scripts import sales_forecast_evaluation as ev

DEFAULT_DATA = ROOT_DIR / "data" / "raw" / "trackflow_sales.csv"
DEFAULT_OUTPUT = ROOT_DIR / "data" / "eval"
REPORT_NAME = "evaluation_report.md"
CHART_NAME = "learning_curve.png"
JSON_NAME = Path("sales_forecast") / "evaluation.json"

# Brand colours (memory-bank/context.md). Validation, the series that matters,
# takes Flow Blue; Cream Orange is below 3:1 on white, so both series are also
# told apart by marker shape and direct labels, and every value is in the report.
FLOW_BLUE = "#2563EB"
CREAM_ORANGE = "#FF9142"
INK = "#111827"
MUTED = "#6B7280"
HAIRLINE = "#E5E7EB"


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def _records(frame: pd.DataFrame) -> list[dict[Any, Any]]:
    """JSON-ready rows: NaN becomes null."""
    return frame.astype(object).where(frame.notna(), None).to_dict("records")


def _json_default(value: Any) -> Any:
    return value.item() if hasattr(value, "item") else str(value)


# ----------------------------------------------------------------------- run


def run(data_path: Path, output_dir: Path, tune: bool = True) -> dict[str, Any]:
    raw = sf.load_sales(data_path)
    clean, _ = sf.clean_sales(raw)
    revenue = sf.consolidated_series(clean)
    split = sf.split_train_test(revenue)
    # Everything below sees the training years only.
    train = split.train[sf.TARGET]

    _, fit_info = sf.fit_model(train, tune=tune)
    params: dict[str, Any] = dict(fit_info["params"])

    folds = ev.temporal_folds(train)
    leakage = {str(f.number): ev.check_fold_boundary_leakage(train, f) for f in folds}
    failed = {n: c for n, c in leakage.items() if not all(c.values())}
    if failed:
        raise ev.EvaluationError(f"fold-boundary leakage detected: {failed}")

    results = {
        name: ev.cross_validate(train, folds, candidate_params, columns)
        for name, (candidate_params, columns) in ev.candidate_settings(params).items()
    }
    shipped = results["shipped"]
    curve_cv = ev.learning_curve_cv_windows(train, params)
    curve_year = ev.learning_curve_fixed_year(train, params)
    diagnosis = ev.diagnose(shipped, curve_year)
    selected = ev.select_corrective_action(results)
    usable = ev.usable_months(train)

    candidates: dict[str, Any] = {}
    for name, (candidate_params, columns) in ev.candidate_settings(params).items():
        result = results[name]
        candidates[name] = {
            "description": ev.CANDIDATE_DESCRIPTIONS[name],
            "params": candidate_params,
            "features": list(columns),
            "summary": result.summary,
            "gap_ratio": result.gap_ratio,
            "folds_beating_naive": result.folds_beating_naive,
            "validation_peak_months": int(result.per_fold["validation_peak_months"].sum()),
            "validation_peak_under_forecast": int(
                result.per_fold["validation_peak_under_forecast"].sum()
            ),
            "per_fold_validation_rmse_pct": result.per_fold["validation_rmse_pct"].tolist(),
        }

    output = {
        "dataset": repo_relative(data_path),
        "test_years_read": False,
        "data": {
            "training_years": list(split.train_years),
            "usable_first": usable[0].strftime("%Y-%m"),
            "usable_last": usable[-1].strftime("%Y-%m"),
            "usable_months": len(usable),
            "warmup_months": sf.WARMUP_MONTHS,
            "yearly_growth_pct": sf.data_profile(train)["yearly_growth_pct"],
        },
        "model": {
            "algorithm": "XGBoost (XGBRegressor) on revenue_eur / trailing-12-month mean",
            "params": params,
            "features": list(sf.FEATURE_COLUMNS),
        },
        "cross_validation": {
            "splitter": (
                f"sklearn TimeSeriesSplit(n_splits={ev.CV_SPLITS}, "
                f"test_size={ev.CV_VALIDATION_MONTHS}), expanding window, no shuffle"
            ),
            "validation_mode": "one month ahead from measured history",
            "folds": [f.describe() for f in folds],
            "chronological_order_verified": True,
            "leakage_checks": leakage,
            "per_fold": _records(shipped.per_fold),
            "summary": shipped.summary,
            "gap_ratio": shipped.gap_ratio,
            "folds_beating_naive": shipped.folds_beating_naive,
        },
        "learning_curves": {
            "cv_windows": _records(curve_cv),
            "fixed_year": _records(curve_year),
        },
        "diagnosis": {"label": diagnosis.label, "checks": diagnosis.checks},
        "corrective_action": {
            "selection_rule": (
                "lowest mean validation RMSE across the 5 folds; candidates within "
                f"{ev.TIE_TOLERANCE_PP} pp are tied and the lowest std wins"
            ),
            "selected": selected,
            "applied": False,
            "candidates": candidates,
            "dropped_from_compact": dict(ev.DROPPED_FROM_COMPACT),
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / JSON_NAME
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(output, indent=2, default=_json_default) + "\n")
    plot_learning_curves(curve_cv, curve_year, output_dir / CHART_NAME)
    (output_dir / REPORT_NAME).write_text(render_report(output))
    return output


# --------------------------------------------------------------------- chart


def _direct_label(ax: Any, x: float, y: float, text: str) -> None:
    ax.annotate(
        text,
        xy=(x, y),
        xytext=(10, 0),
        textcoords="offset points",
        va="center",
        fontsize=9,
        color=INK,
    )


def _style(ax: Any, title: str) -> None:
    ax.set_title(title, fontsize=10, color=INK, loc="left")
    ax.set_xlabel("Training months", color=MUTED)
    ax.grid(axis="y", color=HAIRLINE, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(HAIRLINE)
    ax.tick_params(colors=MUTED, labelsize=9)


def plot_learning_curves(cv_windows: pd.DataFrame, fixed_year: pd.DataFrame, path: Path) -> None:
    fig = Figure(figsize=(12, 5.2))
    left, right = fig.subplots(1, 2, sharey=True)
    series = (
        ("validation", "Validation", FLOW_BLUE, "o"),
        ("train", "Training", CREAM_ORANGE, "s"),
    )
    for key, label, color, marker in series:
        x = cv_windows["train_months"]
        mean = cv_windows[f"{key}_rmse_pct_mean"]
        std = cv_windows[f"{key}_rmse_pct_std"]
        left.fill_between(x, mean - std, mean + std, color=color, alpha=0.15, linewidth=0)
        left.plot(x, mean, color=color, lw=2, marker=marker, ms=8, mec="white", mew=2, label=label)
        _direct_label(
            left, float(x.iloc[-1]), float(mean.iloc[-1]), f"{label} {mean.iloc[-1]:.1f}%"
        )

        x2 = fixed_year["train_months"]
        y2 = fixed_year[f"{key}_rmse_pct"]
        right.plot(x2, y2, color=color, lw=2, marker=marker, ms=8, mec="white", mew=2, label=label)
        _direct_label(right, float(x2.iloc[-1]), float(y2.iloc[-1]), f"{label} {y2.iloc[-1]:.1f}%")

    windows = cv_windows["folds"].iloc[0]
    _style(left, f"Same {windows} CV validation windows; band = mean ± 1 std")
    year = int(fixed_year["validation_year"].iloc[0])
    _style(right, f"Validation = {year} (12 months); training = the months right before it")
    left.set_ylabel("RMSE, % of mean monthly revenue", color=MUTED)
    left.set_ylim(bottom=0)
    left.set_xlim(cv_windows["train_months"].min() - 2, cv_windows["train_months"].max() + 8)
    right.set_xlim(fixed_year["train_months"].min() - 3, fixed_year["train_months"].max() + 14)
    left.set_xticks(cv_windows["train_months"].tolist())
    right.set_xticks(fixed_year["train_months"].tolist())
    left.legend(loc="upper right", frameon=False, fontsize=9, labelcolor=INK)
    fig.suptitle(
        "Learning curve: TrackFlow revenue forecast (XGBoost), training years only",
        x=0.01,
        ha="left",
        fontsize=12,
        color=INK,
    )
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor="white")


# -------------------------------------------------------------------- report


def _pct(value: float) -> str:
    return f"{value:.2f}%"


def _eur(value: float) -> str:
    return f"€{value:,.0f}"


def _pm(summary: dict[str, dict[str, float]], metric: str, unit: str = "%") -> str:
    mean, std = summary[metric]["mean"], summary[metric]["std"]
    if unit == "€":
        return f"{_eur(mean)} ± {_eur(std)}"
    return f"{mean:.2f} ± {std:.2f}%"


def _signed(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}%"


def _change(before: float, after: float) -> str:
    return f"{100 * (after / before - 1):+.0f}%"


def render_report(r: dict[str, Any]) -> str:
    data, model, cv = r["data"], r["model"], r["cross_validation"]
    summary, folds, per_fold = cv["summary"], cv["folds"], cv["per_fold"]
    curves, diagnosis, action = r["learning_curves"], r["diagnosis"], r["corrective_action"]
    checks = diagnosis["checks"]
    label = diagnosis["label"]
    candidates = action["candidates"]
    selected = action["selected"]
    chosen = candidates[selected]
    shipped = candidates["shipped"]
    growth = {int(k): v for k, v in data["yearly_growth_pct"].items()}
    years = data["training_years"]
    n_folds = len(folds)
    params_text = ", ".join(f"`{k}={v}`" for k, v in model["params"].items())

    val_rmse = summary["validation_rmse_pct"]
    train_rmse = summary["train_rmse_pct"]
    fold_values = [row["validation_rmse_pct"] for row in per_fold]
    variation = 100 * val_rmse["std"] / val_rmse["mean"]
    curve_step = checks["learning_curve_last_step"]
    step_improvement = curve_step["validation_rmse_improvement_pct"]
    cv_gap = checks["cv_gap_ratio"]["value"]
    curve_gap = checks["learning_curve_gap_ratio_at_largest_size"]["value"]
    curve_gap_months = checks["learning_curve_gap_ratio_at_largest_size"]["train_months"]
    naive = checks["beats_seasonal_naive"]
    peak_months = shipped["validation_peak_months"]
    peak_under = shipped["validation_peak_under_forecast"]

    def bias_by_year() -> str:
        parts = []
        for row in per_fold:
            year_growth = growth.get(int(row["validation_first"][:4]), float("nan"))
            parts.append(
                f"{row['validation_first']}..{row['validation_last']} "
                f"{row['validation_bias_pct']:+.1f}% (year growth {year_growth:.1f}%)"
            )
        return "; ".join(parts)

    out: list[str] = []
    add = out.append

    add("# Sales forecast: technical evaluation\n")
    add(
        "Ticket: formal technical evaluation before promotion to staging. "
        "The model under evaluation is the monthly revenue forecast in "
        "[`scripts/sales_forecast.py`](../../scripts/sales_forecast.py): "
        f"{model['algorithm']}, parameters {params_text}, {len(model['features'])} causal "
        "features (see [`docs/SALES_FORECAST.md`](../../docs/SALES_FORECAST.md)).\n"
    )
    add(
        f"Data: `{r['dataset']}`, `consolidated` rows, target `revenue_eur`. "
        f"**Only the training years {years[0]}-{years[-1]} were used; the 2024-2025 test "
        "years were not read**, so they remain a clean final check. "
        "Generated by "
        "[`scripts/evaluate_sales_forecast.py`](../../scripts/evaluate_sales_forecast.py); "
        "every number below is in "
        "[`sales_forecast/evaluation.json`](sales_forecast/evaluation.json).\n"
    )

    # ------------------------------------------------------------- verdict
    add(f"## Verdict: **{label}**\n")
    if label == "overfitting":
        fit_answer = (
            f"**Overfitting.** In cross-validation the validation error is "
            f"{cv['gap_ratio']:.1f}× the training error ({val_rmse['mean']:.2f}% vs "
            f"{train_rmse['mean']:.2f}% RMSE of mean monthly revenue). At the largest "
            f"learning-curve size it is {curve_gap:.1f}×, "
            f"and the validation error was still falling "
            f"({step_improvement:.1f}% in the last step) when the "
            "history ran out. It is not underfitting: the model beats a seasonal-naive rule "
            f"in {naive['folds_beating_naive']} of {naive['folds']} folds."
        )
    elif label == "underfitting":
        fit_answer = (
            f"**Underfitting.** Training and validation errors are close "
            f"({train_rmse['mean']:.2f}% vs {val_rmse['mean']:.2f}%) and the model does not beat "
            "the seasonal-naive rule."
        )
    else:
        fit_answer = (
            f"**Well fitted.** The validation error is {cv['gap_ratio']:.1f}× the training error, "
            "under the 2× over-fitting threshold, and the model beats the seasonal-naive rule."
        )
    if selected == "shipped":
        action_answer = (
            "**No change.** None of the candidates beat the shipped model in cross-validation."
        )
    else:
        action_answer = (
            f"**{chosen['description']}.** On the same folds the validation RMSE goes "
            f"{val_rmse['mean']:.2f}% → {chosen['summary']['validation_rmse_pct']['mean']:.2f}% "
            f"({_change(val_rmse['mean'], chosen['summary']['validation_rmse_pct']['mean'])}), "
            f"its spread across folds {val_rmse['std']:.2f} → "
            f"{chosen['summary']['validation_rmse_pct']['std']:.2f} pp "
            f"({_change(val_rmse['std'], chosen['summary']['validation_rmse_pct']['std'])}), and "
            f"the gap {shipped['gap_ratio']:.1f}× → {chosen['gap_ratio']:.1f}×. Proposed, not "
            "applied; see section 5."
        )
    add("| Ticket question | Answer |")
    add("|---|---|")
    add(f"| 1. Underfitting, overfitting or well fitted? | {fit_answer} |")
    add(
        f"| 2. How stable when the training portion changes? | Validation RMSE "
        f"{val_rmse['mean']:.2f} ± {val_rmse['std']:.2f}% across {n_folds} folds (range "
        f"{min(fold_values):.2f}-{max(fold_values):.2f}%, coefficient of variation "
        f"{variation:.0f}%). The *direction* of the error follows the growth year, the "
        f"briefing's alternating 9%/3% pattern: {bias_by_year()}. |"
    )
    add(f"| 3. Specific corrective action | {action_answer} |")
    add("")

    # ------------------------------------------------------ 1. time-aware CV
    add("## 1. Time-aware cross-validation\n")
    add(
        f"**Splitter:** {cv['splitter']}, over the {data['usable_months']} usable training "
        f"months ({data['usable_first']} to {data['usable_last']}). The first "
        f"{data['warmup_months']} months ({years[0]}-{years[2]}) are warm-up: the lag and "
        "seasonal features need three years of history before the first usable row.\n"
    )
    add(
        "Why 6-month validation windows: 5 × 6 months leaves fold 1 with 30 training months. "
        "sklearn's default sizing (60 ÷ 6 = 10 validation months) would leave fold 1 with "
        "only 10, too few to say anything about fit.\n"
    )
    add(
        f"**Validation mode:** {cv['validation_mode']}, the same task as the training fit, so "
        "the training/validation gap measures over-fitting and nothing else. The error of "
        "the real use (a recursive forecast feeding on its own predictions) is reported next "
        "to it in every fold.\n"
    )
    add("| Fold | Training months | n | Validation months | Chronology | Leakage checks |")
    add("|---|---|---|---|---|---|")
    for fold in folds:
        fold_checks = cv["leakage_checks"][str(fold["fold"])]
        passed = sum(fold_checks.values())
        add(
            f"| {fold['fold']} | {fold['train_first']} → {fold['train_last']} | "
            f"{fold['train_months']} | {fold['validation_first']} → {fold['validation_last']} | "
            f"pass | {passed}/{len(fold_checks)} pass |"
        )
    add("")
    add(
        "**No mixing or shuffling.** `assert_chronological` refuses any fold whose training "
        "and validation months are not one contiguous, increasing run (training ends the month "
        "before validation starts, with no gap and no overlap). It also refuses any validation "
        "window that does not start after the previous fold's window ends. The unit test "
        "`test_temporal_folds_preserve_chronological_order` checks the same rules independently.\n"
    )
    add(
        "**No leakage across the fold boundary.** A lag or rolling feature computed on the "
        "full series can still read a later fold. Three checks run on every fold:\n"
    )
    add(
        "- every month after the fold's last training month is multiplied by 3, and the "
        "training rows (features, base and label) must not change;"
    )
    add(
        "- the training rows built from the full series must equal the ones built from the "
        "series cut at the boundary, which is what the evaluation trains on;"
    )
    add(
        "- for each validation month *t*, months *t* and later are multiplied by 3, and its "
        "features must not change.\n"
    )
    add(
        "All pass, because every feature is built with `shift(≥1)` before any window. The "
        "detrending base is the mean of the 12 *previous* months, there are no centred windows "
        "or full-series statistics, and the `StandardScaler` is refitted inside each fold's "
        "`Pipeline`.\n"
    )
    add(
        "**Results per fold** (shipped model; RMSE and MAE as % of that set's mean "
        "monthly revenue):\n"
    )
    add(
        "| Fold | Validation | Train MAE | Train RMSE | Val MAE | Val RMSE | Val RMSE % | "
        "Val bias | Recursive RMSE % | Naive RMSE % |"
    )
    add("|---|---|---|---|---|---|---|---|---|---|")
    for row in per_fold:
        add(
            f"| {row['fold']} | {row['validation_first']} → {row['validation_last']} | "
            f"{_eur(row['train_mae_eur'])} | {_eur(row['train_rmse_eur'])} | "
            f"{_eur(row['validation_mae_eur'])} | {_eur(row['validation_rmse_eur'])} | "
            f"{_pct(row['validation_rmse_pct'])} | {row['validation_bias_pct']:+.2f}% | "
            f"{_pct(row['validation_recursive_rmse_pct'])} | "
            f"{_pct(row['naive_validation_rmse_pct'])} |"
        )
    add(
        f"| **mean ± std** | | {_pm(summary, 'train_mae_eur', '€')} | "
        f"{_pm(summary, 'train_rmse_eur', '€')} | {_pm(summary, 'validation_mae_eur', '€')} | "
        f"{_pm(summary, 'validation_rmse_eur', '€')} | {_pm(summary, 'validation_rmse_pct')} | "
        f"{_pm(summary, 'validation_bias_pct')} | "
        f"{_pm(summary, 'validation_recursive_rmse_pct')} | "
        f"{_pm(summary, 'naive_validation_rmse_pct')} |"
    )
    add("")
    add(
        "Bias is signed: total forecast minus total actual, over total actual. Positive means "
        "over-forecast. *Naive* is the seasonal-naive rule anyone can check: the same month last "
        "year × the growth of the last 12 months, from months before *t* only. Std uses ddof = 1.\n"
    )

    # ---------------------------------------------------- 2. learning curve
    add("## 2. Learning curve\n")
    add(f"![Learning curve]({CHART_NAME})\n")
    add(
        "Two views of the same question: what happens to the training and validation error "
        "as the model sees more history?\n"
    )
    add(
        f"- **Left, CV windows:** the same {n_folds} validation windows at every size. Each "
        "fold trains on the *n* months right before its window (`TimeSeriesSplit` with "
        "`max_train_size=n`), so only the amount of history changes. The largest size is fold "
        "1's 30 months."
    )
    add(
        "- **Right, last training year:** validation is "
        f"{curves['fixed_year'][0]['validation_year']} "
        "(12 months, both February and Nov-Dec). Training is the *n* months right before it, "
        "up to 48, close to the 60 the shipped model trains on.\n"
    )
    add("| Training months | Train RMSE (CV windows) | Val RMSE (CV windows) |")
    add("|---|---|---|")
    for row in curves["cv_windows"]:
        add(
            f"| {row['train_months']} | "
            f"{row['train_rmse_pct_mean']:.2f} ± {row['train_rmse_pct_std']:.2f}% | "
            f"{row['validation_rmse_pct_mean']:.2f} ± {row['validation_rmse_pct_std']:.2f}% |"
        )
    add("")
    add("| Training months | From | Train RMSE | Val RMSE | Train RMSE (€) | Val RMSE (€) |")
    add("|---|---|---|---|---|---|")
    for row in curves["fixed_year"]:
        add(
            f"| {row['train_months']} | {row['train_first']} | {_pct(row['train_rmse_pct'])} | "
            f"{_pct(row['validation_rmse_pct'])} | {_eur(row['train_rmse_eur'])} | "
            f"{_eur(row['validation_rmse_eur'])} |"
        )
    add("")
    first, last = curves["fixed_year"][0], curves["fixed_year"][-1]
    add(
        f"**Reading.** The training error stays low and almost flat "
        f"({first['train_rmse_pct']:.2f}% → {last['train_rmse_pct']:.2f}%). The validation "
        f"error starts high and keeps falling ({first['validation_rmse_pct']:.2f}% → "
        f"{last['validation_rmse_pct']:.2f}%, still {step_improvement:.1f}% "
        f"lower in the last step from {curve_step['from_train_months']} to "
        f"{curve_step['to_train_months']} months). The curves have not met: at "
        f"{curve_gap_months} months validation is still {curve_gap:.1f}× "
        "training. A large gap that more data keeps narrowing is the signature of high "
        "variance (over-fitting), not of high bias. With high bias, both errors would be high "
        "and close together.\n"
    )

    # ------------------------------------------------------------ 3. metrics
    add("## 3. Metrics: MAE and RMSE, training and validation\n")
    add("| | Training | Validation |")
    add("|---|---|---|")
    metric_rows = (
        ("MAE (EUR)", "mae_eur", "€"),
        ("RMSE (EUR)", "rmse_eur", "€"),
        ("MAE (% of mean monthly revenue)", "mae_pct", "%"),
        ("RMSE (% of mean monthly revenue)", "rmse_pct", "%"),
    )
    for title, metric, unit in metric_rows:
        add(
            f"| {title} | {_pm(summary, f'train_{metric}', unit)} | "
            f"{_pm(summary, f'validation_{metric}', unit)} |"
        )
    add("")
    ratio = summary["validation_rmse_eur"]["mean"] / summary["validation_mae_eur"]["mean"]
    add("### Which one reflects the business cost: RMSE\n")
    add(
        "The briefing "
        "([`contexts/trackflow-sales-briefing.md`](../../contexts/trackflow-sales-briefing.md)) "
        "says TrackFlow bills mainly on **shipment volume**, and that the peaks and valleys "
        "are the ones Ana (Warehouse) and Carlos (Carriers) plan for by hand every year. A "
        "revenue error is therefore a **workload** error: the forecast is what warehouse staff in "
        "Los Angeles and Zaragoza are scheduled against and what carrier capacity is booked "
        "against. The two directions of error do not cost the same:\n"
    )
    add(
        "- **Under-forecasting is the expensive error.** Too few operatives and carrier slots, "
        "worst of all in Nov-Dec, when revenue runs 25-35% above the average (Black Friday and "
        "the holidays). Once planned capacity is exceeded, each extra parcel costs more: "
        "overtime, last-minute carrier capacity at peak rates, late deliveries and SLA "
        "incidents. Those land on the brands in their most important selling weeks, and "
        "client contracts are renewed yearly on whether the operation runs well."
    )
    add(
        "- **Over-forecasting is the cheaper error.** Idle staff hours and unused carrier "
        "commitments. The cost is roughly proportional to the miss, bounded, and recoverable "
        "the following month.\n"
    )
    add(
        "Because the cost of a miss grows faster than the miss itself, the metric should too. "
        "**RMSE squares each error**, so one large miss (a peak month badly under-planned) "
        "weighs more than several small ones of the same total. That matches how the cost "
        "behaves. **MAE** weighs every euro of error equally, as if a €100k shortfall in "
        "December cost the same as ten €10k misses in spring. RMSE is the decision metric for "
        'this model. MAE is kept as the plain-language figure ("off by about '
        f'€{summary["validation_mae_eur"]["mean"] / 1000:,.0f}k a month") for Thomas and Ana.\n'
    )
    add(
        f"The RMSE/MAE ratio in validation is {ratio:.2f}. It would be 1.00 if every month "
        f"missed by the same amount, and √{ev.CV_VALIDATION_MONTHS} ≈ "
        f"{ev.CV_VALIDATION_MONTHS**0.5:.2f} if one month of a 6-month window carried all the "
        "error. The errors are spread evenly, with no single blow-out month. A rising ratio "
        "would warn that misses are concentrating, which is the peak-month risk.\n"
    )
    add(
        "Neither metric sees *direction*, so the report adds the signed bias. In "
        f"cross-validation, **{peak_under} of the {peak_months} Nov-Dec validation months were "
        "under-forecast**: the shipped model leans towards the expensive side exactly in "
        "the peak. Any future change should be checked on this count as well as on RMSE.\n"
    )

    # ---------------------------------------------------------- 4. diagnosis
    add(f"## 4. Diagnosis: **{label}**\n")
    add("The rule (`classify_fit` in `scripts/sales_forecast_evaluation.py`):\n")
    add(
        f"- **overfitting** if validation RMSE ≥ {ev.OVERFIT_GAP_RATIO:.1f}× training RMSE both in "
        "cross-validation and at the largest learning-curve size;"
    )
    add(
        f"- **underfitting** if the gap is below {ev.UNDERFIT_GAP_RATIO:.1f}× and the model does "
        "not beat the seasonal-naive rule, so both errors are high and it has not learned "
        "even what a simple rule knows;"
    )
    add("- **well fitted** otherwise.\n")
    add("| Check | Measured | Threshold | Result |")
    add("|---|---|---|---|")
    add(
        f"| CV gap: validation ÷ training RMSE | {cv_gap:.2f}× | ≥ {ev.OVERFIT_GAP_RATIO:.1f}× "
        f"| {'over-fit signal' if cv_gap >= ev.OVERFIT_GAP_RATIO else 'below'} |"
    )
    add(
        f"| Learning-curve gap at {curve_gap_months} "
        f"months | {curve_gap:.2f}× | ≥ {ev.OVERFIT_GAP_RATIO:.1f}× | "
        f"{'over-fit signal' if curve_gap >= ev.OVERFIT_GAP_RATIO else 'below'} |"
    )
    step_result = (
        "still improving: more history would help" if curve_step["still_improving"] else "converged"
    )
    add(
        f"| Last learning-curve step ({curve_step['from_train_months']} → "
        f"{curve_step['to_train_months']} months) | validation "
        f"{step_improvement:.1f}% lower | ≥ "
        f"{curve_step['threshold_pct']:.0f}% = still improving | {step_result} |"
    )
    add(
        f"| Beats seasonal-naive rule | {naive['model_validation_rmse_pct']:.2f}% vs "
        f"{naive['naive_validation_rmse_pct']:.2f}% RMSE ({naive['folds_beating_naive']}/"
        f"{naive['folds']} folds) | lower is better | "
        f"{'yes: not underfitting' if naive['value'] else 'no'} |"
    )
    add("")
    if label == "overfitting":
        depth_1 = candidates["depth_1"]
        as_well = (
            "validates at least as well"
            if depth_1["summary"]["validation_rmse_pct"]["mean"] <= val_rmse["mean"]
            else "validates almost as well"
        )
        add(
            "**Root cause.** The shipped model has more capacity than its training data can "
            f"support. It has {len(model['features'])} features and depth-"
            f"{model['params'].get('max_depth')} trees, so it can learn pairwise feature "
            f"interactions, boosted {model['params'].get('n_estimators')} times. It trains on "
            f"30 to {data['usable_months']} monthly rows. That capacity lets it fit month-level "
            f"noise. Its training error ({train_rmse['mean']:.2f}%) is less than half of what "
            f"the depth-1 variant reaches ({depth_1['summary']['train_rmse_pct']['mean']:.2f}%), "
            f"and that variant {as_well}. The fit below that level was noise, "
            "not signal. The learning curve shows the model would need more history to use its "
            f"capacity, and there is none: {years[0]} is the first month on record, and the "
            f"{data['warmup_months']}-month warm-up already uses 3 of the 8 training years. "
            "The fix is therefore to shrink capacity to what about 60 rows support. "
            '"Add more data" is not possible, and "increase complexity" would widen the gap.\n'
        )

    # ------------------------------------------------- 5. corrective action
    add("## 5. Corrective action\n")
    add(
        "Every candidate below is a way to shrink capacity, scored on the same "
        f"{n_folds} folds as the shipped model, on the training years only. "
        f"Selection rule: {action['selection_rule']}.\n"
    )
    add(
        "| Candidate | Train RMSE | Val RMSE (mean ± std) | Gap | Recursive val RMSE | Val MAE | "
        "Beats naive | Nov-Dec under-forecast |"
    )
    add("|---|---|---|---|---|---|---|---|")
    for name, c in candidates.items():
        s = c["summary"]
        marker = " **(selected)**" if name == selected else ""
        add(
            f"| {c['description']}{marker} | {s['train_rmse_pct']['mean']:.2f}% | "
            f"{s['validation_rmse_pct']['mean']:.2f} ± {s['validation_rmse_pct']['std']:.2f}% | "
            f"{c['gap_ratio']:.2f}× | {s['validation_recursive_rmse_pct']['mean']:.2f} ± "
            f"{s['validation_recursive_rmse_pct']['std']:.2f}% | "
            f"{_eur(s['validation_mae_eur']['mean'])} | {c['folds_beating_naive']}/{n_folds} | "
            f"{c['validation_peak_under_forecast']}/{c['validation_peak_months']} |"
        )
    add("")
    if selected == "shipped":
        add("**No change is recommended:** no candidate beat the shipped model.\n")
    else:
        cs = chosen["summary"]
        add(f"**Recommended: {chosen['description']}.**\n")
        add(f"- **Parameters:** {', '.join(f'`{k}={v}`' for k, v in chosen['params'].items())}.")
        feature_list = ", ".join(f"`{f}`" for f in chosen["features"])
        add(f"- **Features ({len(chosen['features'])}):** {feature_list}.")
        add(
            f"- **Effect in cross-validation:** validation RMSE {val_rmse['mean']:.2f}% → "
            f"{cs['validation_rmse_pct']['mean']:.2f}% "
            f"({_change(val_rmse['mean'], cs['validation_rmse_pct']['mean'])}). Fold-to-fold "
            f"spread {val_rmse['std']:.2f} → {cs['validation_rmse_pct']['std']:.2f} pp "
            f"({_change(val_rmse['std'], cs['validation_rmse_pct']['std'])}), which answers "
            f"question 2 directly. Gap {shipped['gap_ratio']:.2f}× → {chosen['gap_ratio']:.2f}×. "
            f"Recursive validation RMSE {_pm(shipped['summary'], 'validation_recursive_rmse_pct')} "
            f"→ {_pm(cs, 'validation_recursive_rmse_pct')}."
        )
        add(
            "- **Why it targets the root cause:** depth 1 removes feature interactions, so each "
            "tree is a single split on one signal. The forecast becomes an additive sum of "
            "seasonal, lag and growth effects, which is how the briefing describes the series "
            "(growth × seasonality × noise). The compact set keeps each signal once and drops "
            "the columns that only restate the calendar month or measure month-to-month "
            f"volatility, the noise being memorised. Training error rises "
            f"{train_rmse['mean']:.2f}% → {cs['train_rmse_pct']['mean']:.2f}% while validation "
            "error falls, so the fit it gives up was noise, not signal.\n"
        )
        add("Columns dropped from the compact set:\n")
        add("| Column | Why it can go |")
        add("|---|---|")
        for column, reason in action["dropped_from_compact"].items():
            add(f"| `{column}` | {reason} |")
        add("")
    add(
        "**What this does not fix.** Two limits remain in every candidate. "
        "Neither is over-fitting:\n"
    )
    add(
        "- **The growth year.** The validation bias flips sign with the growth year. It is an "
        "over-forecast in low-growth years and an under-forecast in high-growth years "
        "(question 2's bias list). From 3 to 5 usable training years the model cannot tell "
        "whether the coming year is a 9% or a 3% year. A growth-regime feature or external "
        "drivers (clients won or lost) are the way to address it."
    )
    flattened = [
        row
        for row in per_fold
        if row["validation_peak_bias_pct"] is not None
        and row["validation_bias_pct"] > 0
        and row["validation_peak_bias_pct"] < 0
    ]
    peak_text = (
        f"- **The peak is flattened.** {peak_under} of {peak_months} Nov-Dec validation months "
        f"are under-forecast by the shipped model, and "
        f"{chosen['validation_peak_under_forecast']} of {chosen['validation_peak_months']} by "
        "the recommended one."
    )
    if flattened:
        row = flattened[0]
        peak_text += (
            f" In {row['validation_first']}..{row['validation_last']} the half-year as a whole "
            f"was over-forecast ({row['validation_bias_pct']:+.1f}%), yet Nov-Dec were still "
            f"under-forecast ({row['validation_peak_bias_pct']:+.1f}%)."
        )
    peak_text += (
        " Boosted trees with shrinkage pull the most extreme ratio, the peak, towards the "
        "average. Under-forecasting the peak is the expensive error (section 3), so this is "
        "the next limit to work on. One option is an asymmetric loss, for example XGBoost's "
        "`reg:quantileerror` above the median, which buys peak protection at a known cost in "
        "RMSE. That is a business trade-off for Thomas, not part of this fix.\n"
    )
    add(peak_text)
    add(
        "**How to apply it, after approval.** In `scripts/sales_forecast.py`, train with the "
        "parameters above on the compact feature list. Re-run "
        "`scripts/train_sales_forecast.py` **once** and compare its 2024-2025 metrics with "
        "the current [`sales_forecast/metrics.json`](sales_forecast/metrics.json). The test "
        "years were not used to choose this action, so that single run is an honest "
        "confirmation. Running it repeatedly to tune would turn the test years into a "
        "second validation set.\n"
    )

    # ---------------------------------------------------------- 6. caveats
    add("## 6. Caveats\n")
    add(
        "- The shipped parameters were chosen by recursive backtests from origins inside these "
        "same training years. The shipped model's cross-validation scores are therefore "
        "slightly optimistic."
    )
    add(
        "- The recommended candidate was chosen on the same five folds it is scored on, so "
        "its gain is an upper bound. The single test-year run above is the unbiased check."
    )
    add(
        "- The validation windows alternate between H1 (the February dip) and H2 (the Nov-Dec "
        "peak), and between low- and high-growth years. The fold-to-fold spread therefore "
        "mixes season and growth-year effects with the data-portion effect."
    )
    add(
        f"- {data['usable_months']} usable months is a small sample. Differences of a few "
        "tenths of a percentage point between candidates are within noise. The ranking is "
        "driven by the gap and the spread, which move by much more.\n"
    )

    # --------------------------------------------------------- 7. reproduce
    add("## 7. Reproduce\n")
    add("```bash")
    add("# macOS without Homebrew's libomp only:")
    add('export DYLD_LIBRARY_PATH="$PWD/.venv/lib/python3.14/site-packages/sklearn/.dylibs"')
    add(".venv/bin/python scripts/evaluate_sales_forecast.py")
    add(".venv/bin/python -m pytest tests/pipelines/test_sales_forecast_evaluation.py")
    add("```\n")
    add("The run is deterministic: a second run rewrites identical files.")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------- main


def summary_text(result: dict[str, Any]) -> str:
    cv = result["cross_validation"]
    s = cv["summary"]
    action = result["corrective_action"]
    chosen = action["candidates"][action["selected"]]["summary"]["validation_rmse_pct"]
    return "\n".join(
        [
            f"Folds        {len(cv['folds'])} x TimeSeriesSplit, chronology + leakage checks pass",
            (f"Train RMSE   {_pm(s, 'train_rmse_pct')}   MAE {_pm(s, 'train_mae_pct')}"),
            (
                f"Val RMSE     {_pm(s, 'validation_rmse_pct')}   "
                f"MAE {_pm(s, 'validation_mae_pct')}   (gap {cv['gap_ratio']:.2f}x)"
            ),
            f"Diagnosis    {result['diagnosis']['label']}",
            (
                f"Action       {action['selected']}: val RMSE {chosen['mean']:.2f} ± "
                f"{chosen['std']:.2f}% (proposed, not applied)"
            ),
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--no-tune",
        action="store_true",
        help="evaluate the default parameters instead of the tuned (shipped) ones",
    )
    args = parser.parse_args(argv)
    try:
        result = run(args.data, args.output_dir, tune=not args.no_tune)
    except (sf.SalesDataError, FileNotFoundError) as exc:
        print(f"dataset error: {exc}", file=sys.stderr)
        return 2
    except ev.EvaluationError as exc:
        print(f"evaluation check failed: {exc}", file=sys.stderr)
        return 3
    print(summary_text(result))
    print(f"Outputs written to {args.output_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
