"""Shared media helpers for the vtx kernel."""


def union_intervals(windows):
    """Merge touching/overlapping intervals without changing units or input."""
    out = []
    for start, end in sorted(windows):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out
