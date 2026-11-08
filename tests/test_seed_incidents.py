"""Behaviour of scripts/seed_incidents.py and the shared CSV rules it applies."""

import csv

import pytest
from trackflow_shared.incidents_csv import derive_title, validate_csv_row

from scripts import seed_incidents
from services.incidents import service

HEADER = [
    "incident_id",
    "date",
    "country",
    "customer_type",
    "tracking_number",
    "carrier",
    "category",
    "description",
    "status",
    "customer_email",
    "satisfaction_score",
]

VALID_ROW = {
    "incident_id": "TRF-000001",
    "date": "2024-01-08",
    "country": "ES",
    "customer_type": "B2C",
    "tracking_number": "2YKCCPRHVLZ0",
    "carrier": "MRW",
    "category": "RETURN_REQUEST",
    "description": "Return requested within 14-day window, awaiting approval",
    "status": "OPEN",
    "customer_email": "person@example.com",
    "satisfaction_score": "",
}


@pytest.fixture
def write_csv(tmp_path):
    def _write(rows):
        path = tmp_path / "export.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=HEADER)
            writer.writeheader()
            writer.writerows(rows)
        return path

    return _write


def row(**overrides):
    return {**VALID_ROW, **overrides}


# --- Shared validation rules ------------------------------------------------


def test_a_clean_row_has_no_issues():
    assert validate_csv_row(VALID_ROW) == []


@pytest.mark.parametrize(
    ("overrides", "expected_rule"),
    [
        ({"country": ""}, "invalid_country"),
        ({"country": "FR"}, "invalid_country"),
        ({"carrier": ""}, "invalid_carrier"),
        ({"carrier": "UPS"}, "invalid_carrier"),  # US carrier on an ES record
        ({"tracking_number": "SHORT"}, "invalid_tracking_number"),
        ({"tracking_number": ""}, "invalid_tracking_number"),
        ({"category": "UNKNOWN"}, "invalid_category"),
        ({"category": ""}, "invalid_category"),
        ({"description": "hi"}, "empty_description"),
        ({"customer_email": "not-an-email"}, "invalid_email"),
        ({"customer_email": ""}, "invalid_email"),
        ({"status": "CLOSED", "satisfaction_score": ""}, "closed_without_score"),
        ({"status": "CLOSED", "satisfaction_score": "6"}, "score_out_of_range"),
        ({"status": "CLOSED", "satisfaction_score": "0"}, "score_out_of_range"),
        ({"status": "CLOSED", "satisfaction_score": "three"}, "score_out_of_range"),
        ({"status": "PENDING"}, "invalid_status"),
        ({"date": "08/01/2024"}, "invalid_date"),
        ({"date": ""}, "invalid_date"),
    ],
)
def test_each_rule_fires_on_its_own_case(overrides, expected_rule):
    rules = {issue.rule for issue in validate_csv_row(row(**overrides))}
    assert expected_rule in rules


def test_a_carrier_is_only_judged_against_a_country_that_is_itself_valid():
    """An unknown country must not be reported twice as a carrier problem too."""
    rules = {issue.rule for issue in validate_csv_row(row(country="FR", carrier="UPS"))}
    assert rules == {"invalid_country"}


def test_a_valid_score_on_an_open_record_is_accepted():
    assert validate_csv_row(row(status="OPEN", satisfaction_score="4")) == []


def test_no_issue_message_ever_repeats_a_customer_email():
    """`customer_email` is personal data: it is checked, never echoed."""
    secret = "real.customer@example.com"
    issues = validate_csv_row(row(customer_email=secret, country="FR"))

    assert issues, "the row is invalid, so there is something to inspect"
    assert all(secret not in issue.message for issue in issues)


def test_derive_title_leaves_a_short_description_alone():
    assert derive_title("Parcel delayed for five days") == "Parcel delayed for five days"


def test_derive_title_cuts_a_long_description_on_a_word_boundary():
    title = derive_title("word " * 40, max_length=20)

    assert len(title) <= 20
    assert title.endswith("…")
    assert "wor…" not in title, "the cut must not sever a word"


def test_derive_title_collapses_whitespace():
    assert derive_title("Crushed   box\n and dented  unit") == "Crushed box and dented unit"


# --- Seed script ------------------------------------------------------------


