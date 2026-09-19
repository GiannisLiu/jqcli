import httpx
import pytest

from jqcli.api.client import ApiClient
from jqcli.api.simulation import date_range, get_records, get_returns, get_simulation, list_simulations
from jqcli.errors import ApiError, NotAuthenticatedError, UsageError


def client_with(handler):
    return ApiClient("https://example.test", transport=httpx.MockTransport(handler))


def response(data):
    return httpx.Response(200, json={"code": "00000", "status": "0", "data": data})


def test_list_follows_total_and_preserves_fields():
    def handler(request):
        page = request.url.params["page"]
        return response({"liveArr": [{"backtestId": page, "name": "模拟仓", "status": "1"}], "totalCount": "2"})
    with client_with(handler) as client:
        result = list_simulations(client)
    assert [i["id"] for i in result["items"]] == ["1", "2"]
    assert result["items"][0]["status"] == "1"
    assert result["complete"]


def test_list_does_not_silently_accept_missing_page():
    with client_with(lambda r: response({"liveArr": [], "totalCount": "2"})) as client:
        with pytest.raises(ApiError, match="提前结束"):
            list_simulations(client)


def test_detail_resolves_internal_id():
    html = '<input id="backtestId" value="inner"><input id="startDate" value="2026-01-01"><input id="title-box" value="模拟仓">'
    with client_with(lambda r: httpx.Response(200, text=html)) as client:
        assert get_simulation(client, "outer")["resolved_id"] == "inner"


@pytest.mark.parametrize("kind,path,key", [("positions", "position", "position"), ("orders", "transactionDetail", "transaction")])
def test_records_query_each_date_and_preserve_truncation(kind, path, key):
    seen = []
    def handler(request):
        assert request.url.path == f"/algorithm/live/{path}"
        seen.append(request.url.params["date"])
        return response({key: [{"stock": "测试", "orderAmount": "100股"}], "isLimit": len(seen) == 2})
    with client_with(handler) as client:
        result = get_records(client, "s", kind=kind, start="2026-09-17", end="2026-09-18")
    assert seen == ["2026-09-17", "2026-09-18"]
    assert not result["complete"]
    assert result["days"][0]["items"][0]["orderAmount"] == "100股"


def test_historical_returns_fetch_until_empty():
    offsets = []
    def handler(request):
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        values = [offset + 1] if offset < 2 else []
        return response({"state": "1", "result": {"count": len(values), "offset": offset,
                         "overallReturn": {"time": values, "value": values},
                         "benchmark": {"time": values, "value": values}}})
    with client_with(handler) as client:
        result = get_returns(client, "s")
    assert offsets == [0, 1, 2]
    assert [p["return"] for p in result["items"]] == [1, 2]
    assert result["unit"] == "percent"


def test_repeated_result_offset_is_error():
    series = {"time": [1], "value": [1]}
    with client_with(lambda r: response({"result": {"count": 1, "offset": 0, "overallReturn": series, "benchmark": series}})) as client:
        with pytest.raises(ApiError, match="未前进"):
            get_returns(client, "s")


def test_empty_today_is_not_previous_trading_day():
    def handler(request):
        assert request.url.path == "/algorithm/backtest/dayResult"
        assert request.url.params["date"] == "2026-09-19"
        return response({"state": "1", "result": {"count": 0, "offset": 0}})
    with client_with(handler) as client:
        result = get_returns(client, "s", day="2026-09-19")
    assert result["items"] == []
    assert result["date"] == "2026-09-19"


@pytest.mark.parametrize("payload,error", [({"code": 403}, NotAuthenticatedError), ({"code": "50000", "msg": "失败"}, ApiError), ({"data": []}, ApiError)])
def test_business_errors(payload, error):
    with client_with(lambda r: httpx.Response(200, json=payload)) as client:
        with pytest.raises(error):
            list_simulations(client)


def test_invalid_range():
    with pytest.raises(UsageError):
        date_range("2026-09-20", "2026-09-19")


def test_position_reports_actual_record_date():
    with client_with(lambda r: response({"position": [{"time": "2026-09-18 16:00:00"}], "isLimit": False})) as client:
        result = get_records(client, "s", kind="positions", start="2026-09-19", end="2026-09-19")
    assert result["days"][0]["date"] == "2026-09-19"
    assert result["days"][0]["record_dates"] == ["2026-09-18"]


def test_missing_truncation_flag_is_not_complete():
    with client_with(lambda r: response({"position": []})) as client:
        with pytest.raises(ApiError, match="截断标志"):
            get_records(client, "s", kind="positions", start="2026-09-19", end="2026-09-19")


def test_list_repeated_page_rejected():
    with client_with(lambda r: response({"liveArr": [{"backtestId": "s"}], "totalCount": 2})) as client:
        with pytest.raises(ApiError, match="重复 ID"):
            list_simulations(client)


def test_intraday_nonempty_series_is_single_request():
    requests = []
    def handler(request):
        requests.append(request)
        return response({"state": "1", "result": {"count": 1, "offset": 0,
            "overallReturn": {"time": [123], "value": [-1.2]},
            "benchmark": {"time": [123], "value": [0.5]}}})
    with client_with(handler) as client:
        result = get_returns(client, "s", day="2026-09-18")
    assert len(requests) == 1
    assert result["items"][0]["return"] == -1.2
