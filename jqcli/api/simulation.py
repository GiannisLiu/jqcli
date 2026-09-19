"""Read-only simulation endpoints used by JoinQuant's live trading page."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from .backtest import _InputParser
from .client import ApiClient
from jqcli.errors import ApiError, NotAuthenticatedError, UsageError

SHANGHAI = timezone(timedelta(hours=8))


def today() -> str:
    return datetime.now(SHANGHAI).date().isoformat()


def date_range(start: str, end: str) -> list[str]:
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError as exc:
        raise UsageError("日期必须为 YYYY-MM-DD") from exc
    if first > last:
        raise UsageError("开始日期不能晚于结束日期")
    return [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]


def _data(client: ApiClient, path: str, **params: Any) -> dict[str, Any]:
    payload = client.get(path, params=params)
    if not isinstance(payload, dict):
        raise ApiError(f"模拟盘接口返回格式异常：{path}")
    if str(payload.get("code")) in {"401", "403"}:
        raise NotAuthenticatedError("模拟盘无访问权限或登录已失效")
    if ("code" in payload and str(payload["code"]) not in {"00000", "0"}) or (
        "status" in payload and str(payload["status"]) != "0"
    ):
        raise ApiError(f"模拟盘接口失败：{payload.get('msg') or path}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ApiError(f"模拟盘接口缺少 data：{path}")
    return data


def list_simulations(client: ApiClient, *, max_pages: int = 100) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    for page in range(1, max_pages + 1):
        data = _data(client, "/algorithm/trade/list", page=page)
        rows = data.get("liveArr")
        if not isinstance(rows, list) or any(not isinstance(r, dict) or not r.get("backtestId") for r in rows):
            raise ApiError("模拟盘列表格式异常")
        try:
            total = int(data["totalCount"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ApiError("模拟盘列表缺少有效总数") from exc
        if not rows and len(items) < total:
            raise ApiError("模拟盘列表分页提前结束")
        page_ids = [str(row["backtestId"]) for row in rows]
        if len(set(page_ids)) != len(page_ids) or seen.intersection(page_ids):
            raise ApiError("模拟盘列表分页出现重复 ID，终止同步")
        seen.update(page_ids)
        items.extend({**row, "id": str(row["backtestId"])} for row in rows)
        if len(items) >= total:
            return {"items": items, "total": total, "complete": True}
    raise ApiError("模拟盘列表超过最大页数，未同步完整")


def get_simulation(client: ApiClient, simulation_id: str) -> dict[str, Any]:
    html = client.get_text("/algorithm/live/index", params={"backtestId": simulation_id})
    parser = _InputParser()
    parser.feed(html)
    if not parser.ids.get("backtestId") or not parser.ids.get("startDate"):
        raise ApiError("无法读取模拟盘详情，请检查 ID、登录状态和访问权限")
    return {
        "id": simulation_id,
        "resolved_id": parser.ids["backtestId"],
        "strategy_id": parser.ids.get("algorithmId", ""),
        "name": parser.ids.get("title-box", ""),
        "start_date": parser.ids["startDate"],
        "status": parser.ids.get("status", ""),
        "frequency": parser.ids.get("frequency", ""),
        "server_time": parser.ids.get("currentTime", ""),
    }


def get_records(client: ApiClient, simulation_id: str, *, kind: str, start: str, end: str,
                limit: int = 10000) -> dict[str, Any]:
    if kind not in {"positions", "orders"}:
        raise UsageError("记录类型必须为 positions 或 orders")
    if limit < 1:
        raise UsageError("limit 必须大于 0")
    endpoint, key = ("position", "position") if kind == "positions" else ("transactionDetail", "transaction")
    days = []
    for day in date_range(start, end):
        params = {"backtestId": simulation_id, "date": day, "limit": limit}
        if kind == "positions":
            params["isForward"] = 0
        data = _data(client, f"/algorithm/live/{endpoint}", **params)
        if not isinstance(data.get(key), list):
            raise ApiError(f"{day} 模拟盘{kind}格式异常")
        # No verified offset pagination exists for these per-day endpoints.
        if "isLimit" not in data or str(data["isLimit"]).lower() not in {"true", "false", "0", "1"}:
            raise ApiError(f"{day} 模拟盘{kind}缺少有效截断标志")
        complete = str(data["isLimit"]).lower() in {"false", "0"}
        record_dates = sorted({str(row.get("date") or row.get("time") or "")[:10]
                               for row in data[key] if isinstance(row, dict)
                               and (row.get("date") or row.get("time"))})
        days.append({"date": day, "record_dates": record_dates,
                     "items": data[key], "complete": complete,
                     "data": data})
    return {"id": simulation_id, "kind": kind, "start": start, "end": end,
            "days": days, "complete": all(d["complete"] for d in days)}


def get_stats(client: ApiClient, simulation_id: str) -> dict[str, Any]:
    data = _data(client, "/algorithm/live/stat", backtestId=simulation_id, offset=-1, limit=1)
    if not isinstance(data.get("stat"), dict):
        raise ApiError("模拟盘收益统计格式异常")
    return {"id": simulation_id, "data": data}


def get_returns(client: ApiClient, simulation_id: str, *, day: str | None = None,
                max_pages: int = 1000) -> dict[str, Any]:
    if day:
        date_range(day, day)
    offset = 0
    points = []
    for _ in range(max_pages):
        params: dict[str, Any] = {"backtestId": simulation_id, "offset": offset}
        if day:
            params["date"] = day
        else:
            params["userRecordOffset"] = 0
        data = _data(client, "/algorithm/backtest/dayResult" if day else "/algorithm/backtest/result", **params)
        result = data.get("result")
        if result is None and str(data.get("state")) == "0":
            result = {"count": 0}
        if not isinstance(result, dict):
            raise ApiError("模拟盘收益序列格式异常")
        try:
            count = int(result["count"])
            returned_offset = int(result.get("offset", offset))
        except (KeyError, ValueError, TypeError) as exc:
            raise ApiError("模拟盘收益分页格式异常") from exc
        if count == 0:
            return {"id": simulation_id, "date": day, "items": points, "complete": True,
                    "state": data.get("state"), "unit": "percent", "next_offset": offset}
        if count < 0 or returned_offset != offset:
            raise ApiError("模拟盘收益分页未前进，终止同步")
        series = result.get("overallReturn", {})
        benchmark = result.get("benchmark", {})
        if any(not isinstance(s, dict) or not isinstance(s.get(k), list) or len(s[k]) != count
               for s in (series, benchmark) for k in ("time", "value")):
            raise ApiError("模拟盘收益数据长度与 count 不一致")
        for i in range(count):
            points.append({"time": series["time"][i], "return": series["value"][i],
                           "benchmark_time": benchmark["time"][i], "benchmark_return": benchmark["value"][i]})
        offset += count
        # The live page requests the complete intraday series once, without pagination.
        if day:
            return {"id": simulation_id, "date": day, "items": points, "complete": True,
                    "state": data.get("state"), "unit": "percent", "next_offset": offset}
    raise ApiError("模拟盘收益超过最大页数，未同步完整")
