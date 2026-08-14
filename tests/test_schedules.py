"""
Schedule input validation and store behaviour.

A malformed cron or an unknown time zone used to reach croniter/ZoneInfo inside
the store and surface as a 500 — an ordinary bad request reported as a server
fault.
"""

import pytest
from starlette.testclient import TestClient

from src.utils.config import Config
from web import settings
from web.server import create_app
from web.services import schedules


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    monkeypatch.setattr(Config, "DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "DATA_DIR", str(tmp_path))
    return schedules


# --------------------------------------------------------------------------- #
# Input validation
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "cron",
    [
        "not a cron here",
        "99 99 99 99 99",
        "* * * *",           # too few fields
    ],
)
def test_invalid_cron_is_a_422_not_a_500(client, cron) -> None:
    response = client.post(
        "/api/schedules", json={"target_domain": "example.com", "cron": cron}
    )

    assert response.status_code == 422


def test_unknown_timezone_is_a_422_not_a_500(client) -> None:
    response = client.post(
        "/api/schedules",
        json={"target_domain": "example.com", "cron": "0 9 * * 1", "timezone": "Mars/Olympus"},
    )

    assert response.status_code == 422


def test_valid_schedule_is_accepted(client) -> None:
    response = client.post(
        "/api/schedules",
        json={
            "target_domain": "example.com",
            "cron": "0 9 * * 1",
            "timezone": "Asia/Kolkata",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["next_run_at"]
    assert body["target_domain"] == "example.com"


def test_patch_rejects_invalid_cron(client) -> None:
    created = client.post("/api/schedules", json={"target_domain": "example.com"}).json()

    response = client.patch(f"/api/schedules/{created['id']}", json={"cron": "nonsense"})

    assert response.status_code == 422


def test_patch_on_unknown_schedule_is_404(client) -> None:
    assert client.patch("/api/schedules/nope", json={"cron": "0 9 * * 1"}).status_code == 404


def test_delete_on_unknown_schedule_is_404(client) -> None:
    assert client.delete("/api/schedules/nope").status_code == 404


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #

def _payload(**overrides) -> dict:
    base = {
        "target_domain": "example.com",
        "search_name": None,
        "scan_config": {},
        "cron": "0 9 * * 1",
        "timezone": "UTC",
        "enabled": True,
        "notification_url": None,
    }
    base.update(overrides)
    return base


def test_create_then_list_round_trips(store) -> None:
    created = store.create(_payload())

    rows = store.list_schedules()

    assert [r["id"] for r in rows] == [created["id"]]


def test_disabled_schedule_has_no_next_run(store) -> None:
    created = store.create(_payload(enabled=False))

    assert created["next_run_at"] is None
    assert created not in store.due()


def test_update_of_unrelated_field_keeps_schedule_enabled(store) -> None:
    """
    Regression: the enabled check defaulted to False, so editing only the name
    silently stopped a schedule from ever firing again.
    """
    created = store.create(_payload())

    updated = store.update(created["id"], {"search_name": "Renamed"})

    assert updated["search_name"] == "Renamed"
    assert updated["next_run_at"] is not None


def test_delete_removes_the_row(store) -> None:
    created = store.create(_payload())

    assert store.delete(created["id"]) is True
    assert store.delete(created["id"]) is False
    assert store.list_schedules() == []


def test_mark_started_records_the_run_and_advances_next(store) -> None:
    created = store.create(_payload())

    store.mark_started(created["id"], "scan-123")

    row = store.list_schedules()[0]
    assert row["last_scan_id"] == "scan-123"
    assert row["last_run_at"] is not None
    assert row["next_run_at"] > created["created_at"]


def test_corrupt_store_file_reads_as_empty(store, tmp_path) -> None:
    (tmp_path / "schedules.json").write_text("{ not json", encoding="utf-8")

    assert store.list_schedules() == []
