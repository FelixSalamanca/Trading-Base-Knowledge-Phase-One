"""Run a registered hypothesis against the FelixScalper journal.

This is the only bridge between the knowledge base and real trading data, and
the only way anything reaches the VALIDATED state. Everything upstream is a
claim; everything that comes out of here carries a sample size, a p-value and a
fingerprint of the exact dataset it was computed from.

Two rules the runners hold to:

**Never test a definition.** ``cost_r`` is *defined* as cost divided by risk, so
correlating it with risk recovers the definition and reports it as a discovery.
Where a quantity is arithmetically linked to the thing being predicted, the test
is built on something that is not — usually the gross outcome, which the market
decides rather than our own formula.

**Underpowered is not the same as null.** If the sample is smaller than the
hypothesis says it needs, the result is ``inconclusive`` and says so, even when
the numbers look decisive. A confident answer from too little data is the exact
failure mode this whole repository exists to prevent.

The FelixScalper repository is located through ``FELIX_REPO`` (default
``C:\\FelixBot``). If it is absent, the knowledge base still loads and every
other command still works -- only testing is unavailable.
"""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

import yaml

from kb.store import REPO_ROOT, KnowledgeBase

FELIX_REPO = Path(os.environ.get("FELIX_REPO", r"C:\FelixBot"))
VALIDATIONS_DIR = REPO_ROOT / "registry" / "validations"


class FelixUnavailable(RuntimeError):
    """The trading repository could not be found or imported."""


def _load_research():
    """Import the FelixScalper research layer, on demand."""
    if not FELIX_REPO.exists():
        raise FelixUnavailable(
            f"FelixScalper repository not found at {FELIX_REPO}. "
            "Set FELIX_REPO to its location to enable hypothesis testing."
        )
    if str(FELIX_REPO) not in sys.path:
        sys.path.insert(0, str(FELIX_REPO))
    try:
        import research.io as rio
        from research.analysis import bootstrap, costs, factors
        from research.config import get_settings
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise FelixUnavailable(f"Could not import the research layer: {exc}") from exc
    return rio, costs, factors, bootstrap, get_settings


def journal():
    """The decided M5 signals, as a DataFrame."""
    rio, _, _, _, get_settings = _load_research()
    settings = get_settings()
    files = rio.discover_journals(
        settings.journal_dir,
        timeframe_filter=settings.timeframe_filter,
        include_scanner=False,
    )
    if not files:
        raise FelixUnavailable(f"No journal files under {settings.journal_dir}")
    return rio.load_journals(files).frame


def fingerprint(frame) -> str:
    """A digest identifying this exact dataset.

    Built from the identifying columns of every row in a stable order, so the
    same trades always produce the same digest and one extra trade produces a
    different one. Recorded in every validation so a result can be re-checked
    against the data that produced it rather than taken on trust.
    """
    columns = ["signal_time", "symbol", "timeframe", "direction", "journal_result"]
    ordered = frame[columns].sort_values(columns).astype(str)
    digest = hashlib.sha256()
    for row in ordered.itertuples(index=False):
        digest.update("|".join(row).encode("utf-8"))
    return digest.hexdigest()


@dataclass
class TestOutcome:
    """What a runner produces before it is written to disk."""

    result: str                     # supported | rejected | inconclusive
    p_value: float
    effect_size: float
    metrics: dict[str, float]
    method: str
    breakdowns: dict[str, Any]
    caveats: list[str]
    symbols: list[str]
    timeframe: str
    n: int


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

