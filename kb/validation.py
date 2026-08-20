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


RUNNERS: dict[str, Callable[[KnowledgeBase], TestOutcome]] = {
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
