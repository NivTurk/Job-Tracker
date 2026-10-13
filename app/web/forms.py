"""Small helpers for reading HTML form values."""

from datetime import date


class FormValueError(ValueError):
    pass


def parse_date(value: str | None, field_label: str) -> date | None:
    """Parse an <input type=date> value (YYYY-MM-DD). Empty means None."""
    if value is None or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise FormValueError(f"{field_label}: not a valid date.") from exc


def parse_score(value: str | None) -> int | None:
    """Parse a 0-100 score filter. Empty or invalid means no filter."""
    if value is None or not value.strip():
        return None
    try:
        number = int(value)
    except ValueError:
        return None
    return max(0, min(100, number))


def lines_to_list(value: str) -> list[str]:
    """Textarea with one item per line -> list, blank lines and duplicates removed."""
    items: list[str] = []
    for line in value.splitlines():
        item = line.strip()
        if item and item not in items:
            items.append(item)
    return items
