"""Loading, cross-references, and the integrity of the shipped content."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from kb.schema import SchemaError
from kb.store import load, sha256_of

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Synthetic fixtures -- known answers, so a passing test proves something
# ---------------------------------------------------------------------------


def build_kb(tmp_path: Path, concepts: str, hypotheses: str) -> Path:
    book_dir = tmp_path / "extraction" / "a-book"
    book_dir.mkdir(parents=True)
    (book_dir / "book.yaml").write_text(
        "title: A Book\nauthor: An Author\nyear: 2003\n", encoding="utf-8"
    )
    (book_dir / "concepts.yaml").write_text(textwrap.dedent(concepts), encoding="utf-8")

    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "hypotheses.yaml").write_text(textwrap.dedent(hypotheses), encoding="utf-8")
    return tmp_path


ONE_CONCEPT = """
- id: c-one
  category: breakouts
  claim: Breakouts continue.
  source: {book: A Book, author: An Author}
"""

NO_HYPOTHESES = "[]\n"


def test_loads_a_minimal_knowledge_base(tmp_path):
    root = build_kb(tmp_path, ONE_CONCEPT, NO_HYPOTHESES)
    kb = load(root)
    assert set(kb.concepts) == {"c-one"}
    assert kb.concept_book["c-one"] == "a-book"
    assert not kb.check_references()


def test_duplicate_concept_ids_are_refused(tmp_path):
    root = build_kb(tmp_path, ONE_CONCEPT + ONE_CONCEPT, NO_HYPOTHESES)
    with pytest.raises(SchemaError, match="duplicate concept id"):
        load(root)


def test_a_hypothesis_citing_a_missing_concept_is_caught(tmp_path):
    root = build_kb(
        tmp_path,
        ONE_CONCEPT,
        """
        - id: h-one
          category: breakouts
          statement: Retests beat breaks.
          derived_from: [c-does-not-exist]
          measurable_as: Compare the two.
          invalidated_by: No difference.
          sample_required: 100
        """,
    )
    kb = load(root)
    problems = kb.check_references()
    assert any("unknown concept" in p for p in problems)


def test_a_conflict_pointing_nowhere_is_caught(tmp_path):
    root = build_kb(
        tmp_path,
        """
        - id: c-one
          category: breakouts
          claim: Breakouts continue.
          source: {book: A Book, author: An Author}
          conflicts_with: [c-ghost]
        """,
        NO_HYPOTHESES,
    )
    problems = load(root).check_references()
    assert any("unknown concept 'c-ghost'" in p for p in problems)


def test_conflicts_are_reported_once_not_twice(tmp_path):
    root = build_kb(
        tmp_path,
        """
        - id: c-one
          category: breakouts
          claim: Enter on the break.
          source: {book: A Book, author: An Author}
          conflicts_with: [c-two]
        - id: c-two
          category: breakouts
          claim: Wait for the retest.
          source: {book: A Book, author: An Author}
          conflicts_with: [c-one]
        """,
        NO_HYPOTHESES,
    )
    assert len(load(root).conflicts()) == 1


def test_the_queue_puts_cheap_questions_first(tmp_path):
    """A test answerable from data we already hold must outrank one that is not."""
    root = build_kb(
        tmp_path,
        ONE_CONCEPT,
        """
        - id: h-expensive
          category: breakouts
          statement: Needs a new configuration.
          measurable_as: Re-run with a changed input.
          invalidated_by: No difference.
          testability: needs_new_data
          sample_required: 3106
        - id: h-cheap
          category: breakouts
          statement: Answerable today.
          measurable_as: Group the existing journal.
          invalidated_by: No difference.
          testability: existing_data_full
          sample_required: 767
        """,
    )
    assert [h.id for h in load(root).untested()] == ["h-cheap", "h-expensive"]


def test_unfalsifiable_entries_never_enter_the_queue(tmp_path):
    root = build_kb(
        tmp_path,
        ONE_CONCEPT,
        """
        - id: h-mindset
          category: psychology
          statement: Belief shapes results.
          testability: not_measurable
          status: unfalsifiable
        """,
    )
    kb = load(root)
    assert kb.untested() == []
    assert [h.id for h in kb.unfalsifiable()] == ["h-mindset"]


def test_hashing_is_stable(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"knowledge")
    assert sha256_of(path) == sha256_of(path)
    assert len(sha256_of(path)) == 64


# ---------------------------------------------------------------------------
# The shipped content itself
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_kb():
    return load(REPO_ROOT)


def test_the_shipped_knowledge_base_is_internally_consistent(real_kb):
    assert real_kb.check_references() == []
    assert real_kb.unbacked_claims() == []


def test_nothing_is_validated_yet_so_nothing_may_influence_trading(real_kb):
    """Guards the headline promise of the project.

    The moment this fails, a validation exists -- and it should only exist
    because a real test produced one.
    """
    assert real_kb.validations == {}
    for hypothesis in real_kb.hypotheses.values():
        assert hypothesis.status in ("untested", "unfalsifiable")


def test_every_extracted_concept_carries_a_real_citation(real_kb):
    for concept in real_kb.concepts.values():
        assert concept.source.book
        assert concept.source.author
        assert concept.source.page or concept.source.chapter, (
            f"{concept.id} has no page or chapter -- attribution is required"
        )


def test_source_pdf_hashes_match_what_the_extraction_claims(real_kb):
    """A book.yaml pointing at a file that has since changed is a silent lie."""
    for book in real_kb.books.values():
        if not book.source_file or not book.source_sha256:
            continue
        path = REPO_ROOT / book.source_file
        assert path.exists(), f"{book.slug}: missing source {book.source_file}"
        assert sha256_of(path) == book.source_sha256, (
            f"{book.slug}: source file no longer matches its recorded hash"
        )


def test_concepts_that_disagree_have_a_hypothesis_to_settle_them(real_kb):
    """A recorded disagreement with no way to resolve it is just trivia."""
    for first, second in real_kb.conflicts():
        settlers = [
            h for h in real_kb.hypotheses.values()
            if first.id in h.derived_from and second.id in h.derived_from
        ]
        assert settlers, (
            f"{first.id} and {second.id} disagree but no hypothesis adjudicates them"
        )
