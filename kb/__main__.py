"""Command line interface: ``python -m kb <command>``."""

from __future__ import annotations

import argparse
import sys

from kb import query
from kb.schema import SchemaError
from kb.store import load


def cmd_validate() -> int:
    """Structural integrity. Exits non-zero so CI can gate on it."""
    try:
        kb = load()
    except SchemaError as exc:
        print(f"SCHEMA ERROR: {exc}", file=sys.stderr)
        return 1

    problems = kb.check_references() + kb.unbacked_claims()
    if problems:
        print(f"{len(problems)} problem(s):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(
        f"OK  {len(kb.books)} book(s), {len(kb.concepts)} concept(s), "
        f"{len(kb.hypotheses)} hypothesis/es, {len(kb.validations)} validation(s). "
        "All references resolve; no unbacked claims."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kb",
        description=(
            "Query the trading knowledge base. Reports what authors claim, "
            "what we have turned into hypotheses, and what we have actually "
            "tested -- never blurring the three."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="check schema and cross-references")
    sub.add_parser("summary", help="counts by category, status and stance")
    sub.add_parser("conflicts", help="where our sources disagree")
    sub.add_parser("untested", help="open hypotheses, cheapest to answer first")
    sub.add_parser("validated", help="what we have actually tested")

    p_about = sub.add_parser("about", help="what our books say about a topic")
    p_about.add_argument("topic")

    p_who = sub.add_parser("who", help="which authors recommend something")
    p_who.add_argument("thing")

    p_against = sub.add_parser("against", help="what contradicts a concept")
    p_against.add_argument("concept_id")

    args = parser.parse_args(argv)

    # Windows consoles default to cp1252, which cannot encode the arrows and
    # warning marks used to show derivation and conflict. Without this the CLI
    # crashes on exactly the output that matters most.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if args.command == "validate":
        return cmd_validate()

    try:
        kb = load()
    except SchemaError as exc:
        print(f"SCHEMA ERROR: {exc}", file=sys.stderr)
        return 1

    handlers = {
        "summary": lambda: query.summary(kb),
        "conflicts": lambda: query.conflicts(kb),
        "untested": lambda: query.untested(kb),
        "validated": lambda: query.validated(kb),
        "about": lambda: query.about(kb, args.topic),
        "who": lambda: query.who_recommends(kb, args.thing),
        "against": lambda: query.evidence_against(kb, args.concept_id),
    }
    print(handlers[args.command]())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