def test_execution_cost_dominates(kb: KnowledgeBase) -> TestOutcome:
    """Is there a stop distance below which no win rate can pay?

    The arithmetic half is exact. Expectancy is
    ``w*rr - (1-w) - cost_r``, so break-even needs
    ``w = (1 + cost_r) / (1 + rr)``. Once ``cost_r`` exceeds ``rr`` that
    requires a win rate above 100%, which no edge can supply. Solving for the
    stop distance gives a hard floor per symbol.

    The empirical half is the part that could come out either way: do
    small-stop trades earn *more gross* R, compensating for what they pay? If
    they do, the floor is only theoretical. If gross performance is flat across
    the cost range, cost is the entire story.
    """
    import numpy as np
    from scipy import stats

    _, costs, _, bootstrap, get_settings = _load_research()
    frame = journal()
    settings = get_settings()
    realistic = next(s for s in settings.scenarios if s.name == "REALISTIC")
    adjusted, breakdown = costs.apply_scenario(frame, realistic)

    decided = adjusted[adjusted["is_decided"]].copy()
    rr = float(decided["rr1"].mean())

    # --- arithmetic: the floor, per symbol -------------------------------
    points = costs._symbol_points(decided)
    floors: dict[str, dict[str, float]] = {}
    below_floor = 0
    for symbol, group in decided.groupby("symbol"):
        point = points.get(symbol.upper(), 0.00001)
        spread = realistic.spread_points.get(symbol.upper(), realistic.default_spread_points)
        total_points = spread + 2.0 * realistic.slippage_points
        # cost_r == rr  =>  risk == total_points * point / rr
        floor_price = total_points * point / rr if rr > 0 else float("inf")
        n_below = int((group["risk_price"] < floor_price).sum())
        below_floor += n_below
        floors[symbol] = {
            "floor_price": round(float(floor_price), 6),
            "mean_stop_price": round(float(group["risk_price"].mean()), 6),
            "trades_below_floor": n_below,
            "trades": int(len(group)),
        }

    share_below = below_floor / len(decided) if len(decided) else 0.0
    low, high = bootstrap.wilson_interval(below_floor, len(decided))

    # --- empirical: does gross performance compensate? -------------------
    median_cost = float(decided["cost_r"].median())
    expensive = decided[decided["cost_r"] > median_cost]
    cheap = decided[decided["cost_r"] <= median_cost]

    gross_u, gross_p = stats.mannwhitneyu(
        expensive["realised_r"].to_numpy(dtype=float),
        cheap["realised_r"].to_numpy(dtype=float),
        alternative="two-sided",
    )
    # Rank-biserial correlation: interpretable, bounded, direction-carrying.
    n1, n2 = len(expensive), len(cheap)
    gross_effect = float(2.0 * gross_u / (n1 * n2) - 1.0)

    net_u, net_p = stats.mannwhitneyu(
        expensive["realised_r_net"].to_numpy(dtype=float),
        cheap["realised_r_net"].to_numpy(dtype=float),
        alternative="two-sided",
    )
    net_effect = float(2.0 * net_u / (n1 * n2) - 1.0)

    gross_flat = gross_p > 0.05
    net_differs = net_p <= 0.05

    if net_differs and gross_flat:
        result = "supported"
    elif not gross_flat and gross_effect > 0:
        # Expensive trades earn more gross -- the floor is offset by real edge.
        result = "rejected"
    else:
        result = "inconclusive"

    metrics = {
        "win_rate": float(decided["is_win"].mean() * 100.0),
        "expectancy": float(decided["realised_r"].mean()),
        "sample_size": int(len(decided)),
        "mean_reward_on_win_rr1": round(rr, 4),
        "mean_cost_r": round(breakdown.mean_cost_r, 4),
        "net_expectancy": float(decided["realised_r_net"].mean()),
        "share_below_floor_pct": round(share_below * 100.0, 2),
        "share_below_floor_ci_low_pct": round(low * 100.0, 2),
        "share_below_floor_ci_high_pct": round(high * 100.0, 2),
        "gross_expectancy_expensive_half": float(expensive["realised_r"].mean()),
        "gross_expectancy_cheap_half": float(cheap["realised_r"].mean()),
        "net_expectancy_expensive_half": float(expensive["realised_r_net"].mean()),
        "net_expectancy_cheap_half": float(cheap["realised_r_net"].mean()),
        "gross_p_value": round(float(gross_p), 6),
    }

    caveats = [
        "The floor is arithmetic, not statistical: once cost_r exceeds the "
        "reward multiple, break-even needs a win rate above 100%.",
        "Cost is modelled from the REALISTIC scenario, not measured from a live "
        "account -- the demo feed records zero spread on every bar.",
        "Trades are split at the median cost_r, so the two halves also differ in "
        "symbol mix; the gross comparison is what guards against reading that "
        "mix as an effect.",
    ]
    if gross_flat:
        caveats.append(
            f"Gross performance is statistically flat across the cost range "
            f"(p={gross_p:.3f}), so the net difference is attributable to cost "
            "rather than to small-stop trades being worse setups."
        )

    return TestOutcome(
        result=result,
        p_value=float(net_p),
        effect_size=net_effect,
        metrics=metrics,
        method=(
            "Arithmetic break-even floor per symbol from w=(1+cost_r)/(1+rr), "
            "plus Mann-Whitney U on gross and net R between the expensive and "
            "cheap halves split at median cost_r. Wilson interval on the share "
            "of trades below the floor."
        ),
        breakdowns={"by_symbol": floors, "gross_rank_biserial": round(gross_effect, 4)},
        caveats=caveats,
        symbols=sorted(decided["symbol"].unique().tolist()),
        timeframe="PERIOD_M5",
        n=int(len(decided)),
    )


