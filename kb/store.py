"""Load the knowledge base from disk and hold it in one queryable object.

Layout on disk:

    extraction/<book-slug>/book.yaml        book metadata
    extraction/<book-slug>/concepts.yaml    SOURCE knowledge
    registry/hypotheses.yaml                HYPOTHESIS registry
    registry/validations/<id>.yaml          VALIDATED knowledge

Loading is strict. A malformed record raises rather than being skipped,
because a knowledge base that quietly drops what it cannot parse is worse than
one that refuses to start: you would never know what was missing.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from kb.schema import Concept, Hypothesis, SchemaError, Validation

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTRACTION_DIR = REPO_ROOT / "extraction"
REGISTRY_DIR = REPO_ROOT / "registry"
SOURCES_DIR = REPO_ROOT / "sources"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_yaml(path: Path) -> object:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@dataclass
class Book:
    """One extraction module: a book plus everything drawn from it."""

    slug: str
    title: str
    author: str
    year: int | None = None
    source_file: str | None = None
    source_sha256: str | None = None
    categories: list[str] = field(default_factory=list)
    pages: int | None = None
    extraction_status: str = "not_started"
    notes: str = ""

    @classmethod
    def from_dict(cls, slug: str, data: dict) -> Book:
        return cls(
            slug=slug,
            title=data.get("title", slug),
            author=data.get("author", "unknown"),
            year=data.get("year"),
            source_file=data.get("source_file"),
            source_sha256=data.get("source_sha256"),
            categories=list(data.get("categories", [])),
            pages=data.get("pages"),
            extraction_status=data.get("extraction_status", "not_started"),
            notes=data.get("notes", ""),
        )


@dataclass
class KnowledgeBase:
    books: dict[str, Book] = field(default_factory=dict)
    concepts: dict[str, Concept] = field(default_factory=dict)
    hypotheses: dict[str, Hypothesis] = field(default_factory=dict)
    validations: dict[str, Validation] = field(default_factory=dict)
    #: Which book each concept came from.
    concept_book: dict[str, str] = field(default_factory=dict)

    # -- integrity ---------------------------------------------------------

    def check_references(self) -> list[str]:
        """Every cross-reference must resolve. Returns the problems found.

        A dangling reference is how a knowledge base rots: a hypothesis that
        cites a concept nobody can find, or a status claiming a validation that
        was never written.
        """
        problems: list[str] = []

        for concept in self.concepts.values():
            for other in concept.conflicts_with:
                if other not in self.concepts:
                    problems.append(
                        f"concept {concept.id!r} conflicts_with unknown concept {other!r}"
                    )

        for hypothesis in self.hypotheses.values():
            for origin in hypothesis.derived_from:
                if origin not in self.concepts:
                    problems.append(
                        f"hypothesis {hypothesis.id!r} derived_from unknown concept {origin!r}"
                    )
            if hypothesis.validation_id and hypothesis.validation_id not in self.validations:
                problems.append(
                    f"hypothesis {hypothesis.id!r} references missing "
                    f"validation {hypothesis.validation_id!r}"
                )

        for validation in self.validations.values():
            if validation.hypothesis_id not in self.hypotheses:
                problems.append(
                    f"validation {validation.id!r} tests unknown "
                    f"hypothesis {validation.hypothesis_id!r}"
                )

        return problems

    def unbacked_claims(self) -> list[str]:
        """Hypotheses claiming a result without a validation to support it.

        The single most important integrity check in the package: it is what
        stops "supported" from meaning "someone was convinced".
        """
        problems = []
        for hypothesis in self.hypotheses.values():
            if hypothesis.status in ("supported", "rejected") and not hypothesis.validation_id:
                problems.append(
                    f"hypothesis {hypothesis.id!r} claims status "
                    f"{hypothesis.status!r} with no validation"
                )
        return problems

    # -- views -------------------------------------------------------------

    def concepts_in(self, category: str) -> list[Concept]:
        return sorted(
            (c for c in self.concepts.values() if c.category == category),
            key=lambda c: c.id,
        )

    def conflicts(self) -> list[tuple[Concept, Concept]]:
        """Pairs of concepts that disagree, each pair reported once."""
        seen: set[tuple[str, str]] = set()
        pairs: list[tuple[Concept, Concept]] = []
        for concept in self.concepts.values():
            for other_id in concept.conflicts_with:
                other = self.concepts.get(other_id)
                if other is None:
                    continue
                key = tuple(sorted((concept.id, other_id)))
                if key in seen:
                    continue
                seen.add(key)
                pairs.append((concept, other))
        return pairs

    def untested(self) -> list[Hypothesis]:
        """Testable, still open, cheapest to answer first."""
        order = {
            "existing_data_full": 0,
            "existing_data_censored": 1,
            "needs_new_data": 2,
            "needs_external_data": 3,
        }
        open_ones = [
            h for h in self.hypotheses.values()
            if h.status == "untested" and h.testability != "not_measurable"
        ]
        return sorted(
            open_ones,
            key=lambda h: (order.get(h.testability, 9), h.sample_required or 10**9),
        )

    def unfalsifiable(self) -> list[Hypothesis]:
        return sorted(
            (h for h in self.hypotheses.values() if h.status == "unfalsifiable"),
            key=lambda h: h.id,
        )

    def for_felix_input(self, name: str) -> list[Concept]:
        return sorted(
            (c for c in self.concepts.values() if name in c.felix_inputs),
            key=lambda c: c.id,
        )

    def validated(self) -> list[tuple[Hypothesis, Validation]]:
        out = []
        for validation in self.validations.values():
            hypothesis = self.hypotheses.get(validation.hypothesis_id)
            if hypothesis is not None:
                out.append((hypothesis, validation))
        return sorted(out, key=lambda pair: pair[1].tested_on, reverse=True)


def load(root: Path | None = None) -> KnowledgeBase:
    """Read every extraction module and the registry into one object."""
    root = root or REPO_ROOT
    extraction_dir = root / "extraction"
    registry_dir = root / "registry"

    kb = KnowledgeBase()

    if extraction_dir.exists():
        for book_dir in sorted(p for p in extraction_dir.iterdir() if p.is_dir()):
            meta = _read_yaml(book_dir / "book.yaml") or {}
            book = Book.from_dict(book_dir.name, meta)
            kb.books[book.slug] = book

            raw_concepts = _read_yaml(book_dir / "concepts.yaml") or []
            if not isinstance(raw_concepts, list):
                raise SchemaError(
                    f"{book_dir / 'concepts.yaml'}: expected a list of concepts"
                )
            for entry in raw_concepts:
                concept = Concept.from_dict(entry)
                if concept.id in kb.concepts:
                    raise SchemaError(f"duplicate concept id {concept.id!r}")
                kb.concepts[concept.id] = concept
                kb.concept_book[concept.id] = book.slug

    raw_hypotheses = _read_yaml(registry_dir / "hypotheses.yaml") or []
    if not isinstance(raw_hypotheses, list):
        raise SchemaError(f"{registry_dir / 'hypotheses.yaml'}: expected a list")
    for entry in raw_hypotheses:
        hypothesis = Hypothesis.from_dict(entry)
        if hypothesis.id in kb.hypotheses:
            raise SchemaError(f"duplicate hypothesis id {hypothesis.id!r}")
        kb.hypotheses[hypothesis.id] = hypothesis

    validations_dir = registry_dir / "validations"
    if validations_dir.exists():
        for path in sorted(validations_dir.glob("*.yaml")):
            data = _read_yaml(path) or {}
            data.setdefault("id", path.stem)
            validation = Validation.from_dict(data)
            kb.validations[validation.id] = validation

    return kb
