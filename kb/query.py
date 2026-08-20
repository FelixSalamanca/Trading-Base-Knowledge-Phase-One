"""Answer the questions the AI is supposed to be able to answer.

From the project brief:

    "What do our books say about breakouts?"
    "What different opinions exist about breakout confirmation?"
    "Which authors recommend retests?"
    "Which of these ideas have we actually tested?"
    "Which hypotheses are currently untested?"
    "What evidence contradicts this idea?"

Every function here returns text in a form that keeps the three knowledge
states distinguishable. A concept is always rendered as "<author> proposes X
[SOURCE, unvalidated]" and never as "X". That phrasing is enforced by
``Concept.as_statement`` rather than left to whoever writes the answer.
"""

from __future__ import annotations

from kb.schema import CATEGORIES, Concept
from kb.store import KnowledgeBase


def _wrap(text: str, width: int = 76, indent: str = "    ") -> str:
    words = " ".join(text.split()).split(" ")
    lines: list[str] = []
    current = indent
    for word in words:
        if len(current) + len(word) + 1 > width and current.strip():
            lines.append(current.rstrip())
            current = indent + word + " "
        else:
            current += word + " "
    if current.strip():
        lines.append(current.rstrip())
    return "\n".join(lines)


def _render_concept(kb: KnowledgeBase, concept: Concept, show_felix: bool = True) -> list[str]:
    out = [f"  [{concept.id}]  {concept.source.author} -- {concept.source.cite()}"]
    out.append(_wrap(concept.claim, indent="      "))

    if concept.conditions:
        out.append("      conditions the author states:")
        out.extend(f"        - {c}" for c in concept.conditions)
    if concept.assumptions:
        out.append("      assumptions the author makes:")
        out.extend(f"        - {a}" for a in concept.assumptions)
    if concept.invalidated_by:
        out.append("      the author says it fails when:")
        out.extend(f"        - {i}" for i in concept.invalidated_by)

    out.append(f"      author's evidence: {concept.evidence_quality}")
    if concept.author_claimed_result:
        out.append(_wrap(f"claimed: {concept.author_claimed_result}", indent="        "))

    if concept.conflicts_with:
        for other in concept.conflicts_with:
            label = kb.concepts[other].source.author if other in kb.concepts else "?"
            out.append(f"      ⚠ disagrees with [{other}] ({label})")

    if show_felix:
        out.append(f"      stance vs FelixScalper: {concept.stance}")
        if concept.felix_inputs:
            out.append(f"      touches inputs: {', '.join(concept.felix_inputs)}")
        if concept.felix_current_behaviour:
            out.append(_wrap(f"we currently: {concept.felix_current_behaviour}",
                             indent="        "))

    # Anything downstream of this concept.
    derived = [h for h in kb.hypotheses.values() if concept.id in h.derived_from]
    for hypothesis in derived:
        out.append(f"      → hypothesis [{hypothesis.id}] {hypothesis.status.upper()}")

    out.append("")
    return out


def about(kb: KnowledgeBase, topic: str) -> str:
    """What do our books say about <topic>?

    Matches a category name, or falls back to a free-text search across claims,
    mechanics and terminology.
    """
    topic_key = topic.strip().lower().replace(" ", "_").replace("-", "_")

    if topic_key in CATEGORIES:
        matches = kb.concepts_in(topic_key)
        heading = f"What our books say about {topic_key.replace('_', ' ')}"
    else:
        needle = topic.strip().lower()
        matches = sorted(
            (
                c for c in kb.concepts.values()
                if needle in c.claim.lower()
                or needle in c.rationale.lower()
                or any(needle in m.lower() for m in c.mechanics)
                or any(needle in cond.lower() for cond in c.conditions)
            ),
            key=lambda c: c.id,
        )
        heading = f"Concepts mentioning {topic!r}"

    if not matches:
        return f"{heading}\n\n  Nothing extracted on this yet."

    lines = [heading, "=" * len(heading), ""]
    for concept in matches:
        lines.extend(_render_concept(kb, concept))

    lines.append(
        "  Note: every entry above is a SOURCE claim. None of it has been "
        "validated on our data."
    )
    return "\n".join(lines)


def conflicts(kb: KnowledgeBase) -> str:
    """What different opinions exist? Read this before adopting anything."""
    pairs = kb.conflicts()
    if not pairs:
        return "No recorded conflicts between concepts."

    lines = ["Where our sources disagree", "=" * 26, ""]
    lines.append(
        _wrap(
            "Disagreement is the useful part of a library. Each pair below is a "
            "question our own data can settle, rather than a choice between "
            "authorities.",
            indent="  ",
        )
    )
    lines.append("")

    for first, second in pairs:
        lines.append(f"  {first.category}:")
        lines.append(f"    A. {first.source.author} [{first.id}]")
        lines.append(_wrap(first.claim, indent="       "))
        lines.append(f"    B. {second.source.author} [{second.id}]")
        lines.append(_wrap(second.claim, indent="       "))

        adjudicators = [
            h for h in kb.hypotheses.values()
            if first.id in h.derived_from and second.id in h.derived_from
        ]
        if adjudicators:
            for hypothesis in adjudicators:
                lines.append(
                    f"    → settled by [{hypothesis.id}], currently {hypothesis.status}"
                )
        else:
            lines.append("    → no hypothesis registered to settle this yet")
        lines.append("")

    return "\n".join(lines)


