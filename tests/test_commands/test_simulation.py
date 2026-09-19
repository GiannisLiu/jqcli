import json

import pytest
from click.testing import CliRunner

from jqcli.cli import main
from jqcli.errors import ApiError
from jqcli.commands import simulation


def invoke(tmp_path, args):
    return CliRunner().invoke(main, ["--config", str(tmp_path / "config.json"), "--format", "json", *args])


@pytest.fixture
def fake_api(monkeypatch):
    closed = []
    monkeypatch.setattr(simulation, "make_client", lambda app: object())
    monkeypatch.setattr(simulation, "close_client", lambda client: closed.append(True))
    monkeypatch.setattr(simulation.api, "today", lambda: "2026-09-19")
    monkeypatch.setattr(simulation.api, "list_simulations", lambda client: {"items": [{"id": "s"}], "complete": True})
    monkeypatch.setattr(simulation.api, "get_simulation", lambda c, i: {"id": i, "name": "测试", "resolved_id": "inner", "start_date": "2026-09-18"})
    monkeypatch.setattr(simulation.api, "get_records", lambda c, i, **kw: {"complete": True, **kw})
    monkeypatch.setattr(simulation.api, "get_returns", lambda c, i, **kw: {"complete": True, **kw})
    monkeypatch.setattr(simulation.api, "get_stats", lambda c, i: {})
    return closed


def test_sync_writes_all_sections_and_closes(fake_api, tmp_path):
    path = tmp_path / "snapshot.json"
    result = invoke(tmp_path, ["simulation", "sync", "--output", str(path)])
    assert result.exit_code == 0, result.output
    payload = json.loads(path.read_text(encoding="utf-8"))
    item = payload["simulations"][0]
    assert item["orders"]["start"] == "2026-09-18"
    assert item["positions"]["end"] == "2026-09-19"
    assert item["today_returns"]["day"] == "2026-09-19"
    assert "historical_returns" in item and "latest_stats" in item
    assert fake_api == [True]


@pytest.mark.parametrize("truncated", [True, False])
def test_failed_sync_preserves_existing_snapshot(fake_api, monkeypatch, tmp_path, truncated):
    path = tmp_path / "snapshot.json"
    path.write_text("old snapshot", encoding="utf-8")
    def records(*a, **kw):
        if truncated:
            return {"complete": False}
        raise ApiError("接口失败")
    monkeypatch.setattr(simulation.api, "get_records", records)
    result = invoke(tmp_path, ["simulation", "sync", "s", "--output", str(path)])
    assert result.exit_code == 4
    assert path.read_text(encoding="utf-8") == "old snapshot"
    assert fake_api == [True]


def test_orders_pass_range_and_resolved_id(fake_api, monkeypatch, tmp_path):
    captured = {}
    def records(c, i, **kw):
        captured.update(id=i, **kw)
        return {"complete": True}
    monkeypatch.setattr(simulation.api, "get_records", records)
    result = invoke(tmp_path, ["simulation", "orders", "s", "--start", "2026-09-17", "--end", "2026-09-18"])
    assert result.exit_code == 0
    assert captured == {"id": "inner", "kind": "orders", "start": "2026-09-17", "end": "2026-09-18", "limit": 10000}


def test_returns_conflicting_flags(tmp_path):
    result = invoke(tmp_path, ["simulation", "returns", "s", "--today", "--date", "2026-09-18"])
    assert result.exit_code == 2


def test_atomic_write_failure_keeps_original(monkeypatch, tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text("old", encoding="utf-8")
    def fail(*args):
        raise OSError("locked")
    monkeypatch.setattr(simulation.os, "replace", fail)
    from jqcli.errors import FileError
    with pytest.raises(FileError):
        simulation.write_snapshot(path, {"new": True})
    assert path.read_text() == "old"
    assert list(tmp_path.iterdir()) == [path]
