# Sales forecast: monthly revenue prediction

This answers the ticket *"how much is the company going to sell in the coming months?"*. It is a regression model trained on TrackFlow's historical monthly revenue and evaluated honestly on the last two years of data.

| Piece | Location |
|---|---|
| Input dataset | [`data/raw/trackflow_sales.csv`](../data/raw/trackflow_sales.csv), the path the briefing names. It is an identical copy of the delivered [`contexts/company-sales.csv`](../contexts/company-sales.csv). |
| Business context | [`contexts/trackflow-sales-briefing.md`](../contexts/trackflow-sales-briefing.md) |
| Library (cleaning, split, features, model, metrics) | [`scripts/sales_forecast.py`](../scripts/sales_forecast.py) |
| CLI (train + evaluate + chart) | [`scripts/train_sales_forecast.py`](../scripts/train_sales_forecast.py) |
| Tests (8/2 split, no leakage, cleaning, metrics) | [`tests/pipelines/test_sales_forecast.py`](../tests/pipelines/test_sales_forecast.py) |
| Outputs | `data/eval/sales_forecast/` |

## How to run

```bash
uv sync                                             # dependencies are in pyproject.toml
uv run python scripts/train_sales_forecast.py       # full run, with parameter search
uv run python scripts/train_sales_forecast.py --no-tune   # faster, default parameters
uv run pytest tests/pipelines/test_sales_forecast.py
```

Options: `--data <csv>` (default `data/raw/trackflow_sales.csv`) and `--output-dir <dir>` (default `data/eval/sales_forecast`).

**macOS prerequisite:** XGBoost needs the OpenMP runtime. Without it, `import xgboost` fails with `Library not loaded: @rpath/libomp.dylib`.
- With Homebrew: `brew install libomp`, then the commands above work as written.
- Without Homebrew: scikit-learn ships its own `libomp.dylib`, and XGBoost can load it. Point the loader at it and call the venv's Python directly, because `uv run` does not pass `DYLD_*` variables through:

  ```bash
  export DYLD_LIBRARY_PATH="$PWD/.venv/lib/python3.14/site-packages/sklearn/.dylibs"
  .venv/bin/python scripts/train_sales_forecast.py
  .venv/bin/python -m pytest tests/pipelines/test_sales_forecast.py
  ```

Exit codes:
- `0`: success.
- `2`: the dataset is missing or breaks the briefing's contract. Examples are wrong columns, missing months, non-positive revenue, or a span that isn't 10 full years.

### Outputs

| File | Content |
|---|---|
| `metrics.json` | data profile vs briefing, cleaning report, split, algorithm and chosen parameters, test metrics for the model **and** a baseline, PSI, variability band, feature importance |
| `forecast_test_years.csv` | per test month: actual, forecast, 80% range, baseline |
| `forecast_test_years.png` | chart of forecast + 80% range against the actual test years |

## Data contract and cleaning

These columns are required, in this order: `month, revenue_eur, shipments_processed, avg_revenue_per_shipment_eur, market`. The target is `revenue_eur` of the `consolidated` rows.

Cleaning steps:
- Validate that `month` is `YYYY-MM-01`.
- Trim and lowercase `market`.
- Drop exact duplicate rows.
- Refuse conflicting duplicates for the same month and market.
- If one of revenue, shipments or revenue per shipment is missing, derive it from the other two (`revenue = shipments × avg`).
- Refuse gaps in the monthly sequence and non-positive revenue.

Every action is counted in `metrics.json → cleaning`. Nothing is silently imputed.

`data_profile` compares the data with the briefing's expectations. On the company file every check passes:

| Briefing expectation | Measured in `trackflow_sales.csv` |
|---|---|
| 120 consolidated months, 2016-01 to 2025-12, no gaps, all revenue positive | 120 months, 2016-01 to 2025-12, no gaps, no empty cells, no duplicates. Only `consolidated` rows. The cleaning report is all zeros. |
| Yearly growth alternating between about 9% and 3% | 9.6, 1.8, 9.4, 2.9, 8.0, 2.8, 10.1, 4.0, 7.9% (2017-2025). The high/low alternation is exactly as described. The yearly totals include the monthly noise, so the values spread over 1.8-10.1% instead of landing on 3% and 9%. |
| Nov-Dec +25-35% against the yearly average | +25.3% / +24.0% |
| February −10-15% | −15.9% |
| Other months within ±5% | −2.2% to −5.1%. They sit below the yearly average because the Nov-Dec peak lifts it. |

