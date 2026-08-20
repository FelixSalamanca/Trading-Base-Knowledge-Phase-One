"""The guarantees that keep source claims from becoming facts.

Each test here defends one property of the design. If any of them fails, the
knowledge base has stopped distinguishing what an author said from what we
measured, which is the only thing it exists to do.
"""

from __future__ import annotations

import pytest

from kb.schema import (
    Concept,
    Hypothesis,
    SchemaError,
    SourceRef,
    Validation,
)

# ---------------------------------------------------------------------------
# A. SOURCE -- has no truth value, by construction
# ---------------------------------------------------------------------------


def make_concept(**overrides) -> dict:
    data = {
        "id": "test-concept",
        "category": "breakouts",
        "claim": "Breakouts continue in the direction of the break.",
        "source": {"book": "A Book", "author": "An Author", "year": 2003, "page": "12"},
        "evidence_quality": "anecdote",
        "stance": "contradicts",
    }
    data.update(overrides)
    return data


def test_a_concept_has_nowhere_to_record_that_it_is_true():
    """The central guarantee of the SOURCE state.

    If a truth field is ever added here, a book claim becomes indistinguishable
    from a measured result and the whole separation collapses.
    """
    concept = Concept.from_dict(make_concept())
    forbidden = {"valid", "true", "works", "confidence", "score", "proven", "reliable"}
    assert not (set(vars(concept)) & forbidden)


def test_a_concept_can_only_be_stated_as_a_claim():
    concept = Concept.from_dict(make_concept())
    statement = concept.as_statement()
    assert statement.startswith("An Author proposes:")
    assert "[SOURCE, unvalidated]" in statement


def test_attribution_is_mandatory():
    with pytest.raises(SchemaError, match="book and author"):
        Concept.from_dict(make_concept(source={"book": "A Book", "author": ""}))


def test_unknown_category_is_rejected():
    with pytest.raises(SchemaError, match="category"):
        Concept.from_dict(make_concept(category="vibes"))


def test_unknown_stance_is_rejected():
    with pytest.raises(SchemaError, match="stance"):
        Concept.from_dict(make_concept(stance="probably_fine"))


def test_evidence_quality_is_constrained():
    """"The author showed three charts" and "the author ran 20 years" differ."""
    with pytest.raises(SchemaError, match="evidence_quality"):
        Concept.from_dict(make_concept(evidence_quality="convincing"))


def test_citation_renders_with_locator():
    ref = SourceRef(book="A Book", author="An Author", year=2003, page="12")
    assert ref.cite() == "A Book, An Author, 2003, 12"


# ---------------------------------------------------------------------------
# B. HYPOTHESIS -- must be refutable to exist
# ---------------------------------------------------------------------------


def make_hypothesis(**overrides) -> dict:
    data = {
        "id": "test-hypothesis",
        "category": "breakouts",
        "statement": "Retest entries beat immediate breakout entries.",
        "measurable_as": "Compare expectancy with InpBreakoutRetestMode on and off.",
        "invalidated_by": "No difference in expectancy.",
        "testability": "needs_new_data",
        "sample_required": 3106,
    }
    data.update(overrides)
    return data


def test_a_hypothesis_without_a_measurement_is_rejected():
    with pytest.raises(SchemaError, match="measurable_as"):
        Hypothesis.from_dict(make_hypothesis(measurable_as=""))


def test_a_hypothesis_nothing_could_refute_is_rejected():
    """A claim no result could overturn is not a hypothesis."""
    with pytest.raises(SchemaError, match="not a hypothesis"):
        Hypothesis.from_dict(make_hypothesis(invalidated_by=""))


def test_sample_size_must_be_stated():
    with pytest.raises(SchemaError, match="sample_required"):
        Hypothesis.from_dict(make_hypothesis(sample_required=None))


def test_claiming_a_result_requires_naming_the_validation():
    """Nobody may mark a hypothesis 'supported' by being persuaded."""
    with pytest.raises(SchemaError, match="reference the validation"):
        Hypothesis.from_dict(make_hypothesis(status="supported"))


def test_a_supported_hypothesis_with_a_validation_is_accepted():
    hypothesis = Hypothesis.from_dict(
        make_hypothesis(status="supported", validation_id="v-001")
    )
    assert hypothesis.validation_id == "v-001"
    assert "SUPPORTED" in hypothesis.as_statement()


def test_unfalsifiable_entries_are_allowed_but_must_admit_it():
    hypothesis = Hypothesis.from_dict(
        {
            "id": "h-mindset",
            "category": "psychology",
            "statement": "Belief shapes results.",
            "testability": "not_measurable",
            "status": "unfalsifiable",
        }
    )
    assert hypothesis.status == "unfalsifiable"
    assert "not measurable" in hypothesis.as_statement()


def test_unfalsifiable_cannot_pretend_to_be_testable():
    with pytest.raises(SchemaError, match="not_measurable"):
        Hypothesis.from_dict(
            {
                "id": "h-mindset",
                "category": "psychology",
                "statement": "Belief shapes results.",
                "testability": "existing_data_full",
                "status": "unfalsifiable",
            }
        )


def test_an_untested_hypothesis_says_so_when_stated():
    hypothesis = Hypothesis.from_dict(make_hypothesis())
    assert "[HYPOTHESIS, untested]" in hypothesis.as_statement()


# ---------------------------------------------------------------------------
# C. VALIDATED -- cannot be forged by conviction
# ---------------------------------------------------------------------------


def make_validation(**overrides) -> dict:
    data = {
        "id": "v-001",
        "hypothesis_id": "test-hypothesis",
        "tested_on": "2026-08-19",
        "symbols": ["EURUSD", "XAUUSD"],
        "timeframe": "PERIOD_M5",
        "n": 3106,
        "result": "supported",
        "metrics": {"win_rate": 55.1, "expectancy": 0.041, "sample_size": 3106},
        "p_value": 0.012,
        "effect_size": 0.09,
        "dataset_sha256": "a" * 64,
    }
    data.update(overrides)
    return data


def test_validation_requires_a_dataset_hash_of_the_right_shape():
    """Without a real digest the result cannot be reproduced or checked."""
    with pytest.raises(SchemaError, match="64-character digest"):
        Validation.from_dict(make_validation(dataset_sha256="trust me"))


def test_validation_requires_a_sample_size():
    with pytest.raises(SchemaError, match="n must be positive"):
        Validation.from_dict(make_validation(n=0))


def test_validation_requires_a_p_value_in_range():
    with pytest.raises(SchemaError, match="p_value out of range"):
        Validation.from_dict(make_validation(p_value=1.4))


def test_validation_requires_the_core_metrics():
    with pytest.raises(SchemaError, match="expectancy"):
        Validation.from_dict(
            make_validation(metrics={"win_rate": 55.1, "sample_size": 3106})
        )


def test_validation_must_record_which_symbols_were_tested():
    with pytest.raises(SchemaError, match="symbols"):
        Validation.from_dict(make_validation(symbols=[]))


def test_validation_reports_numbers_not_adjectives():
    validation = Validation.from_dict(make_validation())
    statement = validation.as_statement()
    assert "n=3,106" in statement
    assert "p=0.0120" in statement
    assert "EURUSD" in statement


def test_result_vocabulary_is_closed():
    with pytest.raises(SchemaError, match="result"):
        Validation.from_dict(make_validation(result="looks good"))


def test_results_is_a_classvar_not_a_field():
    """A bare annotation would give every record its own mutable vocabulary."""
    validation = Validation.from_dict(make_validation())
    assert "RESULTS" not in vars(validation)
