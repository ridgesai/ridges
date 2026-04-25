import json
from collections.abc import Mapping
from typing import Any


def parse_jsonb_fields(row: Mapping[str, Any], *field_names: str) -> dict[str, Any]:
    """
    Parse JSONB fields from a database row into Python objects.

    Args:
        row: The database row containing the JSONB fields.
        field_names: The names of the JSONB fields to parse.

    Returns:
        A dictionary with the parsed JSONB fields.
    """

    row_dict = dict(row)
    for field_name in field_names:
        value = row_dict.get(field_name)

        if isinstance(value, str):
            row_dict[field_name] = json.loads(value)

    return row_dict