## Split: 8 years train, 2 years test

`split_train_test` splits by **calendar year**: 2016-2023 for training (96 months) and 2024-2025 for testing (24 months). It refuses any dataset that doesn't contain exactly 10 complete years. The two sets are asserted to be disjoint and ordered, and the tests verify this.

## Model

### Why XGBoost (vs Random Forest)

- **Data size:** about 120 points. Shallow boosted trees with regularization (`max_depth` 2-3, `subsample`, `colsample_bytree`, `reg_lambda`) fit a small series well. A Random Forest averages deep trees and tends to over-smooth the seasonal peaks at this size.
- **Explainability:** gain-based feature importance is reported in `metrics.json`. It shows which signals drive the forecast, for example last year's seasonal ratio or the peak-season flag.
- **Tuning time:** 8 parameter combinations × 3 backtests run in seconds on a laptop.

### Detrended target

Tree models cannot extrapolate: they never predict above the largest value seen in training, and TrackFlow keeps growing. So the model predicts

```
ratio = revenue / mean(revenue over the previous 12 months)
```

and converts the prediction back to EUR as `ratio × trailing 12-month mean`. The growth then comes from the base, and the model learns seasonality and momentum.

### Features (all causal)

Every feature for month *t* uses only months up to *t−1*:
- Lagged ratios at 1, 2, 3 and 12 months.
- Rolling mean and standard deviation of the ratio.
- Trailing year-over-year growth.
- Last year's and the 2-year mean seasonal ratio.
- Calendar month, sin/cos, `is_peak_season` (Nov-Dec) and `is_february`.

The first 36 months are used only as warm-up for the features.

`StandardScaler` (fit on training only) and `XGBRegressor` are wrapped in a single sklearn `Pipeline`.

### Forecast mode: recursive

The 24 test months are forecast **recursively** from the end of 2023. Each predicted month is fed back as input for the next one, so the model never sees a measured 2024-2025 value, not even as a lag. This is the realistic setup for "the coming months". The tests check it by multiplying all test-year values ×3 and verifying that the forecast doesn't change.

### Parameter selection

Each candidate in `PARAM_GRID` is scored by running 24-month recursive backtests from origins 2019, 2020 and 2021, all **inside** the training years. The candidate with the lowest mean absolute relative error wins. Standard k-fold cross-validation is not used, because it would let the model see the future of the time series.

## Metrics: what each one says

All metrics are computed on the 2 test years, for the model **and** for a seasonal-naive baseline: the same month last year × last year's growth, applied recursively.

| Metric | Meaning | How to read it |
|---|---|---|
| **MSE** (EUR²) | mean squared error | The raw number is hard to interpret because it is in squared euros. Read it as **RMSE as % of mean monthly revenue** (also reported). |
| **K2 = R²** | share of month-to-month variance explained | 1 is perfect; 0 is no better than predicting the mean. |
| **Gini** (normalized) | does the forecast *rank* months correctly (peaks vs valleys)? | 1 is perfect ordering. Useful for planning capacity around peaks. |
| **PSI** | Population Stability Index between two distributions | <0.10 stable, 0.10-0.25 moderate shift, >0.25 significant shift |
| MAE / MAPE | average error in EUR / in % | MAPE is the easiest to communicate. |

PSI is computed for:
- `seasonal_ratio_train_vs_test`: has the real pattern changed between training and test? If so, any model trained on the past is at risk.
- `model_ratio_fitted_train_vs_forecast_test`: are the forecasts distributed like the training fit? A moderate value is expected, because recursive forecasts are smoother than reality.
- `us_spain_mix_train_vs_test`: whether the US/Spain revenue mix (about 60/40) drifted. This is only computed if the CSV includes the per-market rows. The company file has only `consolidated` rows, so `metrics.json` reports it as `null` with a note rather than inventing a split.

**PSI needs a noise check at this size.** The 0.10 / 0.25 thresholds come from credit scoring, where samples have thousands of rows. Here the test side has 24 months, and PSI comes out large by chance alone. Each PSI therefore comes with `p_value_vs_no_shift`. The train and test values are pooled and re-split at random 2000 times into the same sizes, and the result is the share of re-splits whose PSI is at least as large as the real one. A value of 0.05 or more means the shift is within noise, whatever the label says. This reference treats months as interchangeable, so it gives the size of the noise, not a full time-series test.