def test_seed_inserts_a_valid_row_with_the_mapped_values(db_path, write_csv):
    report = seed_incidents.seed(write_csv([VALID_ROW]))

    assert report.inserted == 1
    assert report.rejected_count == 0

    (incident,) = service.list_incidents()
    assert incident.origin.value == "customer", "the whole export is customer-reported"
    assert incident.branch.value == "central", "a complaint belongs to no facility"
    assert incident.category.value == "returns_issue", "RETURN_REQUEST maps here"
    assert incident.status.value == "open"
    assert incident.title == VALID_ROW["description"], "short enough to pass through"
    assert incident.description == VALID_ROW["description"]
    assert incident.created_at.date().isoformat() == "2024-01-08", "original date kept"
    assert incident.source_incident_id == "TRF-000001"


@pytest.mark.parametrize(
    ("csv_status", "model_status"),
    [("OPEN", "open"), ("CLOSED", "resolved"), ("DISCARDED", "discarded")],
)
def test_status_map(db_path, write_csv, csv_status, model_status):
    score = "3" if csv_status == "CLOSED" else ""
    seed_incidents.seed(write_csv([row(status=csv_status, satisfaction_score=score)]))

    assert service.list_incidents()[0].status.value == model_status


@pytest.mark.parametrize(
    ("csv_category", "model_category"),
    [
        ("LOST_PARCEL", "lost_parcel"),
        ("DELAYED_DELIVERY", "carrier_issue"),
        ("WRONG_ADDRESS", "delivery_failure"),
        ("RETURN_REQUEST", "returns_issue"),
        ("DAMAGE", "carrier_issue"),
    ],
)
def test_category_map(db_path, write_csv, csv_category, model_category):
    seed_incidents.seed(write_csv([row(category=csv_category)]))

    assert service.list_incidents()[0].category.value == model_category


def test_seed_never_inserts_an_invalid_row_and_reports_it(db_path, write_csv):
    path = write_csv(
        [
            row(incident_id="TRF-000001"),
            row(incident_id="TRF-000002", tracking_number="SHORT"),
            row(incident_id="TRF-000003", category="UNKNOWN"),
        ]
    )
    report = seed_incidents.seed(path)

    assert report.total_rows == 3
    assert report.inserted == 1
    assert report.rejected_count == 2
    assert len(service.list_incidents()) == 1

    assert {rejected.incident_id for rejected in report.rejected} == {
        "TRF-000002",
        "TRF-000003",
    }
    assert report.issues_by_rule["invalid_tracking_number"] == 1
    assert report.issues_by_rule["invalid_category"] == 1


def test_a_row_breaking_two_rules_is_counted_once_under_each(db_path, write_csv):
    report = seed_incidents.seed(write_csv([row(tracking_number="SHORT", customer_email="")]))

    assert report.rejected_count == 1
    assert report.issues_by_rule["invalid_tracking_number"] == 1
    assert report.issues_by_rule["invalid_email"] == 1


def test_rejected_rows_are_identified_by_csv_line_number(db_path, write_csv):
    report = seed_incidents.seed(
        write_csv([VALID_ROW, row(incident_id="TRF-000002", country="FR")])
    )

    assert report.rejected[0].line == 3, "line 1 is the header, so data starts at 2"


def test_seed_is_idempotent(db_path, write_csv):
    path = write_csv([row(incident_id=f"TRF-{index:06d}") for index in range(1, 6)])

    first = seed_incidents.seed(path)
    assert (first.inserted, first.skipped_existing) == (5, 0)

    second = seed_incidents.seed(path)
    assert (second.inserted, second.skipped_existing) == (0, 5)

    assert len(service.list_incidents()) == 5, "a second run duplicates nothing"


def test_a_re_run_still_inserts_rows_that_are_new(db_path, write_csv):
    seed_incidents.seed(write_csv([row(incident_id="TRF-000001")]))

    extended = write_csv([row(incident_id="TRF-000001"), row(incident_id="TRF-000002")])
    report = seed_incidents.seed(extended)

    assert (report.inserted, report.skipped_existing) == (1, 1)
    assert len(service.list_incidents()) == 2


def test_two_rows_sharing_a_description_and_date_are_both_kept(db_path, write_csv):
    """The supplied export has 7 such pairs, so the fallback key is not enough."""
    path = write_csv(
        [
            row(incident_id="TRF-000001"),
            row(incident_id="TRF-000002"),  # identical description and date
        ]
    )
    report = seed_incidents.seed(path)

    assert report.inserted == 2
    assert len(service.list_incidents()) == 2


