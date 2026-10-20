"""priority_breakdown.breakdown_line() — the one shared "P1:1 P3:3"-style count-line
formatter (held_commitments #445's nudge-coalescing spec and #446's reconcile-sweeper
drop-page both call for this exact shape; built once here so neither hand-rolls it)."""

_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def breakdown_line(rows_or_counts) -> str:
    """Format a priority breakdown like 'P1:1 P3:3'.

    Accepts either an iterable of priority strings (one per row) or an
    already-tallied {priority: count} dict. P0..P3 sort first and in order;
    any other priority value sorts after, alphabetically.
    """
    if isinstance(rows_or_counts, dict):
        counts = dict(rows_or_counts)
    else:
        counts = {}
        for p in rows_or_counts:
            counts[p] = counts.get(p, 0) + 1
    ordered = sorted(counts, key=lambda p: (_ORDER.get(p, 99), p))
    return " ".join(f"{p}:{counts[p]}" for p in ordered)
