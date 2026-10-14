"""Words the loop says to people (D1094)."""


def plural(n: int | float, one: str, many: str | None = None) -> str:
    """'1 design', '3 designs' -- never 'design(s)'; `many` where an 's' is not the plural."""
    return f"{n} {one if n == 1 else (many if many is not None else one + 's')}"