**Why low MSE alone is not enough:**
- It is scale-dependent, so it looks huge in EUR².
- It can be low while the model misses the timing of the peaks, which Gini catches.
- It says nothing about whether the past still represents the future, which PSI checks.
- It is meaningless without a reference. That is why the baseline is reported next to the model: the model must **beat the baseline** to be worth using.

## Variability range

The chart shades an **80% range**: `forecast × (1 ± P80 of absolute relative error)`. The error comes from the walk-forward backtests inside the training years and is computed separately for horizon year 1 and year 2, so the range widens further out. `metrics.json → variability_band` reports the half-width per horizon and the share of test months that fell inside the range.

Caveat: the same backtests also select the parameters, so the range may be slightly optimistic.

## Results

Real run on `data/raw/trackflow_sales.csv`, run on 2026-11-07. The run is deterministic: two runs give identical outputs. The model was trained on 2016-2023 and forecast all 24 months of 2024-2025 from the end of 2023, without seeing any of them.

| Test set 2024-2025 | XGBoost | Seasonal-naive baseline |
|---|---|---|
| **MSE** | 2.10 × 10⁹ EUR² | 1.16 × 10¹⁰ EUR² |
| RMSE (√MSE) | €45,863 = **3.53%** of mean monthly revenue (€1.30M) | €107,935 = 8.31% |
| MAE | €35,389 | €93,013 |
| MAPE | **2.75%** | 7.33% |
| **K2 Score (R²)** | **0.928** | 0.602 |
| **Gini** (normalized) | **0.931** | 0.921 |
| Test months inside the 80% range | 23 of 24 (95.8%) | — |

**PSI**
- Seasonal pattern, train vs test: **0.156**. The rule of thumb calls that a "moderate shift", but p = 0.66: two out of three random re-splits give a PSI at least as large. That is sampling noise from 24 months, not a change in TrackFlow's seasonality.
- Model fit vs forecast: **0.024**, stable (p = 1.00). The forecasts are distributed like the training fit.
- US/Spain mix: not measurable, because the file has only consolidated rows.

**Chosen parameters:** `max_depth=2`, `n_estimators=200`, `learning_rate=0.03`, with a 5.1% backtest MAPE inside the training years. The shallowest and slowest-learning candidate won, which fits a 60-row training set.

**What drives the forecast** (gain importance): `lag_12_ratio` 0.40, `seasonal_ratio_2y_mean` 0.16, `seasonal_ratio_last_year` 0.15, `month_cos` 0.08. In plain words, the forecast is mostly "this month last year, against its own trailing average", corrected by the last two years' seasonal shape. That is the logic Ana and Carlos already apply by hand. `is_peak_season` and `is_february` get zero importance, because the seasonal ratios already carry that information.

**Variability range:** ±7.0% in the first forecast year and ±7.5% in the second. The one month outside it is 2025-08 (actual €1,244k, range €1,255k-€1,460k), about 1% under the lower edge.

### Answer for Finance

Yes, it is feasible. Forecasting 12 to 24 months ahead from the history alone, the model was off by **2.75% per month on average (about €35k on a €1.3M month)**. It explained 93% of the month-to-month variation and ranked the peak and valley months almost exactly as they happened. The simple rule "same month last year × last year's growth" was off by 7.3%, so the model cuts that error by more than half. The shaded range (about ±7%) is the honest planning margin: 23 of the 24 real months fell inside it.

### Why XGBoost, with these numbers

The stakeholder asked for an "acceptable margin of error" before committing money. That is an accuracy question, so XGBoost was chosen over Random Forest. The explainability that Random Forest would offer is covered another way. The error is reported in EUR and in % of revenue, which Finance can read. The baseline is a rule anyone can recompute in a spreadsheet. The feature importances show that the model leans on last year's same month, which is easy to explain. The cost of the choice was tuning. That cost stayed small: a grid of 8 settings, scored on backtests inside the training years.

## Limitations

- About 120 monthly points is a small sample. With different noise realizations on synthetic data, R² ranged from 0.64 to 0.97, and in one case the model did not beat the baseline. Always compare against the baseline.
- No external drivers are used, such as clients won or lost, pricing changes, or the economy. A sudden structural change will not be anticipated.
- The forecast is for consolidated revenue only. US and Spain are not modelled separately.
- This run answers the ticket's feasibility question on 2024-2025. A forecast of 2026 onwards would need the model refitted on all 10 years, which is not built yet.
