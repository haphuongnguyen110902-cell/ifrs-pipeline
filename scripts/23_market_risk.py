"""
scripts/23_market_risk.py

WHAT
----
Market risk and return metrics per company, computed from DAILY PRICE
HISTORY (not fundamentals): annualized volatility, Sharpe ratio, max
drawdown, beta vs. a benchmark, and rolling correlation. Before this
script, the entire pipeline was fundamentals-only - zero analysis of how
a company's stock actually behaves. This is the most basic vocabulary of
the Asset Management discipline, and the project had none of it despite
Asset Management being an explicit goal (see ROADMAP.md Phase 7).

RENUMBERED FROM THE ORIGINALLY-PLANNED "22_market_risk.py"
------------------------------------------------------------
`22` was already taken by `22_dcf.py` (Phase 6) by the time this phase
was written up in ROADMAP.md - found while actually starting this phase,
not left to collide silently. `24_credit.py`/`25_scenario.py` (Phases 8-9)
are bumped from `23`/`24` for the same reason.

BENCHMARK: STOXX EUROPE 600 (^STOXX ON YAHOO FINANCE), NOT CAC 40
--------------------------------------------------------------------
This project's universe spans France, Italy, Spain, Sweden and the UK -
a France-only index (CAC 40) would be the wrong comparison for Essity,
Shell, Amplifon, Puig Brands and Moncler. One consistent pan-European
benchmark for every company, not a per-country one - the same "one
universe-wide comparison" principle as the single EUR conversion used
everywhere else in this project (see 19_valuation.py).

BETA IS COMPUTED FROM RAW PRICE HISTORY, NOT READ FROM YFINANCE
--------------------------------------------------------------------
22_dcf.py's WACC uses yfinance's own `info.get("beta")` - deliberately
kept as-is there (it's a fine quick WACC input). This script does NOT
reuse that number: Yahoo's own beta is an opaque black box (unstated
benchmark, unstated lookback window, unstated return frequency), and
"quick WACC input" is a different bar than "market-risk analysis this
project is claiming as its own." Beta here is a straightforward OLS
slope of the company's daily returns regressed on ^STOXX's daily
returns over the same explicitly-stated window - computed transparently
rather than trusted from an opaque number. The two scripts making
different choices here is intentional, not an inconsistency to fix.

METHODOLOGY
-----------
- Annualized volatility: std(daily returns) * sqrt(252)
- Sharpe ratio: (mean(daily returns) * 252 - risk_free_rate) / annualized
  volatility. Same risk-free-rate assumption as 22_dcf.py's WACC
  (DEFAULT_RISK_FREE_RATE below must be kept in sync with that script's
  constant - duplicated rather than imported to avoid this script
  pulling in the entire DCF/3-statement/ratio-engine import chain for
  one shared float).
- Beta: cov(stock returns, benchmark returns) / var(benchmark returns) -
  the textbook OLS-slope definition, not a canned library call, so the
  formula is inspectable in one line.
- Correlation: Pearson correlation of the same two return series over
  the full window.
- Rolling correlation: a 60-trading-day rolling correlation series,
  summarized as mean/min/max rather than persisted point-by-point - same
  "aggregate, not fold-level detail" choice 17_backtest.py already made
  for its rolling folds.
- Max drawdown: largest peak-to-trough decline in the price series over
  the window (price / cumulative-max - 1, take the minimum).

LIMITATIONS - stated up front
------------------------------
- One year of daily history by default (~252 trading days) - long enough
  for a stable volatility/Sharpe estimate, short enough that beta
  reflects CURRENT risk characteristics rather than a stale 5-year
  average. Override with --period for a different yfinance period string
  (e.g. "2y", "6mo").
- Same "filing currency == quote currency" simplification 19_valuation.py
  already documents: this script works entirely in each ticker's own
  quote currency (no EUR conversion) since volatility/Sharpe/beta/
  correlation are unit-, not currency-, comparisons - converting a return
  SERIES to EUR would require daily FX rates this project doesn't fetch,
  and would not change any of these metrics in a way that matters (a
  return is already scale-free).
- Companies with a short listing history or trading halts within the
  window will have fewer than ~252 observations - n_observations is
  stored precisely so a thin window is visible, never hidden.

EXAMPLE
-------
    python scripts/23_market_risk.py --company "L'Oreal"
    python scripts/23_market_risk.py --company "L'Oreal" --period 2y

Usage:
    python scripts/23_market_risk.py --company "COMPANY NAME"
    python scripts/23_market_risk.py --company "COMPANY NAME" --period 2y
    python scripts/23_market_risk.py --company "COMPANY NAME" --benchmark "^STOXX"
    python scripts/23_market_risk.py --company "COMPANY NAME" --no-db
"""
import argparse
import importlib.util
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