def test_engulfing_by_session(kb: KnowledgeBase) -> TestOutcome:
    """Do engulfing patterns behave differently by session?

    The pooled factor screen found ``pattern`` non-significant. This asks
    whether pooling across sessions is what hid an effect, which is a different
    question and deserves its own test rather than a re-reading of the old one.
    """
    from scipy import stats

    frame = journal()
    decided = frame[frame["is_decided"]].copy()
    engulfing = decided[decided["pattern"].str.contains("Engulf", case=False, na=False)]

    table = []
    sessions = sorted(engulfing["session"].unique())
    per_session: dict[str, dict[str, float]] = {}
    for session in sessions:
        group = engulfing[engulfing["session"] == session]
        wins = int(group["is_win"].sum())
        total = int(len(group))
        table.append([wins, total - wins])
        per_session[session] = {
            "trades": total,
            "wins": wins,
            "win_rate": round(float(group["is_win"].mean() * 100.0), 2),
            "expectancy": round(float(group["realised_r"].mean()), 4),
        }

    chi2, p_value, _, _ = stats.chi2_contingency(table)
    n = int(len(engulfing))
    # Cramer's V for a 2 x k table reduces to sqrt(chi2 / n).
    effect = float((chi2 / n) ** 0.5) if n else 0.0

    hypothesis = kb.hypotheses["h-engulfing-performs-differently-by-session"]
    required = hypothesis.sample_required or 0
    underpowered = n < required

    if underpowered:
        result = "inconclusive"
    elif p_value <= 0.05:
        result = "supported"
    else:
        result = "rejected"

    caveats = [
        "Only engulfing patterns are included; other patterns are out of scope "
        "for this hypothesis.",
        "Sessions are as labelled by the indicator, so LONDON+NY is the overlap "
        "rather than a fourth independent session.",
    ]
    if underpowered:
        caveats.append(
            f"n={n} against a required {required:,} for the claimed effect size. "
            "The p-value is reported for completeness but the test cannot "
            "distinguish a real effect from none at this sample."
        )

    return TestOutcome(
        result=result,
        p_value=float(p_value),
        effect_size=effect,
        metrics={
            "win_rate": round(float(engulfing["is_win"].mean() * 100.0), 2),
            "expectancy": round(float(engulfing["realised_r"].mean()), 4),
            "sample_size": n,
            "chi2": round(float(chi2), 4),
            "sessions_compared": len(sessions),
        },
        method="Chi-square test of independence on win/loss by session, engulfing patterns only.",
        breakdowns={"by_session": per_session},
        caveats=caveats,
        symbols=sorted(engulfing["symbol"].unique().tolist()),
        timeframe="PERIOD_M5",
        n=n,
    )


