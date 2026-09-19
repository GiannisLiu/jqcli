from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from jqcli.api import simulation as api
from jqcli.commands.backtest import close_client, make_client
from jqcli.errors import ApiError, FileError
from jqcli.output import write_json

if TYPE_CHECKING:
    from jqcli.cli import AppContext


@click.group(name="simulation")
def simulation_group() -> None:
    """模拟盘列表、持仓、下单记录及收益同步（只读）。"""


def emit(app: AppContext, payload: dict[str, Any]) -> None:
    if app.json_output or not app.quiet:
        write_json(payload)


@simulation_group.command("ls")
@click.pass_obj
def ls(app: AppContext) -> None:
    """读取全部模拟盘列表，自动分页。"""
    client = make_client(app)
    try:
        payload = api.list_simulations(client)
    finally:
        close_client(client)
    emit(app, payload)


@simulation_group.command("show")
@click.argument("simulation_id")
@click.pass_obj
def show(app: AppContext, simulation_id: str) -> None:
    """读取模拟盘基本信息。"""
    client = make_client(app)
    try:
        payload = api.get_simulation(client, simulation_id)
    finally:
        close_client(client)
    emit(app, payload)


def _record_command(kind: str) -> click.Command:
    @click.command(name=kind, help="读取持仓记录（含历史）。" if kind == "positions" else "读取模拟仓下单记录（含历史）。")
    @click.argument("simulation_id")
    @click.option("--start", type=click.DateTime(formats=["%Y-%m-%d"]), help="起始日期，默认当日")
    @click.option("--end", type=click.DateTime(formats=["%Y-%m-%d"]), help="结束日期，默认与起始日期相同")
    @click.option("--limit", type=click.IntRange(min=1), default=10000, show_default=True)
    @click.pass_obj
    def records(app: AppContext, simulation_id: str, start: datetime | None, end: datetime | None, limit: int) -> None:
        first = start.date().isoformat() if start else api.today()
        last = end.date().isoformat() if end else first
        api.date_range(first, last)
        client = make_client(app)
        try:
            detail = api.get_simulation(client, simulation_id)
            payload = api.get_records(client, detail["resolved_id"], kind=kind, start=first, end=last, limit=limit)
            payload["id"] = simulation_id
        finally:
            close_client(client)
        emit(app, payload)
    return records


simulation_group.add_command(_record_command("positions"))
simulation_group.add_command(_record_command("orders"))


@simulation_group.command("returns")
@click.argument("simulation_id")
@click.option("--date", "day", type=click.DateTime(formats=["%Y-%m-%d"]), help="查询指定日期的日内收益")
@click.option("--today", "current_day", is_flag=True, help="查询北京时间当日收益")
@click.pass_obj
def returns(app: AppContext, simulation_id: str, day: datetime | None, current_day: bool) -> None:
    """默认读取全部历史累计收益；可查询当日/指定日的日内收益。"""
    if day and current_day:
        raise click.UsageError("--date 与 --today 不能同时使用")
    selected = api.today() if current_day else day.date().isoformat() if day else None
    client = make_client(app)
    try:
        detail = api.get_simulation(client, simulation_id)
        payload = api.get_returns(client, detail["resolved_id"], day=selected)
        payload["id"] = simulation_id
    finally:
        close_client(client)
    emit(app, payload)


def write_snapshot(path: Path, payload: dict[str, Any]) -> None:
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    except OSError as exc:
        raise FileError(f"无法写入模拟盘快照：{path}") from exc
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


@simulation_group.command("sync")
@click.argument("simulation_id", required=False)
@click.option("--start", type=click.DateTime(formats=["%Y-%m-%d"]), help="持仓、下单历史起始日期，默认模拟盘开始日")
@click.option("--end", type=click.DateTime(formats=["%Y-%m-%d"]), help="持仓、下单历史结束日期，默认当日")
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path),
              default="local/data/simulations/snapshot.json", show_default=True)
@click.pass_obj
def sync(app: AppContext, simulation_id: str | None, start: datetime | None,
         end: datetime | None, output: Path) -> None:
    """同步完整 JSON 快照；省略 ID 同步全部模拟盘，成功后原子替换输出文件。"""
    current = api.today()
    last = end.date().isoformat() if end else current
    if start:
        api.date_range(start.date().isoformat(), last)
    client = make_client(app)
    try:
        listing = api.list_simulations(client)
        ids = [simulation_id] if simulation_id else [item["id"] for item in listing["items"]]
        simulations = []
        for identifier in ids:
            detail = api.get_simulation(client, identifier)
            resolved = detail["resolved_id"]
            first = start.date().isoformat() if start else detail["start_date"]
            if not app.quiet:
                click.echo(f"同步 {detail['name'] or identifier}：{first} 至 {last}", err=True)
            positions = api.get_records(client, resolved, kind="positions", start=first, end=last)
            orders = api.get_records(client, resolved, kind="orders", start=first, end=last)
            if not positions["complete"] or not orders["complete"]:
                raise ApiError("持仓或下单记录被服务端截断，未覆盖已有快照；请使用 positions/orders 检查对应日期")
            simulations.append({
                "detail": detail, "positions": positions, "orders": orders,
                "historical_returns": api.get_returns(client, resolved),
                "today_returns": api.get_returns(client, resolved, day=current),
                "latest_stats": api.get_stats(client, resolved),
            })
        payload = {"schema_version": 1, "synced_at": datetime.now(api.SHANGHAI).isoformat(),
                   "today": current, "complete": True, "list": listing, "simulations": simulations}
        write_snapshot(output, payload)
    finally:
        close_client(client)
    emit(app, {"ok": True, "path": str(output.resolve()), "count": len(simulations), "complete": True})