_THIS_DIR = Path(__file__).parent
_val_spec = importlib.util.spec_from_file_location("valuation_19", _THIS_DIR / "19_valuation.py")
val19 = importlib.util.module_from_spec(_val_spec)
_val_spec.loader.exec_module(val19)

MARKET_RISK_SCHEMA = Path(__file__).parent.parent / "sql" / "schema_market_risk.sql"

DEFAULT_BENCHMARK = "^STOXX"  # STOXX Europe 600 - see module docstring for why not CAC 40
TRADING_DAYS_PER_YEAR = 252
ROLLING_WINDOW = 60  # trading days (~3 months)

# Must match 22_dcf.py's DEFAULT_RISK_FREE_RATE - duplicated, not
# imported, to avoid pulling in that script's entire DCF/3-statement/
# ratio-engine import chain for one shared float. See module docstring.
DEFAULT_RISK_FREE_RATE = 0.034  # German 10Y Bund, ~Sept 2026


# ---------------------------------------------------------------- price history

def fetch_daily_returns(ticker: str, period: str) -> pd.Series:
    """Daily pct-change returns from yfinance's adjusted close, indexed by
    date. Empty series (not an exception) if yfinance returns no data -
    callers decide what "no data" means for them."""
    history = yf.Ticker(ticker).history(period=period, auto_adjust=True)
    if history.empty:
        return pd.Series(dtype=float)
    prices = history["Close"]
    prices.index = pd.to_datetime(prices.index).tz_localize(None)
    return prices.pct_change().dropna()


def fetch_price_series(ticker: str, period: str) -> pd.Series:
    """Raw adjusted close prices (not returns) - needed separately for
    max drawdown, which depends on the price PATH, not just returns."""
    history = yf.Ticker(ticker).history(period=period, auto_adjust=True)
    if history.empty:
        return pd.Series(dtype=float)
    prices = history["Close"]
    prices.index = pd.to_datetime(prices.index).tz_localize(None)
    return prices


# ---------------------------------------------------------------- metrics

def annualized_volatility(returns: pd.Series) -> float:
    if returns.empty:
        return float("nan")
    return float(returns.std() * np.sqrt(TRADING_DAYS_PER_YEAR))


def sharpe_ratio(returns: pd.Series, risk_free_rate: float) -> float:
    if returns.empty:
        return float("nan")
    annualized_return = returns.mean() * TRADING_DAYS_PER_YEAR
    vol = annualized_volatility(returns)
    if not vol:
        return float("nan")
    return float((annualized_return - risk_free_rate) / vol)


def max_drawdown(prices: pd.Series) -> float:
    """Largest peak-to-trough decline over the window, as a negative
    fraction (e.g. -0.35 = a 35% drawdown at the worst point)."""
    if prices.empty:
        return float("nan")
    running_max = prices.cummax()
    drawdown = prices / running_max - 1
    return float(drawdown.min())


def compute_beta(stock_returns: pd.Series, bench_returns: pd.Series) -> tuple:
    """Beta = cov(stock, benchmark) / var(benchmark) - the textbook OLS-
    slope definition. Returns (beta, n_aligned_observations) - aligns on
    shared dates first, since a stock and its benchmark won't trade on
    exactly the same calendar (different market holidays)."""
    aligned = pd.concat([stock_returns, bench_returns], axis=1, join="inner").dropna()
    if len(aligned) < 2:
        return float("nan"), 0
    stock_aligned, bench_aligned = aligned.iloc[:, 0], aligned.iloc[:, 1]
    bench_var = bench_aligned.var()
    if not bench_var:
        return float("nan"), len(aligned)
    beta = stock_aligned.cov(bench_aligned) / bench_var
    return float(beta), len(aligned)


