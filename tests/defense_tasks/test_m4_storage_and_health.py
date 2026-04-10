from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone

import pytest


class _FakeStorage:
    def __init__(self) -> None:
        self.records: dict[str, dict] = {}

    async def get_source_health(self):
        return list(self.records.values())

    async def upsert_source_health(self, site_id: str, payload: dict):
        self.records[site_id] = {"site_id": site_id, **payload}


def test_task_10_storage_contract():
    storage_mod = importlib.import_module("app.defense.storage")
    assert hasattr(storage_mod, "DefenseStorage")
    assert hasattr(storage_mod, "CREATE_TABLES_SQL")
    assert "normalized_events" in storage_mod.CREATE_TABLES_SQL
    assert "run_history" in storage_mod.CREATE_TABLES_SQL
    assert "source_health" in storage_mod.CREATE_TABLES_SQL

    # Verify interface methods exist
    cls = storage_mod.DefenseStorage
    assert callable(getattr(cls, "init_tables", None))
    assert callable(getattr(cls, "insert_normalized_events", None))
    assert callable(getattr(cls, "insert_run", None))
    assert callable(getattr(cls, "upsert_source_health", None))
    assert callable(getattr(cls, "get_source_health", None))


@pytest.mark.asyncio
async def test_task_11_source_health_manager_contract():
    health_mod = importlib.import_module("app.defense.health")

    storage = _FakeStorage()
    manager = health_mod.SourceHealthManager(storage)

    await manager.record_failure("breakingdefense", "timeout")
    await manager.record_failure("breakingdefense", "timeout")
    await manager.record_failure("breakingdefense", "timeout")
    assert storage.records["breakingdefense"]["status"] == "cooling_down"

    for _ in range(7):
        await manager.record_failure("breakingdefense", "timeout")
    assert storage.records["breakingdefense"]["status"] == "pending_disable"

    storage.records["breakingdefense"] = {
        "site_id": "breakingdefense",
        "status": "cooling_down",
        "consecutive_failures": 3,
        "cooldown_until": datetime.now(timezone.utc) - timedelta(seconds=1),
    }
    await manager.refresh_cache()
    assert manager.is_available("breakingdefense") is True


@pytest.mark.asyncio
async def test_milestone_4_checkpoint():
    storage_mod = importlib.import_module("app.defense.storage")
    health_mod = importlib.import_module("app.defense.health")

    assert hasattr(storage_mod, "DefenseStorage")
    assert hasattr(health_mod, "SourceHealthManager")

    # Verify health manager works with fake storage
    storage = _FakeStorage()
    manager = health_mod.SourceHealthManager(storage)
    assert manager.is_available("new_source") is True

    await manager.record_success("new_source")
    assert storage.records["new_source"]["status"] == "ok"