def who_recommends(kb: KnowledgeBase, needle: str) -> str:
    """Which authors recommend <something>?"""
    needle = needle.strip().lower()
    hits = [
        c for c in kb.concepts.values()
        if needle in c.claim.lower()
        or any(needle in m.lower() for m in c.mechanics)
        or any(needle in cond.lower() for cond in c.conditions)
    ]
    if not hits:
        return f"No source recommends {needle!r} in the extracted material."

    lines = [f"Authors whose material involves {needle!r}", ""]
    for concept in sorted(hits, key=lambda c: c.source.author):
        lines.append(f"  {concept.source.author} -- {concept.source.cite()}")
        lines.append(_wrap(concept.claim, indent="      "))
        lines.append(f"      evidence offered: {concept.evidence_quality}")
        lines.append("")
    return "\n".join(lines)


def untested(kb: KnowledgeBase) -> str:
    """Which hypotheses are open, cheapest first?"""
    open_ones = kb.untested()
    if not open_ones:
        return "No open hypotheses."

    lines = ["Open hypotheses, cheapest to answer first", "=" * 41, ""]
    for hypothesis in open_ones:
        lines.append(f"  [{hypothesis.id}]  {hypothesis.testability}")
        lines.append(_wrap(hypothesis.statement, indent="      "))
        lines.append("      measure:")
        lines.append(_wrap(hypothesis.measurable_as, indent="        "))
        lines.append("      refuted if:")
        lines.append(_wrap(hypothesis.invalidated_by, indent="        "))
        lines.append(f"      needs n = {hypothesis.sample_required:,}")
        if hypothesis.last_attempt:
            attempt = hypothesis.last_attempt
            lines.append(
                f"      tried   : {attempt.get('date', '?')} -- "
                f"{attempt.get('reason', 'no reason recorded')}"
            )
        if hypothesis.derived_from:
            lines.append(f"      from    : {', '.join(hypothesis.derived_from)}")
        lines.append("")

    held = kb.unfalsifiable()
    if held:
        lines.append("  Held as principle, not testable, never queued:")
        for hypothesis in held:
            lines.append(f"    [{hypothesis.id}] {' '.join(hypothesis.statement.split())}")
    return "\n".join(lines)


def validated(kb: KnowledgeBase) -> str:
    """Which ideas have we actually tested?"""
    pairs = kb.validated()
    if not pairs:
        return (
            "Nothing has been validated yet.\n\n"
            "  No hypothesis has been tested against our own data, so no concept\n"
            "  in this knowledge base may influence a trading decision."
        )

    lines = ["Validated knowledge", "=" * 19, ""]
    for hypothesis, validation in pairs:
        lines.append(f"  [{hypothesis.id}] {validation.result.upper()}")
        lines.append(_wrap(hypothesis.statement, indent="      "))
        lines.append(f"      {validation.as_statement()}")
        lines.append(f"      dataset {validation.dataset_sha256[:16]}...  {validation.tested_on}")
        for caveat in validation.caveats:
            lines.append(f"      caveat: {caveat}")
        lines.append("")
    return "\n".join(lines)


def evidence_against(kb: KnowledgeBase, concept_id: str) -> str:
    """What evidence contradicts this idea?"""
    concept = kb.concepts.get(concept_id)
    if concept is None:
        return f"No concept with id {concept_id!r}."

    lines = [f"Evidence bearing on [{concept_id}]", ""]
    lines.append(_wrap(concept.as_statement(), indent="  "))
    lines.append("")

    for other_id in concept.conflicts_with:
        other = kb.concepts.get(other_id)
        if other:
            lines.append(f"  Contradicted by {other.source.author} [{other_id}]:")
            lines.append(_wrap(other.claim, indent="      "))
            lines.append("")

    tested_any = False
    for hypothesis in kb.hypotheses.values():
        if concept_id not in hypothesis.derived_from:
            continue
        if not hypothesis.validation_id:
            lines.append(f"  [{hypothesis.id}] is registered but {hypothesis.status}.")
            continue
        validation = kb.validations.get(hypothesis.validation_id)
        if validation:
            tested_any = True
            lines.append(f"  Our data: {validation.as_statement()}")

    if not tested_any:
        lines.append("")
        lines.append("  We have not tested this. Nothing here is evidence for or against")
        lines.append("  the claim being true -- only a record of who else disagrees.")

    return "\n".join(lines)


def summary(kb: KnowledgeBase) -> str:
    lines = ["Knowledge base", "=" * 14, ""]
    lines.append(f"  books extracted : {len(kb.books)}")
    lines.append(f"  source concepts : {len(kb.concepts)}")
    lines.append(f"  hypotheses      : {len(kb.hypotheses)}")
    lines.append(f"  validated       : {len(kb.validations)}")
    lines.append("")

    by_category: dict[str, int] = {}
    for concept in kb.concepts.values():
        by_category[concept.category] = by_category.get(concept.category, 0) + 1
    if by_category:
        lines.append("  concepts by category:")
        for name, count in sorted(by_category.items(), key=lambda kv: -kv[1]):
            lines.append(f"    {name:24} {count}")
        lines.append("")

    by_status: dict[str, int] = {}
    for hypothesis in kb.hypotheses.values():
        by_status[hypothesis.status] = by_status.get(hypothesis.status, 0) + 1
    if by_status:
        lines.append("  hypotheses by status:")
        for name, count in sorted(by_status.items()):
            lines.append(f"    {name:24} {count}")
        lines.append("")

    stances: dict[str, int] = {}
    for concept in kb.concepts.values():
        stances[concept.stance] = stances.get(concept.stance, 0) + 1
    lines.append("  stance vs FelixScalper:")
    for name, count in sorted(stances.items(), key=lambda kv: -kv[1]):
        lines.append(f"    {name:24} {count}")

    return "\n".join(lines)