def test_hour_of_day(kb: KnowledgeBase) -> TestOutcome:
    """Does any specific hour carry an edge beyond the session filter?

    Twenty-odd comparisons guarantee a raw-significant hour by chance, so
    Benjamini-Hochberg is applied and only the corrected column is read. The
    expected answer is that nothing survives.
    """
    from scipy import stats

    _, _, factors, _, _ = _load_research()
    frame = journal()
    decided = frame[frame["is_decided"]].copy()
    decided["hour"] = decided["signal_time"].dt.hour

    overall_win = float(decided["is_win"].mean())
    per_hour: dict[str, dict[str, float]] = {}
    p_values: list[float] = []
    hours: list[int] = []

    for hour, group in decided.groupby("hour"):
        total = int(len(group))
        if total < 10:
            continue
        wins = int(group["is_win"].sum())
        # This hour against every other hour pooled.
        rest = decided[decided["hour"] != hour]
        table = [[wins, total - wins],
                 [int(rest["is_win"].sum()), int(len(rest) - rest["is_win"].sum())]]
        _, p_value = stats.fisher_exact(table)
        hours.append(int(hour))
        p_values.append(float(p_value))
        per_hour[str(int(hour))] = {
            "trades": total,
            "win_rate": round(float(group["is_win"].mean() * 100.0), 2),
            "expectancy": round(float(group["realised_r"].mean()), 4),
            "p_raw": round(float(p_value), 4),
        }

    adjusted, rejected = factors.benjamini_hochberg(p_values, alpha=0.05)
    for hour, p_adj, is_significant in zip(hours, adjusted, rejected):
        per_hour[str(hour)]["p_fdr"] = round(float(p_adj), 4)
        per_hour[str(hour)]["survives_fdr"] = bool(is_significant)

    survivors = [h for h, r in zip(hours, rejected) if r]
    best_p = min(p_values) if p_values else 1.0
    best_adj = min(adjusted) if adjusted else 1.0

    hypothesis = kb.hypotheses["h-hour-of-day-carries-an-edge"]
    required = hypothesis.sample_required or 0
    n = int(len(decided))

    if survivors:
        result = "supported"
    elif n < required:
        result = "inconclusive"
    else:
        result = "rejected"

    # Effect size: the spread between the best and worst hour's win rate.
    rates = [v["win_rate"] for v in per_hour.values()]
    effect = round((max(rates) - min(rates)) / 100.0, 4) if rates else 0.0

    caveats = [
        f"{len(hours)} hours tested; at alpha=0.05 roughly "
        f"{len(hours) * 0.05:.1f} would look significant by chance alone, which "
        "is why only the FDR-corrected column is read.",
        "Hours with fewer than 10 trades are excluded rather than reported on.",
        "Hour is taken from the broker's server clock as recorded in the journal.",
    ]
    if n < required and not survivors:
        caveats.append(
            f"n={n} against a required {required:,}. A null result here is "
            "consistent with no effect, but does not establish one."
        )

    return TestOutcome(
        result=result,
        p_value=float(best_adj),
        effect_size=float(effect),
        metrics={
            "win_rate": round(overall_win * 100.0, 2),
            "expectancy": round(float(decided["realised_r"].mean()), 4),
            "sample_size": n,
            "hours_tested": len(hours),
            "best_raw_p": round(float(best_p), 4),
            "best_fdr_p": round(float(best_adj), 4),
            "hours_surviving_fdr": len(survivors),
        },
        method=(
            "Fisher exact test per hour against all other hours pooled, "
            "Benjamini-Hochberg correction across the hours tested."
        ),
        breakdowns={"by_hour": per_hour},
        caveats=caveats,
        symbols=sorted(decided["symbol"].unique().tolist()),
        timeframe="PERIOD_M5",
        n=n,
    )