def compute_correlation(stock_returns: pd.Series, bench_returns: pd.Series) -> float:
    aligned = pd.concat([stock_returns, bench_returns], axis=1, join="inner").dropna()
    if len(aligned) < 2:
        return float("nan")
    return float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))


def rolling_correlation_summary(stock_returns: pd.Series, bench_returns: pd.Series,
                                 window: int = ROLLING_WINDOW) -> dict:
    aligned = pd.concat([stock_returns, bench_returns], axis=1, join="inner").dropna()
    if len(aligned) < window:
        return {"mean": float("nan"), "min": float("nan"), "max": float("nan")}
    rolling = aligned.iloc[:, 0].rolling(window).corr(aligned.iloc[:, 1]).dropna()
    if rolling.empty:
        return {"mean": float("nan"), "min": float("nan"), "max": float("nan")}
    return {"mean": float(rolling.mean()), "min": float(rolling.min()), "max": float(rolling.max())}


# ---------------------------------------------------------------- persistence

def ensure_market_risk_table(engine):
    ddl = MARKET_RISK_SCHEMA.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(ddl))


def _get_company_id(conn, company: str):
    """See PLAN.md WP1 - market_risk now has a real company_id FK
    alongside the legacy `company` TEXT column."""
    row = conn.execute(text("SELECT company_id FROM company WHERE name = :n"), {"n": company}).fetchone()
    return row[0] if row else None


def save_to_db(engine, company: str, ticker: str, benchmark: str, result: dict) -> None:
    with engine.begin() as conn:
        company_id = _get_company_id(conn, company)
        if company_id is None:
            print(f"  *** no company_id found for '{company}' - not saved (run the loader first)")
            return
        conn.execute(text("""
            INSERT INTO market_risk
                (company, company_id, ticker, benchmark, period_start, period_end, n_observations,
                 annualized_volatility, benchmark_volatility, sharpe_ratio, beta,
                 correlation, rolling_corr_mean, rolling_corr_min, rolling_corr_max,
                 max_drawdown, computed_at)
            VALUES
                (:company, :company_id, :ticker, :benchmark, :pstart, :pend, :n,
                 :vol, :bvol, :sharpe, :beta, :corr, :rc_mean, :rc_min, :rc_max,
                 :mdd, now())
            ON CONFLICT (company_id, benchmark)
            DO UPDATE SET company = EXCLUDED.company, ticker = EXCLUDED.ticker, period_start = EXCLUDED.period_start,
                          period_end = EXCLUDED.period_end, n_observations = EXCLUDED.n_observations,
                          annualized_volatility = EXCLUDED.annualized_volatility,
                          benchmark_volatility = EXCLUDED.benchmark_volatility,
                          sharpe_ratio = EXCLUDED.sharpe_ratio, beta = EXCLUDED.beta,
                          correlation = EXCLUDED.correlation,
                          rolling_corr_mean = EXCLUDED.rolling_corr_mean,
                          rolling_corr_min = EXCLUDED.rolling_corr_min,
                          rolling_corr_max = EXCLUDED.rolling_corr_max,
                          max_drawdown = EXCLUDED.max_drawdown, computed_at = now()
        """), {
            "company": company, "company_id": company_id, "ticker": ticker, "benchmark": benchmark,
            "pstart": result["period_start"], "pend": result["period_end"],
            "n": result["n_observations"], "vol": result["annualized_volatility"],
            "bvol": result["benchmark_volatility"], "sharpe": result["sharpe_ratio"],
            "beta": result["beta"], "corr": result["correlation"],
            "rc_mean": result["rolling_corr_mean"], "rc_min": result["rolling_corr_min"],
            "rc_max": result["rolling_corr_max"], "mdd": result["max_drawdown"],
        })


# ---------------------------------------------------------------- main

