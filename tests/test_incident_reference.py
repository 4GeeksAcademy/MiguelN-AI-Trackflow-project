"""The helpdesk reference rule shared by the incident API and the agent's ticket tool."""

import pytest
from trackflow_shared.incidents import is_reference, normalize_reference


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("482", "TRF-000482"),
        ("12", "TRF-000012"),
        ("000012", "TRF-000012"),
        ("TRF-000012", "TRF-000012"),
        ("TRF-12", "TRF-000012"),
        ("trf-000482", "TRF-000482"),
        ("TRF 482", "TRF-000482"),
        ("TRF482", "TRF-000482"),
        ("  TRF-000100 ", "TRF-000100"),
        ("999999", "TRF-999999"),
    ],
)
def test_short_and_loose_forms_become_the_canonical_reference(raw, canonical):
    assert normalize_reference(raw) == canonical


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "0",
        "TRF-000000",
        "1234567",
        "TRF-1234567",
        "ABC-000012",
        "ticket 12",
        "12a",
        "d1b51aae-447e-4f24-bcbe-c14f3bc8cba0",
    ],
)
def test_anything_else_is_not_a_reference(raw):
    assert normalize_reference(raw) is None


def test_only_the_canonical_form_is_a_reference():
    assert is_reference("TRF-000482")
    assert not is_reference("TRF-482")
    assert not is_reference("trf-000482")
    assert not is_reference("TRF-000482 ")
