import pytest
from tinydb import TinyDB

from scripts.seed_performance_data import seed_performance_data
from services.core.db import DEFAULT_DB_PATH


def test_performance_seed_creates_varied_valid_rows_in_scratch_db(tmp_path):
    path = tmp_path / "scale" / "performance.json"

    assert seed_performance_data(path, incident_count=80, supplier_count=40) == (80, 40)

    with TinyDB(path) as database:
        incidents = database.table("incidents").all()
        suppliers = database.table("suppliers").all()

    assert len(incidents) == 80
    assert len({incident["category"] for incident in incidents}) > 1
    assert len({incident["status"] for incident in incidents}) > 1
    assert len({incident["created_at"] for incident in incidents}) == 80
    assert len({supplier["name"] for supplier in suppliers}) == 40
    assert len({supplier["rate_per_shipment"] for supplier in suppliers}) > 1
    assert {supplier["currency"] for supplier in suppliers} == {"USD", "EUR"}

    for incident in incidents:
        assert incident["id"]
        assert incident["title"] and incident["description"]
        assert incident["source_incident_id"]

    for supplier in suppliers:
        assert supplier["categories"]
        assert supplier["country"] == (
            "USA" if supplier["currency"] == "USD" else "Spain"
        )


def test_performance_seed_refuses_default_database(tmp_path):
    with pytest.raises(ValueError, match="default database"):
        seed_performance_data(DEFAULT_DB_PATH, incident_count=1, supplier_count=1)


def test_performance_seed_refuses_existing_file_without_modifying_it(tmp_path):
    path = tmp_path / "existing.json"
    original = '{"keep": {"1": {"value": "untouched"}}}'
    path.write_text(original)

    with pytest.raises(ValueError, match="overwrite existing"):
        seed_performance_data(path, incident_count=1, supplier_count=1)

    assert path.read_text() == original


def test_performance_seed_rejects_negative_counts_before_creating_file(tmp_path):
    path = tmp_path / "negative.json"

    with pytest.raises(ValueError, match="zero or greater"):
        seed_performance_data(path, incident_count=-1)

    assert not path.exists()


def test_performance_seed_ids_are_repeatable_across_fresh_scratch_files(tmp_path):
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    seed_performance_data(first_path, incident_count=4, supplier_count=4)
    seed_performance_data(second_path, incident_count=4, supplier_count=4)

    with TinyDB(first_path) as first_db, TinyDB(second_path) as second_db:
        first_incident_ids = [row["id"] for row in first_db.table("incidents").all()]
        second_incident_ids = [row["id"] for row in second_db.table("incidents").all()]
        first_supplier_names = [row["name"] for row in first_db.table("suppliers").all()]
        second_supplier_names = [row["name"] for row in second_db.table("suppliers").all()]

    assert first_incident_ids == second_incident_ids
    assert first_supplier_names == second_supplier_names