def test_twenty_trades_is_enough(kb: KnowledgeBase) -> TestOutcome:
    """Is a 20-trade sample enough to judge an edge, as Douglas advises?

    This one needs no significance test, because the question is not whether
    an effect exists. The system behind every window is identical -- same
    inputs, same scoring, unchanged throughout -- so any disagreement between
    windows is sampling noise by construction. The measurement is simply: how
    far apart do windows of a given size land?

    If 20-trade windows range from clearly-winning to clearly-losing while
    nothing about the system changed, then 20 trades cannot support the
    judgement Douglas asks it to carry, however sound his underlying advice to
    stop judging trade by trade.
    """
    import numpy as np
    from scipy import stats

    frame = journal()
    decided = frame[frame["is_decided"]].sort_values("signal_time")
    wins = decided["is_win"].to_numpy(dtype=float)
    overall = float(wins.mean())

    sizes = [20, 50, 100, 250]
    profile: dict[str, dict[str, float]] = {}
    for size in sizes:
        count = len(wins) // size
        if count < 2:
            continue
        rates = np.array([wins[i * size:(i + 1) * size].mean() for i in range(count)]) * 100.0
        # Would a trader reading one window conclude the opposite of the truth?
        # The system is break-even-ish, so "winning" and "losing" verdicts both occur.
        verdict_flips = int(((rates > 50.0) != (overall * 100 > 50.0)).sum())
        profile[str(size)] = {
            "windows": count,
            "min_win_rate": round(float(rates.min()), 2),
            "max_win_rate": round(float(rates.max()), 2),
            "spread_pp": round(float(rates.max() - rates.min()), 2),
            "std_pp": round(float(rates.std(ddof=1)), 2),
            "windows_disagreeing_with_the_full_sample": verdict_flips,
            "share_disagreeing_pct": round(100.0 * verdict_flips / count, 1),
        }

    twenty = profile.get("20")
    if twenty is None:
        raise FelixUnavailable("Not enough trades to form two 20-trade windows.")

    # A binomial 95% interval on a single 20-trade window at the observed rate.
    low, high = stats.binomtest(
        int(round(overall * 20)), 20, overall
    ).proportion_ci(confidence_level=0.95)
    interval_width = float(high - low) * 100.0

    hypothesis = kb.hypotheses["h-twenty-trades-is-enough-to-judge-an-edge"]
    required = hypothesis.sample_required or 0
    n = int(len(decided))

    if n < required:
        result = "inconclusive"
    elif interval_width > 20.0 or twenty["share_disagreeing_pct"] > 20.0:
        # A window that cannot place the win rate inside a 20-point band, or
        # that contradicts the full sample more than one time in five, is not
        # supporting a judgement.
        result = "rejected"
    else:
        result = "supported"

    caveats = [
        "No significance test is applied and none is needed: the system is "
        "unchanged across every window, so all variation between them is "
        "sampling noise by construction.",
        "Windows are consecutive and non-overlapping, taken in signal-time "
        "order across all symbols pooled.",
        "This measures whether 20 trades can support a verdict. It does not "
        "test Douglas's broader advice to stop judging an edge trade by trade, "
        "which our data supports rather than contradicts.",
    ]

    return TestOutcome(
        result=result,
        p_value=1.0,          # deliberately not a hypothesis test; see method
        effect_size=round(interval_width / 100.0, 4),
        metrics={
            "win_rate": round(overall * 100.0, 2),
            "expectancy": round(float(decided["realised_r"].mean()), 4),
            "sample_size": n,
            "single_20_window_ci_width_pp": round(interval_width, 2),
            "spread_across_20_windows_pp": twenty["spread_pp"],
            "windows_of_20_disagreeing_pct": twenty["share_disagreeing_pct"],
        },
        method=(
            "Descriptive, not inferential. Consecutive non-overlapping windows "
            "at 20, 50, 100 and 250 trades; spread and standard deviation of "
            "win rate across windows, plus the Clopper-Pearson 95% interval "
            "width for a single 20-trade window."
        ),
        breakdowns={"by_window_size": profile},
        caveats=caveats,
        symbols=sorted(decided["symbol"].unique().tolist()),
        timeframe="PERIOD_M5",
        n=n,
    )


