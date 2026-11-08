"""`packages/shared/types/telemetry.ts` and the Python mapping state one contract.

The API cannot import TypeScript, so this test reads the file and fails when its
field lists and the ones the API actually uses drift apart.
"""

import re
from pathlib import Path

import pytest

from services.telemetry import service
from services.telemetry.database import telemetry_events
from services.telemetry.models import TelemetryEvent
from tests.test_telemetry_service import row_for

CONTRACT = (
    Path(__file__).resolve().parents[1] / "packages" / "shared" / "types" / "telemetry.ts"
).read_text()


def const_array(name):
    match = re.search(rf"export const {name} = \[([^\]]*)\] as const;", CONTRACT)
    assert match, f"{name} not found in telemetry.ts"
    return tuple(re.findall(r'"([^"]+)"', match.group(1)))


def type_fields(name):
    match = re.search(rf"export type {name} = \{{(.*?)\n\}};", CONTRACT, re.DOTALL)
    assert match, f"type {name} not found in telemetry.ts"
    return tuple(re.findall(r"^\s+(\w+)\??:", match.group(1), re.MULTILINE))


def test_event_type_uses_the_real_telemetry_event_names():
    assert type_fields("TelemetryEvent") == tuple(TelemetryEvent.model_fields)


def test_row_type_lists_the_columns_the_api_writes():
    assert type_fields("TelemetryEventRow") == const_array("TELEMETRY_ROW_COLUMNS")
    assert const_array("TELEMETRY_ROW_COLUMNS") == tuple(row_for())


def test_every_written_column_exists_in_the_table():
    written = set(const_array("TELEMETRY_ROW_COLUMNS"))

    assert written <= set(telemetry_events.columns.keys())
    assert set(telemetry_events.columns.keys()) - written == {"created_at"}


@pytest.mark.parametrize(
    ("ts_name", "python_value"),
    [
        ("TELEMETRY_TAG_ENVELOPE_FIELDS", service.TAG_ENVELOPE_FIELDS),
        ("TELEMETRY_TAG_DIMENSIONS", service.TAG_DIMENSIONS),
    ],
)
def test_tag_keys_match(ts_name, python_value):
    assert const_array(ts_name) == python_value


def test_tag_envelope_fields_are_real_event_fields():
    assert set(service.TAG_ENVELOPE_FIELDS) <= set(TelemetryEvent.model_fields)
