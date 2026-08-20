"""Trading Base Knowledge -- a research memory, not a rulebook.

Three knowledge states, kept structurally separate:

    SOURCE      what an author claims       (no truth value)
    HYPOTHESIS  a claim made measurable     (conditions + refutation)
    VALIDATED   a hypothesis actually tested (n, p-value, dataset hash)

Only the third may ever influence a trading decision.
"""

__all__ = ["schema", "store", "query"]