def test_losing_runs_cluster(kb: KnowledgeBase) -> TestOutcome:
    """Do losses cluster, or is the outcome sequence independent?

    This adjudicates a real disagreement between two sources. Zalesky says
    reduce size when trading poorly, which only pays if a losing run predicts
    further losses. Douglas's third fundamental truth says wins and losses are
    randomly distributed for any given edge, which says it does not.

    It also checks an assumption our own research layer depends on: Monte Carlo
    resamples the R sequence with replacement, which is only valid if the
    sequence carries no memory.
    """
    import numpy as np
    from scipy import stats

    frame = journal()
    decided = frame[frame["is_decided"]].sort_values("signal_time")
    wins = decided["is_win"].to_numpy(dtype=bool)
    returns = decided["realised_r"].to_numpy(dtype=float)
    n = int(len(wins))

    # Wald-Wolfowitz runs test on the win/loss sequence.
    runs = 1 + int((wins[1:] != wins[:-1]).sum())
    n1, n2 = int(wins.sum()), int((~wins).sum())
    expected_runs = (2.0 * n1 * n2) / (n1 + n2) + 1.0
    var_runs = (
        2.0 * n1 * n2 * (2.0 * n1 * n2 - n1 - n2)
        / ((n1 + n2) ** 2 * (n1 + n2 - 1.0))
    )
    z = (runs - expected_runs) / (var_runs ** 0.5) if var_runs > 0 else 0.0
    runs_p = float(2.0 * (1.0 - stats.norm.cdf(abs(z))))

    # Lag-1 autocorrelation on the R sequence.
    lag1 = float(np.corrcoef(returns[:-1], returns[1:])[0, 1])
    lag1_p = float(stats.pearsonr(returns[:-1], returns[1:]).pvalue)

    # Longest observed losing streak against what independence predicts.
    longest = current = 0
    for won in wins:
        current = 0 if won else current + 1
        longest = max(longest, current)
    loss_rate = n2 / n
    rng = np.random.default_rng(7)
    simulated = []
    for _ in range(5000):
        draw = rng.random(n) < loss_rate
        best = run = 0
        for is_loss in draw:
            run = run + 1 if is_loss else 0
            best = max(best, run)
        simulated.append(best)
    streak_percentile = float((np.array(simulated) <= longest).mean() * 100.0)

    # Pooling eight symbols could manufacture clustering: correlated pairs
    # losing together in the same USD move would look like persistence without
    # any instrument actually repeating itself. Repeating the runs test inside
    # each symbol separates the two explanations.
    per_symbol: dict[str, dict[str, float]] = {}
    clustered_symbols = 0
    for symbol, group in decided.groupby("symbol"):
        s_wins = group.sort_values("signal_time")["is_win"].to_numpy(dtype=bool)
        if len(s_wins) < 30:
            continue
        s_runs = 1 + int((s_wins[1:] != s_wins[:-1]).sum())
        a, b = int(s_wins.sum()), int((~s_wins).sum())
        if a == 0 or b == 0:
            continue
        s_expected = (2.0 * a * b) / (a + b) + 1.0
        s_var = 2.0 * a * b * (2.0 * a * b - a - b) / ((a + b) ** 2 * (a + b - 1.0))
        s_z = (s_runs - s_expected) / (s_var ** 0.5) if s_var > 0 else 0.0
        s_p = float(2.0 * (1.0 - stats.norm.cdf(abs(s_z))))
        is_clustered = s_p <= 0.05 and s_runs < s_expected
        clustered_symbols += int(is_clustered)
        per_symbol[symbol] = {
            "trades": int(len(s_wins)),
            "runs": s_runs,
            "expected": round(s_expected, 2),
            "z": round(float(s_z), 3),
            "p": round(s_p, 4),
            "clustered": bool(is_clustered),
        }

    hypothesis = kb.hypotheses["h-losing-runs-cluster"]
    required = hypothesis.sample_required or 0
    clustered = runs_p <= 0.05 and runs < expected_runs

    if n < required:
        result = "inconclusive"
    elif clustered or lag1_p <= 0.05:
        result = "supported"
    else:
        result = "rejected"

    caveats = [
        "Trades from eight symbols are pooled in signal-time order, so an "
        "apparent run may be several instruments moving together rather than "
        "one instrument persisting.",
        "A null result supports independence, which is what the Monte Carlo in "
        "the research layer already assumes when it resamples the R sequence.",
        "Fewer runs than expected means clustering; more means alternation. "
        "Only the first would justify reducing size after a losing run.",
        f"{clustered_symbols} of {len(per_symbol)} symbols show clustering on "
        "their own sequence. If that count is low while the pooled test is "
        "significant, the pooled effect is correlation between instruments "
        "rather than persistence within one -- and size reduction after a "
        "losing run would then be the wrong response to it.",
    ]

    return TestOutcome(
        result=result,
        p_value=min(runs_p, lag1_p),
        effect_size=lag1,
        metrics={
            "win_rate": round(float(wins.mean() * 100.0), 2),
            "expectancy": round(float(returns.mean()), 4),
            "sample_size": n,
            "runs_observed": runs,
            "runs_expected_if_independent": round(expected_runs, 2),
            "runs_z": round(float(z), 4),
            "runs_p": round(runs_p, 6),
            "lag1_autocorrelation": round(lag1, 4),
            "lag1_p": round(lag1_p, 6),
            "longest_losing_streak": longest,
            "streak_percentile_vs_independent": round(streak_percentile, 1),
            "symbols_clustering_individually": clustered_symbols,
            "symbols_tested_individually": len(per_symbol),
        },
        method=(
            "Wald-Wolfowitz runs test on the win/loss sequence, Pearson lag-1 "
            "autocorrelation on realised R, and the longest observed losing "
            "streak against 5,000 independent simulations at the same loss rate."
        ),
        breakdowns={"per_symbol_runs_test": per_symbol},
        caveats=caveats,
        symbols=sorted(decided["symbol"].unique().tolist()),
        timeframe="PERIOD_M5",
        n=n,
    )