def test_a_row_without_an_incident_id_falls_back_to_title_and_date(db_path, write_csv):
    path = write_csv([row(incident_id="")])

    assert seed_incidents.seed(path).inserted == 1
    assert seed_incidents.seed(path).skipped_existing == 1, "fallback key still dedupes"


def test_an_incident_created_through_the_api_is_never_treated_as_seeded(db_path, write_csv, client):
    """A manual incident has no source id, so it must not collide with a row."""
    client.post(
        "/api/incidents",
        json={
            "title": VALID_ROW["description"],
            "description": VALID_ROW["description"],
            "category": "returns_issue",
            "origin": "customer",
            "branch": "central",
        },
    )

    report = seed_incidents.seed(write_csv([VALID_ROW]))
    assert report.inserted == 1, "the seed row is still new"
    assert len(service.list_incidents()) == 2


def test_a_file_missing_a_required_column_inserts_nothing(db_path, tmp_path):
    path = tmp_path / "broken.csv"
    path.write_text("incident_id,date\nTRF-000001,2024-01-08\n", encoding="utf-8")

    with pytest.raises(seed_incidents.SeedError, match="missing required column"):
        seed_incidents.seed(path)

    assert service.list_incidents() == []


def test_an_empty_file_is_reported_rather_than_crashing(db_path, tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8")

    with pytest.raises(seed_incidents.SeedError, match="no header row"):
        seed_incidents.seed(path)


def test_a_header_only_file_seeds_nothing_and_does_not_fail(db_path, write_csv):
    report = seed_incidents.seed(write_csv([]))

    assert (report.total_rows, report.inserted, report.rejected_count) == (0, 0, 0)


def test_the_console_report_never_prints_a_customer_email(db_path, write_csv, capsys):
    secret = "real.customer@example.com"
    path = write_csv(
        [
            row(incident_id="TRF-000001", customer_email=secret),
            row(incident_id="TRF-000002", customer_email=secret, country="FR"),
        ]
    )

    seed_incidents.print_report(seed_incidents.seed(path))

    output = capsys.readouterr().out
    assert secret not in output
    assert "real.customer" not in output
    assert "TRF-000002" in output, "the rejected row is still identifiable"


# --- Failures the run has to survive, or report and exit non-zero -------------


def run_main(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["seed-incidents", *map(str, argv)])
    return seed_incidents.main()


def test_a_row_the_model_refuses_is_rejected_and_the_run_continues(db_path, write_csv):
    """Passes every CSV rule, but the description is longer than the model allows."""
    too_long = "Parcel " + "x" * 4000
    path = write_csv(
        [row(incident_id="TRF-000001", description=too_long), row(incident_id="TRF-000002")]
    )

    report = seed_incidents.seed(path)

    assert report.inserted == 1
    assert [rejected.incident_id for rejected in report.rejected] == ["TRF-000001"]
    assert {issue.rule for issue in report.rejected[0].issues} == {seed_incidents.MODEL_RULE}
    assert all(too_long not in issue.message for issue in report.rejected[0].issues)


def test_main_exits_non_zero_when_every_row_is_rejected(db_path, write_csv, monkeypatch, capsys):
    path = write_csv([row(incident_id="TRF-000001", date="08/01/2024")])

    assert run_main(monkeypatch, path) == 1
    assert "every row" in capsys.readouterr().err


def test_main_exits_zero_when_only_some_rows_are_rejected(db_path, write_csv, monkeypatch):
    path = write_csv([row(incident_id="TRF-000001"), row(incident_id="TRF-000002", country="FR")])

    assert run_main(monkeypatch, path) == 0


def test_main_reports_a_missing_file_on_stderr(db_path, tmp_path, monkeypatch, capsys):
    assert run_main(monkeypatch, tmp_path / "nowhere.csv") == 1
    assert "no such file" in capsys.readouterr().err


def test_main_reports_an_unparseable_csv_instead_of_a_traceback(
    db_path, tmp_path, monkeypatch, capsys
):
    """A field past the csv module's size limit raises `csv.Error` mid-file."""
    path = tmp_path / "export.csv"
    path.write_text(",".join(HEADER) + "\nTRF-000001," + "x" * 200_000 + "\n", encoding="utf-8")

    assert run_main(monkeypatch, path) == 1
    assert "could not be parsed" in capsys.readouterr().err


def test_an_unreadable_database_stops_the_run_before_any_insert(db_path, write_csv):
    db_path.write_text("{ this is not json", encoding="utf-8")

    with pytest.raises(seed_incidents.SeedError, match="could not be read"):
        seed_incidents.seed(write_csv([row()]))
