"""The three knowledge states, as three separate types.

The whole point of this package is that a claim from a book and a fact
established from our own trading data are different kinds of thing, and must
never be stored in a way that lets one quietly become the other.

    SOURCE      what an author claims          extraction/<book>/concepts.yaml
       |        (no truth value attached)
       v
    HYPOTHESIS  a claim made measurable        registry/hypotheses.yaml
       |        (conditions, metric, sample)
       v
    VALIDATED   a hypothesis actually tested   registry/validations/<id>.yaml
                (n, p-value, dataset hash)

The separation is structural, not a convention someone has to remember:

* ``Concept`` has no field in which truth could be recorded. There is nowhere
  to write "this works". The most it can carry is what the author asserted and
  what evidence the author offered for it.
* ``Hypothesis`` must name the measurement and the invalidating conditions
  before it is allowed to exist. A claim nobody can specify a test for is
  marked ``unfalsifiable`` and never enters the queue.
* ``Validation`` cannot be written convincingly by hand. It requires the sample
  size, the p-value, the effect size, the symbols and timeframe tested, and the
  SHA-256 of the dataset it was computed from. Omit any of them and validation
  fails.

So the only path from "a book says so" to "our system believes it" runs through
a real measurement on our own data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, ClassVar

#: Default metrics and breakdowns for a hypothesis. Module level rather than
#: class level because a dataclass strips attributes that use default_factory,
#: so ``Hypothesis.metrics`` does not exist to read back from.
DEFAULT_METRICS: tuple[str, ...] = (
    "win_rate",
    "expectancy",
    "average_r",
    "profit_factor",
    "max_drawdown_r",
    "sample_size",
)

DEFAULT_BREAKDOWNS: tuple[str, ...] = ("regime", "symbol", "timeframe", "session")

# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------

#: Topic taxonomy. Kept flat on purpose: a deep tree invites arguments about
#: where something belongs instead of what it claims.
CATEGORIES: tuple[str, ...] = (
    "psychology",
    "market_structure",
    "technical_analysis",
    "price_action",
    "candlestick_patterns",
    "support_resistance",
    "momentum",
    "volatility",
    "breakouts",
    "mean_reversion",
    "scalping",
    "swing_trading",
    "risk_management",
    "position_sizing",
    "trade_management",
    "macro_economic",
    "trading_discipline",
    "strategy_concepts",
)

#: How a concept stands relative to what FelixScalper already does. This is the
#: field that turns a shelf of books into something worth querying: it sorts
#: every claim into "we do this", "we do the opposite", "this is new".
STANCES: tuple[str, ...] = ("agrees", "contradicts", "extends", "unrelated", "unknown")

#: What the author actually offered as support. Recorded because "the author
#: showed three days of cherry-picked charts" and "the author ran 20 years of
#: data" are not the same claim, and the difference is invisible once the idea
#: has been paraphrased into a bullet point.
EVIDENCE_QUALITY: tuple[str, ...] = (
    "none",            # asserted, no support offered
    "anecdote",        # a handful of hand-picked examples
    "small_sample",    # tens of trades, or a few days
    "large_sample",    # hundreds of trades or years of data
    "peer_reviewed",
)

HYPOTHESIS_STATUS: tuple[str, ...] = (
    "untested",
    "testing",
    "supported",       # tested, evidence in favour
    "rejected",        # tested, evidence against
    "inconclusive",    # tested, sample too small to separate
    "unfalsifiable",   # cannot be expressed as a measurement
)

#: Whether the 525-trade journal can already answer this, or whether it needs
#: data we do not have. Excursions are recorded in price units, so some
#: stop/target questions are answerable today -- but the journal stops tracking
#: the moment a trade resolves, which censors the answer in one direction.
TESTABILITY: tuple[str, ...] = (
    "existing_data_full",      # answerable now from what is already recorded
    "existing_data_censored",  # partly answerable; tracking stopped too early
    "needs_new_data",          # requires running a changed configuration
    "needs_external_data",     # requires data the journal never captures
    "not_measurable",
)


class SchemaError(ValueError):
    """A record does not satisfy the rules for its knowledge state."""


def _require(record: dict, name: str, where: str) -> Any:
    if name not in record or record[name] in (None, "", [], {}):
        raise SchemaError(f"{where}: missing required field {name!r}")
    return record[name]


def _check_vocab(value: str, allowed: tuple[str, ...], name: str, where: str) -> str:
    if value not in allowed:
        raise SchemaError(
            f"{where}: {name}={value!r} is not one of {', '.join(allowed)}"
        )
    return value


# ---------------------------------------------------------------------------
# A. SOURCE KNOWLEDGE
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceRef:
    """Where a claim came from. Attribution is not optional."""

    book: str
    author: str
    year: int | None = None
    chapter: str | None = None
    page: str | None = None
    publication: str | None = None

    def cite(self) -> str:
        bits = [self.book]
        if self.author:
            bits.append(self.author)
        if self.year:
            bits.append(str(self.year))
        locator = self.page or self.chapter
        if locator:
            bits.append(locator)
        return ", ".join(bits)


@dataclass
class Concept:
    """What an author claims. Deliberately has no truth value.

    There is no ``valid``, ``works``, ``confidence`` or ``score`` field, and
    that absence is the design. A concept can only ever be reported as
    "<author> proposes X", never as "X".
    """

    id: str
    category: str
    claim: str
    source: SourceRef
    #: The author's own reasoning for why the idea should work.
    rationale: str = ""
    #: Conditions the author says are required. Often the most valuable part:
    #: a setup lifted out of its stated conditions is a different setup.
    conditions: list[str] = field(default_factory=list)
    #: What the author takes for granted -- instrument, era, liquidity, costs.
    assumptions: list[str] = field(default_factory=list)
    #: What the author says would break the idea.
    invalidated_by: list[str] = field(default_factory=list)
    #: Indicators, structures or inputs the idea involves.
    mechanics: list[str] = field(default_factory=list)
    evidence_quality: str = "none"
    #: The author's claimed result, verbatim, so it can be compared with ours.
    author_claimed_result: str = ""
    #: Other concept ids this one disagrees with, across books.
    conflicts_with: list[str] = field(default_factory=list)
    #: Relation to FelixScalper as it exists today.
    stance: str = "unknown"
    felix_inputs: list[str] = field(default_factory=list)
    felix_current_behaviour: str = ""
    notes: str = ""

    def validate(self) -> None:
        where = f"concept {self.id!r}"
        if not self.id or " " in self.id:
            raise SchemaError(f"{where}: id must be a non-empty slug without spaces")
        _check_vocab(self.category, CATEGORIES, "category", where)
        _check_vocab(self.stance, STANCES, "stance", where)
        _check_vocab(self.evidence_quality, EVIDENCE_QUALITY, "evidence_quality", where)
        if not self.claim.strip():
            raise SchemaError(f"{where}: claim is empty")
        if not self.source.book or not self.source.author:
            raise SchemaError(f"{where}: source must name both book and author")

    @classmethod
    def from_dict(cls, data: dict) -> Concept:
        where = f"concept {data.get('id', '<no id>')!r}"
        raw_source = _require(data, "source", where)
        source = SourceRef(
            book=raw_source.get("book", ""),
            author=raw_source.get("author", ""),
            year=raw_source.get("year"),
            chapter=raw_source.get("chapter"),
            page=str(raw_source["page"]) if raw_source.get("page") is not None else None,
            publication=raw_source.get("publication"),
        )
        concept = cls(
            id=_require(data, "id", where),
            category=_require(data, "category", where),
            claim=_require(data, "claim", where),
            source=source,
            rationale=data.get("rationale", ""),
            conditions=list(data.get("conditions", [])),
            assumptions=list(data.get("assumptions", [])),
            invalidated_by=list(data.get("invalidated_by", [])),
            mechanics=list(data.get("mechanics", [])),
            evidence_quality=data.get("evidence_quality", "none"),
            author_claimed_result=data.get("author_claimed_result", ""),
            conflicts_with=list(data.get("conflicts_with", [])),
            stance=data.get("stance", "unknown"),
            felix_inputs=list(data.get("felix_inputs", [])),
            felix_current_behaviour=data.get("felix_current_behaviour", ""),
            notes=data.get("notes", ""),
        )
        concept.validate()
        return concept

    def as_statement(self) -> str:
        """The only sentence form a concept is allowed to be reported in."""
        return f"{self.source.author} proposes: {self.claim} [SOURCE, unvalidated]"


# ---------------------------------------------------------------------------
# B. HYPOTHESIS
# ---------------------------------------------------------------------------

@dataclass
class Hypothesis:
    """A source concept restated as something our data could settle.

    ``measurable_as`` and ``invalidated_by`` are mandatory. If nobody can say
    what measurement would change their mind, the entry belongs in the
    ``unfalsifiable`` bucket rather than the research queue -- which is where
    most psychology and discipline material honestly ends up.
    """

    id: str
    category: str
    statement: str
    #: Concept ids this was derived from. A hypothesis with no source is a
    #: hunch, which is allowed, but it must say so by listing nothing.
    derived_from: list[str] = field(default_factory=list)
    #: The concrete change or split being tested, in our own vocabulary.
    measurable_as: str = ""
    #: Metrics to report. Never win rate alone.
    metrics: list[str] = field(default_factory=lambda: list(DEFAULT_METRICS))
    #: Breakdowns required before a result counts as understood.
    breakdowns: list[str] = field(default_factory=lambda: list(DEFAULT_BREAKDOWNS))
    #: What result would make us abandon it.
    invalidated_by: str = ""
    testability: str = "needs_new_data"
    #: Trades needed for the effect size being claimed.
    sample_required: int | None = None
    status: str = "untested"
    #: Filled once a validation exists.
    validation_id: str | None = None
    felix_inputs: list[str] = field(default_factory=list)
    notes: str = ""

    def validate(self) -> None:
        where = f"hypothesis {self.id!r}"
        if not self.id or " " in self.id:
            raise SchemaError(f"{where}: id must be a non-empty slug without spaces")
        _check_vocab(self.category, CATEGORIES, "category", where)
        _check_vocab(self.status, HYPOTHESIS_STATUS, "status", where)
        _check_vocab(self.testability, TESTABILITY, "testability", where)
        if not self.statement.strip():
            raise SchemaError(f"{where}: statement is empty")

        if self.status == "unfalsifiable":
            if self.testability != "not_measurable":
                raise SchemaError(
                    f"{where}: an unfalsifiable hypothesis must have "
                    f"testability='not_measurable', got {self.testability!r}"
                )
            return

        # Everything else has to be a real, refutable proposition.
        if not self.measurable_as.strip():
            raise SchemaError(
                f"{where}: measurable_as is required unless status is 'unfalsifiable'"
            )
        if not self.invalidated_by.strip():
            raise SchemaError(
                f"{where}: invalidated_by is required -- a hypothesis no result "
                "could refute is not a hypothesis"
            )
        if self.sample_required is None:
            raise SchemaError(f"{where}: sample_required must be stated")
        if self.status in ("supported", "rejected") and not self.validation_id:
            raise SchemaError(
                f"{where}: status={self.status!r} claims a result, so it must "
                "reference the validation that produced it"
            )

    @classmethod
    def from_dict(cls, data: dict) -> Hypothesis:
        where = f"hypothesis {data.get('id', '<no id>')!r}"
        hypothesis = cls(
            id=_require(data, "id", where),
            category=_require(data, "category", where),
            statement=_require(data, "statement", where),
            derived_from=list(data.get("derived_from", [])),
            measurable_as=data.get("measurable_as", ""),
            metrics=list(data.get("metrics", [])) or list(DEFAULT_METRICS),
            breakdowns=list(data.get("breakdowns", [])) or list(DEFAULT_BREAKDOWNS),
            invalidated_by=data.get("invalidated_by", ""),
            testability=data.get("testability", "needs_new_data"),
            sample_required=data.get("sample_required"),
            status=data.get("status", "untested"),
            validation_id=data.get("validation_id"),
            felix_inputs=list(data.get("felix_inputs", [])),
            notes=data.get("notes", ""),
        )
        hypothesis.validate()
        return hypothesis

    def as_statement(self) -> str:
        if self.status == "untested":
            return f"{self.statement} [HYPOTHESIS, untested]"
        if self.status == "unfalsifiable":
            return f"{self.statement} [not measurable -- held as principle, not evidence]"
        return f"{self.statement} [{self.status.upper()}, see validation {self.validation_id}]"


# ---------------------------------------------------------------------------
# C. VALIDATED KNOWLEDGE
# ---------------------------------------------------------------------------

@dataclass
class Validation:
    """A hypothesis measured against our own trades.

    Every field below is required, and that is the safeguard. A record here
    cannot be produced by reading a book or by being persuaded: it needs a
    sample size, a p-value, an effect size, the instruments tested and the
    SHA-256 of the dataset it came from. If the numbers are not real, the
    dataset hash will not match a re-run.
    """

    id: str
    hypothesis_id: str
    tested_on: date
    symbols: list[str]
    timeframe: str
    n: int
    result: str                 # supported | rejected | inconclusive
    metrics: dict[str, float]
    p_value: float
    effect_size: float
    dataset_sha256: str
    method: str = ""
    multiple_testing_correction: str = ""
    breakdowns: dict[str, Any] = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)

    #: ClassVar, not a field -- a bare annotation here would make every
    #: Validation carry its own mutable copy of the vocabulary.
    RESULTS: ClassVar[tuple[str, ...]] = ("supported", "rejected", "inconclusive")

    def validate(self) -> None:
        where = f"validation {self.id!r}"
        if not self.hypothesis_id:
            raise SchemaError(f"{where}: must reference a hypothesis")
        _check_vocab(self.result, self.RESULTS, "result", where)
        if self.n <= 0:
            raise SchemaError(f"{where}: n must be positive, got {self.n}")
        if not 0.0 <= self.p_value <= 1.0:
            raise SchemaError(f"{where}: p_value out of range: {self.p_value}")
        if len(self.dataset_sha256) != 64:
            raise SchemaError(
                f"{where}: dataset_sha256 must be a 64-character digest so the "
                "test can be re-run against the same data"
            )
        if not self.symbols:
            raise SchemaError(f"{where}: must record which symbols were tested")
        for required in ("win_rate", "expectancy", "sample_size"):
            if required not in self.metrics:
                raise SchemaError(f"{where}: metrics missing {required!r}")

    @classmethod
    def from_dict(cls, data: dict) -> Validation:
        where = f"validation {data.get('id', '<no id>')!r}"
        tested = _require(data, "tested_on", where)
        validation = cls(
            id=data["id"],
            hypothesis_id=_require(data, "hypothesis_id", where),
            tested_on=tested if isinstance(tested, date) else date.fromisoformat(str(tested)),
            symbols=list(_require(data, "symbols", where)),
            timeframe=_require(data, "timeframe", where),
            n=int(_require(data, "n", where)),
            result=_require(data, "result", where),
            metrics=dict(_require(data, "metrics", where)),
            p_value=float(_require(data, "p_value", where)),
            effect_size=float(_require(data, "effect_size", where)),
            dataset_sha256=str(_require(data, "dataset_sha256", where)),
            method=data.get("method", ""),
            multiple_testing_correction=data.get("multiple_testing_correction", ""),
            breakdowns=dict(data.get("breakdowns", {})),
            caveats=list(data.get("caveats", [])),
        )
        validation.validate()
        return validation

    def as_statement(self) -> str:
        symbols = ", ".join(self.symbols)
        return (
            f"Tested on {symbols} {self.timeframe}, n={self.n:,}. "
            f"{self.result.upper()}: p={self.p_value:.4f}, "
            f"effect={self.effect_size:+.3f}."
        )