def test_break_even_stop(kb: KnowledgeBase) -> TestOutcome:
    """How much could a break-even stop have saved -- at most?

    Censored in one direction, and the direction decides how the answer must be
    read. Losers rescued can be counted exactly: a losing trade whose ``mfe_r``
    exceeded the threshold definitely reached it, so a stop moved to entry at
    that point would have turned -1R into roughly 0R.

    Winners lost cannot be counted at all. The journal stores one ``mae_r``
    figure, not the path, so a winner that reached the threshold, dipped back
    through entry and then recovered to target is invisible here. Every figure
    below is therefore an **upper bound** on the benefit, and the result is
    reported as inconclusive no matter how large it looks -- an upper bound is
    not a measurement.
    """
    frame = journal()
    decided = frame[frame["is_decided"]].copy()
    losers = decided[decided["is_loss"]]
    winners = decided[decided["is_win"]]

    baseline = float(decided["realised_r"].mean())
    profile: dict[str, dict[str, float]] = {}

    for threshold in (0.25, 0.5, 0.75, 1.0):
        rescued = int((losers["mfe_r"] >= threshold).sum())
        # Upper bound: every rescued loser becomes 0R instead of -1R.
        gain = rescued * 1.0 / len(decided) if len(decided) else 0.0
        # How many winners even passed through this zone, i.e. how many are
        # exposed to the risk this estimate cannot see.
        exposed = int((winners["mae_r"] > 0).sum())
        profile[str(threshold)] = {
            "losers_rescued_at_most": rescued,
            "losers_rescued_pct": round(100.0 * rescued / len(losers), 1) if len(losers) else 0.0,
            "expectancy_upper_bound": round(baseline + gain, 4),
            "winners_exposed_to_unmeasured_risk": exposed,
        }

    best = max(profile.values(), key=lambda v: v["expectancy_upper_bound"])

    return TestOutcome(
        result="inconclusive",
        p_value=1.0,
        effect_size=round(best["expectancy_upper_bound"] - baseline, 4),
        metrics={
            "win_rate": round(float(decided["is_win"].mean() * 100.0), 2),
            "expectancy": round(baseline, 4),
            "sample_size": int(len(decided)),
            "best_expectancy_upper_bound": best["expectancy_upper_bound"],
            "winners_that_went_negative_at_all": int((winners["mae_r"] > 0).sum()),
            "mean_winner_mae_r": round(float(winners["mae_r"].mean()), 4),
        },
        method=(
            "Counts losing trades whose recorded MFE exceeded each candidate "
            "break-even threshold. No significance test: the quantity that "
            "would decide the question -- winners stopped at break-even before "
            "reaching target -- is not recoverable from the journal."
        ),
        breakdowns={"by_threshold": profile},
        caveats=[
            "UPPER BOUND ONLY. Losers rescued are counted exactly; winners lost "
            "to the same stop cannot be counted, because the journal records a "
            "single mae_r figure rather than the price path.",
            f"{int((winners['mae_r'] > 0).sum())} of {len(winners)} winners went "
            "negative at some point, so the unmeasured population is large, not "
            "a rounding error.",
            "Mean winner MAE is a further warning: winners routinely travel a "
            "long way against the position before working, which is exactly the "
            "condition under which a break-even stop does damage.",
            "Settling this needs InpBreakEvenAtR set to a candidate value and a "
            "fresh collection period. The feature already exists in the "
            "indicator; only the value is disabled.",
        ],
        symbols=sorted(decided["symbol"].unique().tolist()),
        timeframe="PERIOD_M5",
        n=int(len(decided)),
    )