def compute_market_risk(ticker: str, benchmark: str, period: str, risk_free_rate: float) -> dict:
    stock_returns = fetch_daily_returns(ticker, period)
    stock_prices = fetch_price_series(ticker, period)
    bench_returns = fetch_daily_returns(benchmark, period)

    if stock_returns.empty:
        raise ValueError(f"No price history returned for {ticker} (period={period}) - "
                          f"check the ticker is correct and yfinance can reach it.")
    if bench_returns.empty:
        raise ValueError(f"No price history returned for benchmark {benchmark} - "
                          f"check the ticker is correct and yfinance can reach it.")

    beta, n_aligned = compute_beta(stock_returns, bench_returns)
    rolling = rolling_correlation_summary(stock_returns, bench_returns)

    return {
        "period_start": stock_prices.index.min().date(),
        "period_end": stock_prices.index.max().date(),
        "n_observations": n_aligned,
        "annualized_volatility": annualized_volatility(stock_returns),
        "benchmark_volatility": annualized_volatility(bench_returns),
        "sharpe_ratio": sharpe_ratio(stock_returns, risk_free_rate),
        "beta": beta,
        "correlation": compute_correlation(stock_returns, bench_returns),
        "rolling_corr_mean": rolling["mean"],
        "rolling_corr_min": rolling["min"],
        "rolling_corr_max": rolling["max"],
        "max_drawdown": max_drawdown(stock_prices),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", required=True)
    ap.add_argument("--period", default="1y",
                     help="yfinance history period (default 1y, e.g. 6mo/2y/5y)")
    ap.add_argument("--benchmark", default=DEFAULT_BENCHMARK,
                     help=f"Benchmark ticker (default {DEFAULT_BENCHMARK} = STOXX Europe 600)")
    ap.add_argument("--risk-free-rate", type=float, default=DEFAULT_RISK_FREE_RATE)
    ap.add_argument("--no-db", action="store_true")
    args = ap.parse_args()

    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not found. Check your .env file.")
        sys.exit(1)
    engine = create_engine(db_url)

    # PLAN.md WP4: prefer TICKER_MAP (hand-verified) but fall back to the
    # DB-resolved ticker (26_entity_resolution.py) for anything not in it
    # - see 19_valuation.py's resolve_ticker_currency() docstring.
    with engine.connect() as _conn:
        db_row = _conn.execute(text(
            "SELECT ticker, ticker_currency FROM company WHERE name = :n"
        ), {"n": args.company}).fetchone()
    db_ticker, db_currency = (db_row.ticker, db_row.ticker_currency) if db_row else (None, None)
    ticker, _quote_ccy = val19.resolve_ticker_currency(args.company, db_ticker, db_currency)
    if not ticker:
        print(f"No ticker mapped for {args.company} (checked 19_valuation.py's TICKER_MAP and "
              f"company.ticker) - cannot fetch price history. Run scripts/26_entity_resolution.py "
              f"or add it to TICKER_MAP first.")
        sys.exit(1)

    print(f"Fetching {args.period} of daily price history for {ticker} vs {args.benchmark}...")
    try:
        result = compute_market_risk(ticker, args.benchmark, args.period, args.risk_free_rate)
    except ValueError as e:
        print(f"Cannot compute market risk: {e}")
        sys.exit(1)

    print(f"\n{args.company} ({ticker}) vs {args.benchmark}")
    print(f"  Window: {result['period_start']} to {result['period_end']} "
          f"({result['n_observations']} aligned trading days)")
    print(f"  Annualized volatility: {result['annualized_volatility']:.1%} "
          f"(benchmark: {result['benchmark_volatility']:.1%})")
    print(f"  Sharpe ratio: {result['sharpe_ratio']:.2f} "
          f"(risk-free rate: {args.risk_free_rate:.1%})")
    print(f"  Beta vs {args.benchmark}: {result['beta']:.2f}")
    print(f"  Correlation vs {args.benchmark}: {result['correlation']:.2f}")
    print(f"  {ROLLING_WINDOW}-day rolling correlation: "
          f"mean {result['rolling_corr_mean']:.2f} "
          f"(range {result['rolling_corr_min']:.2f} to {result['rolling_corr_max']:.2f})")
    print(f"  Max drawdown: {result['max_drawdown']:.1%}")

    if not args.no_db:
        ensure_market_risk_table(engine)
        save_to_db(engine, args.company, ticker, args.benchmark, result)
        print(f"\nWrote market risk result to database")
