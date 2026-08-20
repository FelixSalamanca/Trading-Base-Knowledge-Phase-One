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


def cmd_test(kb, args) -> int:
    """Run one or every runnable hypothesis and record what came back."""
    from kb import validation

    if args.all:
        wanted = [h for h in validation.RUNNERS if h in kb.hypotheses]
    elif args.hypothesis_id:
        wanted = [args.hypothesis_id]
    else:
        print("Give a hypothesis id, or --all.", file=sys.stderr)
        return 2

    try:
        frame = validation.journal()
    except validation.FelixUnavailable as exc:
        print(f"Cannot test: {exc}", file=sys.stderr)
        return 1

    dataset = validation.fingerprint(frame)
    print(f"dataset {dataset[:16]}...  {len(frame)} rows\n")

    exit_code = 0
    for hypothesis_id in wanted:
        if hypothesis_id not in kb.hypotheses:
            print(f"Unknown hypothesis {hypothesis_id!r}", file=sys.stderr)
            exit_code = 1
            continue
        runner = validation.RUNNERS.get(hypothesis_id)
        if runner is None:
            print(f"No runner implemented for {hypothesis_id!r}", file=sys.stderr)
            exit_code = 1
            continue

        outcome = runner(kb)
        print(f"[{hypothesis_id}]")
        print(f"  {kb.hypotheses[hypothesis_id].statement.strip()}")
        print(f"  RESULT   : {outcome.result.upper()}")
        print(f"  n        : {outcome.n:,}")
        print(f"  p-value  : {outcome.p_value:.4f}")
        print(f"  effect   : {outcome.effect_size:+.4f}")
        print(f"  method   : {' '.join(outcome.method.split())}")
        for name, value in outcome.metrics.items():
            print(f"    {name:34} {value}")
        for caveat in outcome.caveats:
            print(f"  caveat: {' '.join(caveat.split())}")

        if args.dry_run:
            print("  (dry run -- nothing written)\n")
            continue

        validation_id = f"v-{hypothesis_id}"
        path = validation.write_validation(hypothesis_id, outcome, dataset, validation_id)
        validation.update_hypothesis_status(hypothesis_id, outcome, validation_id)
        print(f"  written  : {path.relative_to(path.parent.parent.parent)}\n")

    return exit_code


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

    p_test = sub.add_parser("test", help="run a hypothesis against the FelixScalper journal")
    p_test.add_argument("hypothesis_id", nargs="?", help="omit with --all")
    p_test.add_argument("--all", action="store_true", help="run every runnable hypothesis")
    p_test.add_argument(
        "--dry-run",
        action="store_true",
        help="compute and print, but write no validation and change no status",
    )

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

    if args.command == "test":
        return cmd_test(kb, args)

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