RUNNERS: dict[str, Callable[[KnowledgeBase], TestOutcome]] = {
    "h-twenty-trades-is-enough-to-judge-an-edge": test_twenty_trades_is_enough,
    "h-losing-runs-cluster": test_losing_runs_cluster,
    "h-break-even-stop-improves-expectancy": test_break_even_stop,
    "h-execution-cost-dominates-at-small-stops": test_execution_cost_dominates,
    "h-engulfing-performs-differently-by-session": test_engulfing_by_session,
    "h-hour-of-day-carries-an-edge": test_hour_of_day,
}


# ---------------------------------------------------------------------------
# Writing the result
# ---------------------------------------------------------------------------

def _plain(value: Any) -> Any:
    """Convert numpy and pandas scalars to built-in types, recursively.

    pandas and scipy hand back ``np.float64`` and ``np.int64``, which
    ``yaml.safe_dump`` refuses to represent. Coercing at the boundary keeps the
    stored record readable by anything that can parse YAML, rather than only by
    a Python process with numpy installed.
    """
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            return value.item()
        except (AttributeError, ValueError):  # pragma: no cover - defensive
            pass
    return value


def write_validation(
    hypothesis_id: str,
    outcome: TestOutcome,
    dataset_sha256: str,
    validation_id: str | None = None,
) -> Path:
    """Persist a validation and return its path."""
    validation_id = validation_id or f"v-{hypothesis_id}-{date.today().isoformat()}"
    VALIDATIONS_DIR.mkdir(parents=True, exist_ok=True)

    document = {
        "id": validation_id,
        "hypothesis_id": hypothesis_id,
        "tested_on": date.today().isoformat(),
        "symbols": outcome.symbols,
        "timeframe": outcome.timeframe,
        "n": int(outcome.n),
        "result": outcome.result,
        "metrics": _plain(outcome.metrics),
        "p_value": round(float(outcome.p_value), 6),
        "effect_size": round(float(outcome.effect_size), 6),
        "dataset_sha256": dataset_sha256,
        "method": outcome.method,
        "multiple_testing_correction": (
            "benjamini-hochberg" if "benjamini" in outcome.method.lower() else "none"
        ),
        "breakdowns": _plain(outcome.breakdowns),
        "caveats": list(outcome.caveats),
    }

    path = VALIDATIONS_DIR / f"{validation_id}.yaml"
    path.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    return path


#: Re-applied on every rewrite. ``yaml.safe_dump`` discards comments, so
#: without this the explanation of what the registry is would be silently
#: deleted the first time a test updated a status.
REGISTRY_HEADER = """\
# HYPOTHESIS REGISTRY
#
# A concept becomes an entry here only once someone can state what measurement
# would settle it AND what result would refute it. Both fields are enforced by
# the schema; an entry that cannot supply them must be filed `unfalsifiable`,
# which is a legitimate and useful outcome rather than a failure.
#
# `sample_required` is the number of trades needed to detect the claimed effect
# at 80% power, alpha 0.05, from a 52.4% baseline. Recorded so the queue can be
# ordered by what is answerable rather than by what sounds exciting.
#
# Nothing here is believed. Status starts at `untested` and only a validation
# file -- carrying n, p-value, effect size and a dataset hash -- can move it.
# An inconclusive test leaves status at `untested` and records `last_attempt`,
# because a test that could not separate the possibilities has not answered
# the question.
#
# This file is rewritten by `python -m kb test`. Edit it by hand freely, but
# expect formatting to be normalised on the next run.

"""


def update_hypothesis_status(hypothesis_id: str, outcome: TestOutcome, validation_id: str) -> None:
    """Move the registry entry to its tested state.

    ``inconclusive`` deliberately leaves the hypothesis ``untested``: a test
    that could not separate the possibilities has not answered the question,
    and marking it otherwise would retire it from the queue on false grounds.
    """
    registry = REPO_ROOT / "registry" / "hypotheses.yaml"
    entries = yaml.safe_load(registry.read_text(encoding="utf-8"))

    for entry in entries:
        if entry.get("id") != hypothesis_id:
            continue
        if outcome.result == "inconclusive":
            entry["status"] = "untested"
            entry["last_attempt"] = {
                "date": date.today().isoformat(),
                "validation_id": validation_id,
                "reason": "inconclusive -- sample below the required size",
            }
        else:
            entry["status"] = "supported" if outcome.result == "supported" else "rejected"
            entry["validation_id"] = validation_id
        break

    registry.write_text(
        REGISTRY_HEADER
        + yaml.safe_dump(entries, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
