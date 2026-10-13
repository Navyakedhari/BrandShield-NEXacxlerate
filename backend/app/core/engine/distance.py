"""Edit distance helpers (public algorithms, implemented from scratch)."""

from __future__ import annotations


def osa_distance(a: str, b: str) -> int:
    """Damerau-Levenshtein (optimal string alignment): insert, delete, substitute, adjacent swap."""
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev2: list[int] = []
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                v = min(v, prev2[j - 2] + 1)
            cur[j] = v
        prev2, prev = prev, cur
    return prev[lb]


def osa_ops(a: str, b: str) -> list[str]:
    """One minimal alignment as a list of ops: 'sub' | 'ins' | 'del' | 'trans'.

    'ins' means `a` has an extra character relative to `b`; 'del' means `a` is missing one.
    """
    la, lb = len(a), len(b)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            v = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                v = min(v, d[i - 2][j - 2] + 1)
            d[i][j] = v
    ops: list[str] = []
    i, j = la, lb
    while i > 0 or j > 0:
        if i > 0 and j > 0 and a[i - 1] == b[j - 1] and d[i][j] == d[i - 1][j - 1]:
            i, j = i - 1, j - 1
        elif i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1] and d[i][j] == d[i - 2][j - 2] + 1:
            ops.append("trans")
            i, j = i - 2, j - 2
        elif i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + 1:
            ops.append("sub")
            i, j = i - 1, j - 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            ops.append("ins")
            i -= 1
        else:
            ops.append("del")
            j -= 1
    ops.reverse()
    return ops


def one_edit(a: str, b: str) -> str | None:
    """Return the single op ('sub'|'ins'|'del'|'trans') if osa(a, b) == 1, else None. Linear time."""
    la, lb = len(a), len(b)
    if abs(la - lb) > 1 or a == b:
        return None
    i = 0
    while i < min(la, lb) and a[i] == b[i]:
        i += 1
    if la == lb:
        if a[i + 1 :] == b[i + 1 :]:
            return "sub"
        if i + 1 < la and a[i] == b[i + 1] and a[i + 1] == b[i] and a[i + 2 :] == b[i + 2 :]:
            return "trans"
        return None
    if la == lb + 1:
        return "ins" if a[i + 1 :] == b[i:] else None
    return "del" if b[i + 1 :] == a[i:] else None
