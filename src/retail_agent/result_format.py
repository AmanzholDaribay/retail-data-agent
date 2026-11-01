from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from retail_agent.db.base import QueryResult


def format_result(result: QueryResult) -> str:
    """Turn a query result into the text the model reads: a compact table, plus a note when rows were cut off.

    An empty result is said in words, so the model can't mistake the header for data.
    """
    if not result.rows:
        text = f"The query returned no rows. Columns: {', '.join(result.columns)}"
        if result.small_groups_hidden_below is not None:
            text += (
                f"\nNote: groups with fewer than {result.small_groups_hidden_below} customers are hidden for privacy,"
                " so there may be data that can't be shown at this level of detail."
            )
        return text
    lines = [" | ".join(result.columns)]
    lines += [" | ".join(_format_value(value) for value in row) for row in result.rows]
    if result.truncated:
        lines.append(
            f"Note: only the first {len(result.rows)} rows are shown. The query returned {result.total_rows} rows."
        )
    if result.small_groups_hidden_below is not None:
        lines.append(
            f"Note: groups with fewer than {result.small_groups_hidden_below} customers are hidden for privacy,"
            " so these rows may not add up to the full total."
        )
    return "\n".join(lines)


def _format_value(value: Any) -> str:
    """Plain, fixed text for each value, so every provider reads dates, money and missing values the same way."""
    if value is None:
        return "NULL"
    if isinstance(value, datetime):
        if value.tzinfo is None:  # DATETIME: no time zone
            return value.isoformat(sep=" ")
        return value.astimezone(timezone.utc).replace(tzinfo=None).isoformat(sep=" ") + " UTC"  # TIMESTAMP
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")  # never scientific notation such as 1E+3
    return str(value)
