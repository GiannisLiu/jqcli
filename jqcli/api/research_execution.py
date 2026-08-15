from __future__ import annotations

import builtins
import copy
import json
import math
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from time import monotonic
from typing import Any, Callable, Iterator
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from websockets.exceptions import ConnectionClosed, InvalidHandshake
from websockets.sync.client import connect as websocket_connect

from jqcli.errors import ApiError, JqcliError, NetworkError, TimeoutError, UsageError

from .client import ApiClient
from .research import (
    _bootstrap_research,
    _decode_xsrf_cookie,
    _get_contents,
    _normalize_item,
    normalize_research_path,
)


_MAX_WEBSOCKET_MESSAGE_SIZE = 16 * 1024 * 1024
_MAX_EXECUTION_MESSAGE_COUNT = 10_000
_MAX_EXECUTION_TOTAL_BYTES = 64 * 1024 * 1024
_WebSocketConnector = Callable[..., Any]
_EventHandler = Callable[[dict[str, Any]], None]


def list_research_kernelspecs(client: ApiClient) -> dict[str, Any]:
    """List sanitized kernel specifications exposed by the research server."""
    base_path = _bootstrap_research(client)
    payload = client.get(f"{base_path}api/kernelspecs")
    return _normalize_kernelspecs(payload)


def list_research_kernels(client: ApiClient) -> dict[str, Any]:
    """List currently running research kernels without connecting to them."""
    base_path = _bootstrap_research(client)
    return _kernel_collection(client.get(f"{base_path}api/kernels"))


def list_research_sessions(client: ApiClient) -> dict[str, Any]:
    """List current research sessions without attaching to their kernels."""
    base_path = _bootstrap_research(client)
    return _session_collection(client.get(f"{base_path}api/sessions"))


def execute_research_code(
    client: ApiClient,
    code: str,
    *,
    kernel_name: str | None = None,
    execution_timeout: float = 120,
    on_event: _EventHandler | None = None,
    _websocket_connect: _WebSocketConnector | None = None,
) -> dict[str, Any]:
    """Execute code in a newly-created, isolated session/kernel and clean it up.

    The returned object intentionally omits kernel, session, message, and SSO
    identifiers.  ``on_event`` receives only normalized output/status events.
    """
    if not isinstance(code, str):
        raise UsageError("研究平台执行代码必须是字符串")
    if not code.strip():
        raise UsageError("研究平台执行代码不能为空")
    normalized_kernel_name = _normalize_kernel_name(kernel_name)
    normalized_timeout = _normalize_timeout(execution_timeout)

    with _temporary_runtime(
        client,
        kernel_name=normalized_kernel_name,
        session_path=_synthetic_session_path(prefix="exec"),
    ) as runtime:
        deadline = monotonic() + normalized_timeout
        socket = _open_kernel_socket(
            client,
            runtime["base_path"],
            runtime["kernel_id"],
            runtime["client_session_id"],
            deadline,
            connector=_websocket_connect,
        )
        try:
            return _execute_on_socket(
                socket,
                code,
                runtime["client_session_id"],
                deadline,
                on_event=on_event,
            )
        finally:
            _close_socket(socket)


def run_research_notebook(
    client: ApiClient,
    path: str,
    *,
    cell_indexes: list[int] | None = None,
    kernel_name: str | None = None,
    execution_timeout: float = 300,
    on_event: _EventHandler | None = None,
    _websocket_connect: _WebSocketConnector | None = None,
) -> dict[str, Any]:
    """Run all code cells in one new temporary session.

    The notebook is read explicitly and never written back by this API.
    """
    normalized_path = normalize_research_path(path)
    if not normalized_path:
        raise UsageError("运行 Notebook 时必须指定研究平台文件路径")
    normalized_kernel_name = _normalize_kernel_name(kernel_name)
    normalized_timeout = _normalize_timeout(execution_timeout)

    base_path = _bootstrap_research(client)
    raw_item = _get_contents(client, base_path, normalized_path, include_content=True)
    item = _normalize_item(raw_item, include_content=True)
    notebook = _validated_notebook_copy(item)
    if normalized_kernel_name is None:
        normalized_kernel_name = _notebook_kernel_name(notebook)
    all_code_cells = [
        (index, cell)
        for index, cell in enumerate(notebook["cells"])
        if cell.get("cell_type") == "code"
    ]
    code_cells = _select_code_cells(notebook["cells"], all_code_cells, cell_indexes)
    cell_results: list[dict[str, Any]] = []
    run_status = "ok"

    if code_cells:
        synthetic_path = _synthetic_session_path(normalized_path)
        with _temporary_runtime(
            client,
            kernel_name=normalized_kernel_name,
            session_path=synthetic_path,
            base_path=base_path,
        ) as runtime:
            deadline = monotonic() + normalized_timeout
            socket = _open_kernel_socket(
                client,
                runtime["base_path"],
                runtime["kernel_id"],
                runtime["client_session_id"],
                deadline,
                connector=_websocket_connect,
            )
            try:
                message_budget = {"count": 0, "bytes": 0}
                for cell_index, cell in code_cells:
                    source = _cell_source(cell, cell_index)

                    def cell_event(event: dict[str, Any], *, _index: int = cell_index) -> None:
                        if on_event is not None:
                            on_event({"cell_index": _index, **event})

                    result = _execute_on_socket(
                        socket,
                        source,
                        runtime["client_session_id"],
                        deadline,
                        on_event=cell_event if on_event is not None else None,
                        message_budget=message_budget,
                    )
                    cell["execution_count"] = result["execution_count"]
                    cell["outputs"] = copy.deepcopy(result["outputs"])
                    cell_results.append({"cell_index": cell_index, **result})
                    if result["status"] != "ok":
                        run_status = result["status"]
                        break
            finally:
                _close_socket(socket)

    return {
        "status": run_status,
        "path": normalized_path,
        "total_code_cells": len(all_code_cells),
        "selected_code_cells": len(code_cells),
        "executed_cells": len(cell_results),
        "cells": cell_results,
        "saved": False,
    }


@contextmanager
def _temporary_runtime(
    client: ApiClient,
    *,
    kernel_name: str | None,
    session_path: str,
    base_path: str | None = None,
) -> Iterator[dict[str, str]]:
    if base_path is None:
        base_path = _bootstrap_research(client)
    client.get(f"{base_path}api")

    existing_kernels = _kernel_collection(client.get(f"{base_path}api/kernels"))
    existing_kernel_ids = {item["id"] for item in existing_kernels["items"]}
    existing_sessions = _session_collection(client.get(f"{base_path}api/sessions"))
    existing_session_ids = {item["id"] for item in existing_sessions["items"]}
    if any(item["path"] == session_path for item in existing_sessions["items"]):
        raise ApiError("研究平台临时会话路径发生冲突，请重试")

    cleanup: tuple[str, str] | None = None
    primary_error: BaseException | None = None
    try:
        try:
            raw = _create_session(client, base_path, session_path, kernel_name)
            candidate_session_id = _candidate_identifier(raw, "id")
            raw_kernel = raw.get("kernel") if isinstance(raw, dict) else None
            candidate_kernel_id = _candidate_identifier(raw_kernel, "id")
            if (
                candidate_session_id in existing_session_ids
                or candidate_kernel_id in existing_kernel_ids
            ):
                # Deleting a session that unexpectedly reused an existing
                # kernel could shut down a user-owned interactive kernel.
                raise ApiError("研究平台没有创建隔离的临时会话和内核")
            cleanup = ("session", candidate_session_id)
            session = _normalize_session(raw)
            if session["path"] != session_path:
                raise ApiError("研究平台临时会话路径与请求不一致")
            resource_id = session["kernel"]["id"]
        except JqcliError:
            if cleanup is None:
                cleanup = _recover_created_session(
                    client,
                    base_path,
                    session_path,
                    existing_session_ids,
                    existing_kernel_ids,
                )
            raise

        yield {
            "base_path": base_path,
            "kernel_id": resource_id,
            "client_session_id": uuid.uuid4().hex,
        }
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if cleanup is not None:
            try:
                _delete_temporary_resource(client, base_path, *cleanup)
            except JqcliError:
                if primary_error is None:
                    raise
                if isinstance(primary_error, JqcliError):
                    primary_error.details = {**primary_error.details, "cleanup_failed": True}


def _create_session(
    client: ApiClient,
    base_path: str,
    notebook_path: str,
    kernel_name: str | None,
) -> Any:
    endpoint = f"{base_path}api/sessions"
    kernel: dict[str, str] = {}
    if kernel_name is not None:
        kernel["name"] = kernel_name
    body = {
        "path": notebook_path,
        # Match Notebook 5.4.1's own Session._get_model payload.  The full
        # synthetic path is the ownership key; name remains intentionally empty.
        "name": "",
        "type": "notebook",
        "kernel": kernel,
    }
    return _post_created_json(client, endpoint, body)


def _post_created_json(client: ApiClient, endpoint: str, body: dict[str, Any]) -> Any:
    response = client.request_response(
        "POST",
        endpoint,
        json=body,
        headers={"X-XSRFToken": _xsrf_token_for(client, endpoint)},
    )
    if response.status_code != 201:
        raise ApiError(f"研究平台创建临时执行资源返回了意外状态（HTTP {response.status_code}）")
    try:
        return response.json()
    except ValueError:
        raise ApiError("研究平台返回了无效的临时执行资源 JSON") from None


def _delete_temporary_resource(
    client: ApiClient,
    base_path: str,
    resource_type: str,
    resource_id: str,
) -> None:
    collection = "sessions" if resource_type == "session" else "kernels"
    endpoint = f"{base_path}api/{collection}/{quote(resource_id, safe='')}"
    response = client.request_response(
        "DELETE",
        endpoint,
        headers={"X-XSRFToken": _xsrf_token_for(client, endpoint)},
    )
    if response.status_code != 204:
        raise ApiError(f"研究平台清理临时执行资源返回了意外状态（HTTP {response.status_code}）")


def _recover_created_session(
    client: ApiClient,
    base_path: str,
    synthetic_path: str,
    existing_session_ids: set[str],
    existing_kernel_ids: set[str],
) -> tuple[str, str] | None:
    """Find a lost POST result only by its unguessable synthetic path."""
    try:
        sessions = _session_collection(client.get(f"{base_path}api/sessions"))["items"]
    except JqcliError:
        return None
    matches = [session for session in sessions if session["path"] == synthetic_path]
    if len(matches) != 1:
        return None
    session = matches[0]
    if session["id"] in existing_session_ids or session["kernel"]["id"] in existing_kernel_ids:
        return None
    return "session", session["id"]


def _xsrf_token_for(client: ApiClient, endpoint: str) -> str:
    cookie = client.get_cookie("_xsrf", endpoint)
    if not cookie:
        raise ApiError("研究平台未提供远端执行所需的 XSRF 令牌")
    return _decode_xsrf_cookie(cookie)


def _open_kernel_socket(
    client: ApiClient,
    base_path: str,
    kernel_id: str,
    client_session_id: str,
    deadline: float,
    *,
    connector: _WebSocketConnector | None,
) -> Any:
    remaining = _remaining_time(deadline)
    ws_url, origin, http_endpoint = _kernel_channel_urls(
        client.api_base,
        base_path,
        kernel_id,
        client_session_id,
    )
    cookie_header = client.get_cookie_header(http_endpoint)
    additional_headers = {"Cookie": cookie_header} if cookie_header else None
    connect = connector or websocket_connect
    try:
        return connect(
            ws_url,
            origin=origin,
            additional_headers=additional_headers,
            proxy=None,
            open_timeout=remaining,
            close_timeout=min(5.0, remaining),
            max_size=_MAX_WEBSOCKET_MESSAGE_SIZE,
        )
    except builtins.TimeoutError:
        raise TimeoutError("研究平台交互连接建立超时") from None
    except (InvalidHandshake, OSError):
        raise NetworkError("无法建立研究平台交互连接") from None


def _kernel_channel_urls(
    api_base: str,
    base_path: str,
    kernel_id: str,
    client_session_id: str,
) -> tuple[str, str, str]:
    try:
        parsed = urlsplit(api_base)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError
        _ = parsed.port
    except (UnicodeError, ValueError):
        raise ApiError("API 基地址不能用于研究平台交互连接") from None

    endpoint = f"{base_path}api/kernels/{quote(kernel_id, safe='')}/channels"
    query = urlencode({"session_id": client_session_id})
    ws_scheme = "wss" if parsed.scheme.lower() == "https" else "ws"
    ws_url = urlunsplit((ws_scheme, parsed.netloc, endpoint, query, ""))
    origin = urlunsplit((parsed.scheme.lower(), parsed.netloc, "", "", ""))
    http_endpoint = urlunsplit((parsed.scheme.lower(), parsed.netloc, endpoint, query, ""))
    return ws_url, origin, http_endpoint


def _execute_on_socket(
    socket: Any,
    code: str,
    client_session_id: str,
    deadline: float,
    *,
    on_event: _EventHandler | None,
    message_budget: dict[str, int] | None = None,
) -> dict[str, Any]:
    message_id = uuid.uuid4().hex
    request = {
        "header": {
            "msg_id": message_id,
            "username": "jqcli",
            "session": client_session_id,
            "date": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "msg_type": "execute_request",
            "version": "5.2",
        },
        "parent_header": {},
        "metadata": {},
        "content": {
            "code": code,
            "silent": False,
            "store_history": True,
            "user_expressions": {},
            "allow_stdin": False,
            "stop_on_error": True,
        },
        "channel": "shell",
        "buffers": [],
    }
    try:
        socket.send(json.dumps(request, ensure_ascii=False, separators=(",", ":")))
    except (ConnectionClosed, OSError):
        raise NetworkError("研究平台交互连接在发送请求时关闭") from None

    outputs: list[dict[str, Any]] = []
    execution_count: int | None = None
    reply_status: str | None = None
    shell_reply_received = False
    idle_received = False
    clear_on_next_output = False
    if message_budget is None:
        message_budget = {"count": 0, "bytes": 0}

    while not (shell_reply_received and idle_received):
        remaining = _remaining_time(deadline)
        try:
            raw = socket.recv(timeout=remaining)
        except builtins.TimeoutError:
            raise TimeoutError("研究平台代码执行超时") from None
        except (ConnectionClosed, OSError):
            raise NetworkError("研究平台交互连接意外关闭") from None

        frame_size = _kernel_frame_size(raw)
        message_budget["count"] += 1
        message_budget["bytes"] += frame_size
        if (
            frame_size > _MAX_WEBSOCKET_MESSAGE_SIZE
            or message_budget["count"] > _MAX_EXECUTION_MESSAGE_COUNT
            or message_budget["bytes"] > _MAX_EXECUTION_TOTAL_BYTES
        ):
            raise ApiError("研究平台内核消息超过安全处理上限")
        message = _decode_kernel_message(raw)
        parent_header = message.get("parent_header")
        if not isinstance(parent_header, dict) or parent_header.get("msg_id") != message_id:
            continue
        header = message.get("header")
        if not isinstance(header, dict) or not isinstance(header.get("msg_type"), str):
            raise ApiError("研究平台返回了无效的内核消息头")
        message_type = header["msg_type"]
        channel = message.get("channel")
        content = message.get("content")
        if not isinstance(channel, str) or not isinstance(content, dict):
            raise ApiError("研究平台返回了无效的内核消息")

        if message_type == "input_request":
            raise ApiError("远端代码请求交互输入；当前执行禁用了 stdin")

        if channel == "shell" and message_type == "execute_reply":
            reply_status = content.get("status")
            if reply_status not in {"ok", "error", "abort"}:
                raise ApiError("研究平台返回了无效的执行结果状态")
            execution_count = _optional_execution_count(content.get("execution_count"))
            shell_reply_received = True
            _emit_event(
                on_event,
                {
                    "event": "execute_reply",
                    "status": "aborted" if reply_status == "abort" else reply_status,
                    "execution_count": execution_count,
                },
            )
            continue

        if channel != "iopub":
            continue
        if message_type == "status":
            state = content.get("execution_state")
            if not isinstance(state, str):
                raise ApiError("研究平台返回了无效的内核状态")
            idle_received = state == "idle" or idle_received
            _emit_event(on_event, {"event": "status", "state": state})
            continue
        if message_type == "execute_input":
            execution_count = _optional_execution_count(content.get("execution_count"))
            continue
        if message_type == "clear_output":
            wait = content.get("wait", False)
            if not isinstance(wait, bool):
                raise ApiError("研究平台返回了无效的 clear_output 消息")
            if wait:
                clear_on_next_output = True
            else:
                outputs.clear()
                clear_on_next_output = False
            _emit_event(on_event, {"event": "clear_output", "wait": wait})
            continue
        if message_type in {"stream", "display_data", "execute_result", "update_display_data", "error"}:
            output = _normalize_output(message_type, content)
            if clear_on_next_output:
                outputs.clear()
                clear_on_next_output = False
            outputs.append(output)
            _emit_event(on_event, {"event": "output", "output": copy.deepcopy(output)})

    status = "aborted" if reply_status == "abort" else reply_status
    if any(output["output_type"] == "error" for output in outputs):
        status = "error"
    return {
        "status": status,
        "execution_count": execution_count,
        "outputs": outputs,
    }


def _decode_kernel_message(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        encoded = raw
    elif isinstance(raw, (bytes, bytearray, memoryview)):
        encoded = _decode_binary_kernel_frame(bytes(raw))
    else:
        raise ApiError("研究平台返回了不支持的内核消息类型")
    try:
        payload = json.loads(encoded, parse_constant=_reject_json_constant)
    except (RecursionError, TypeError, UnicodeError, ValueError):
        raise ApiError("研究平台返回了无效的内核消息 JSON") from None
    if not isinstance(payload, dict):
        raise ApiError("研究平台返回了无效的内核消息")
    return payload


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _decode_binary_kernel_frame(raw: bytes) -> str:
    # Notebook 5.4.1 serialize.js stores a big-endian uint32 buffer count,
    # followed by that many big-endian offsets.  Buffer zero is UTF-8 JSON.
    if len(raw) < 8:
        raise ApiError("研究平台返回了无效的二进制内核消息")
    buffer_count = int.from_bytes(raw[0:4], "big")
    if buffer_count < 1 or buffer_count > (len(raw) - 4) // 4:
        raise ApiError("研究平台返回了无效的二进制内核消息")
    header_size = 4 * (buffer_count + 1)
    offsets = [
        int.from_bytes(raw[4 * index : 4 * (index + 1)], "big")
        for index in range(1, buffer_count + 1)
    ]
    if (
        offsets[0] != header_size
        or any(offset > len(raw) for offset in offsets)
        or any(left > right for left, right in zip(offsets, offsets[1:]))
    ):
        raise ApiError("研究平台返回了无效的二进制内核消息")
    json_end = offsets[1] if len(offsets) > 1 else len(raw)
    try:
        return raw[offsets[0] : json_end].decode("utf-8", errors="strict")
    except UnicodeError:
        raise ApiError("研究平台返回了无效的二进制内核消息") from None


def _kernel_frame_size(raw: Any) -> int:
    if isinstance(raw, str):
        return len(raw.encode("utf-8"))
    if isinstance(raw, (bytes, bytearray, memoryview)):
        return len(raw)
    raise ApiError("研究平台返回了不支持的内核消息类型")


def _normalize_output(message_type: str, content: dict[str, Any]) -> dict[str, Any]:
    if message_type == "stream":
        name = content.get("name")
        text = content.get("text")
        if name not in {"stdout", "stderr"} or not isinstance(text, str):
            raise ApiError("研究平台返回了无效的 stream 输出")
        return {"output_type": "stream", "name": name, "text": text}
    if message_type in {"display_data", "update_display_data"}:
        data, metadata = _display_payload(content)
        return {"output_type": "display_data", "data": data, "metadata": metadata}
    if message_type == "execute_result":
        data, metadata = _display_payload(content)
        return {
            "output_type": "execute_result",
            "execution_count": _optional_execution_count(content.get("execution_count")),
            "data": data,
            "metadata": metadata,
        }
    if message_type == "error":
        ename = content.get("ename")
        evalue = content.get("evalue")
        traceback = content.get("traceback")
        if (
            not isinstance(ename, str)
            or not isinstance(evalue, str)
            or not isinstance(traceback, list)
            or not all(isinstance(line, str) for line in traceback)
        ):
            raise ApiError("研究平台返回了无效的 error 输出")
        return {
            "output_type": "error",
            "ename": ename,
            "evalue": evalue,
            "traceback": traceback,
        }
    raise ApiError("研究平台返回了不支持的输出类型")


def _display_payload(content: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    data = content.get("data")
    metadata = content.get("metadata")
    if not isinstance(data, dict) or not isinstance(metadata, dict):
        raise ApiError("研究平台返回了无效的富媒体输出")
    return copy.deepcopy(data), copy.deepcopy(metadata)


def _normalize_kernelspecs(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("default"), str):
        raise ApiError("研究平台返回了无效的 KernelSpec 响应")
    specs = raw.get("kernelspecs")
    if not isinstance(specs, dict):
        raise ApiError("研究平台返回了无效的 KernelSpec 响应")
    items: list[dict[str, Any]] = []
    for name, value in specs.items():
        if not isinstance(name, str) or not isinstance(value, dict) or not isinstance(value.get("spec"), dict):
            raise ApiError("研究平台返回了无效的 KernelSpec 项")
        spec = value["spec"]
        display_name = spec.get("display_name")
        language = spec.get("language")
        interrupt_mode = spec.get("interrupt_mode")
        if (
            not isinstance(display_name, str)
            or not isinstance(language, str)
            or (interrupt_mode is not None and not isinstance(interrupt_mode, str))
        ):
            raise ApiError("研究平台返回了无效的 KernelSpec 项")
        items.append(
            {
                "name": name,
                "display_name": display_name,
                "language": language,
                "interrupt_mode": interrupt_mode,
            }
        )
    items.sort(key=lambda item: item["name"])
    if raw["default"] not in specs:
        raise ApiError("研究平台默认 KernelSpec 不存在")
    return {"default": raw["default"], "items": items, "total": len(items)}


def _kernel_collection(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, list):
        raise ApiError("研究平台返回了无效的内核列表")
    items = [_normalize_kernel(item) for item in raw]
    return {"items": items, "total": len(items)}


def _session_collection(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, list):
        raise ApiError("研究平台返回了无效的会话列表")
    items = [_normalize_session(item) for item in raw]
    return {"items": items, "total": len(items)}


def _normalize_kernel(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError("研究平台返回了无效的内核项")
    identifier = _candidate_identifier(raw, "id")
    name = raw.get("name")
    last_activity = raw.get("last_activity")
    execution_state = raw.get("execution_state")
    connections = raw.get("connections")
    if (
        not isinstance(name, str)
        or (last_activity is not None and not isinstance(last_activity, str))
        or (execution_state is not None and not isinstance(execution_state, str))
        or (connections is not None and (not isinstance(connections, int) or isinstance(connections, bool)))
    ):
        raise ApiError("研究平台返回了无效的内核项")
    return {
        "id": identifier,
        "name": name,
        "last_activity": last_activity,
        "execution_state": execution_state,
        "connections": connections,
    }


def _normalize_session(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError("研究平台返回了无效的会话项")
    identifier = _candidate_identifier(raw, "id")
    path = raw.get("path")
    name = raw.get("name")
    session_type = raw.get("type")
    if not all(isinstance(value, str) for value in (path, name, session_type)):
        raise ApiError("研究平台返回了无效的会话项")
    kernel = _normalize_kernel(raw.get("kernel"))
    return {"id": identifier, "path": path, "name": name, "type": session_type, "kernel": kernel}


def _candidate_identifier(raw: Any, field: str) -> str:
    value = raw.get(field) if isinstance(raw, dict) else None
    if (
        not isinstance(value, str)
        or not value
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ApiError("研究平台返回了无效的临时资源标识")
    return value


def _validated_notebook_copy(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("type") != "notebook" or not isinstance(item.get("content"), dict):
        raise ApiError("指定的研究平台文件不是有效 Notebook")
    notebook = copy.deepcopy(item["content"])
    cells = notebook.get("cells")
    if not isinstance(cells, list) or not all(isinstance(cell, dict) for cell in cells):
        raise ApiError("研究平台 Notebook 包含无效的 cells")
    return notebook


def _cell_source(cell: dict[str, Any], cell_index: int) -> str:
    source = cell.get("source", "")
    if isinstance(source, str):
        return source
    if isinstance(source, list) and all(isinstance(part, str) for part in source):
        return "".join(source)
    raise ApiError("研究平台 Notebook 包含无效的代码单元", details={"cell_index": cell_index})


def _notebook_kernel_name(notebook: dict[str, Any]) -> str | None:
    metadata = notebook.get("metadata")
    if not isinstance(metadata, dict):
        return None
    kernelspec = metadata.get("kernelspec")
    if not isinstance(kernelspec, dict):
        return None
    name = kernelspec.get("name")
    if not isinstance(name, str) or not name:
        return None
    try:
        return _normalize_kernel_name(name)
    except UsageError:
        raise ApiError("研究平台 Notebook 包含无效的 kernelspec 名称") from None


def _select_code_cells(
    notebook_cells: list[dict[str, Any]],
    all_code_cells: list[tuple[int, dict[str, Any]]],
    selected: list[int] | None,
) -> list[tuple[int, dict[str, Any]]]:
    if selected is None:
        return all_code_cells
    if not isinstance(selected, list) or any(
        isinstance(index, bool) or not isinstance(index, int) or index < 0 for index in selected
    ):
        raise UsageError("Notebook 单元索引必须是非负整数")
    if len(set(selected)) != len(selected):
        raise UsageError("Notebook 单元索引不能重复")
    selected_indexes = set(selected)
    for index in selected:
        if index >= len(notebook_cells):
            raise UsageError(f"Notebook 单元索引越界：{index}")
        if notebook_cells[index].get("cell_type") != "code":
            raise UsageError(f"Notebook 单元不是代码单元：{index}")
    # Execute in notebook order so dependencies remain deterministic even when
    # repeated --cell options are provided out of order.
    return [(index, cell) for index, cell in all_code_cells if index in selected_indexes]


def _synthetic_session_path(notebook_path: str = "", *, prefix: str = "run") -> str:
    directory, separator, _ = notebook_path.rpartition("/")
    name = f"__jqcli_{prefix}_{uuid.uuid4().hex}.ipynb"
    return f"{directory}/{name}" if separator else name


def _normalize_kernel_name(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise UsageError("研究平台内核名称无效")
    return value


def _normalize_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UsageError("研究平台执行超时必须是正数")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise UsageError("研究平台执行超时必须是正数")
    return timeout


def _remaining_time(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError("研究平台代码执行超时")
    return remaining


def _optional_execution_count(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ApiError("研究平台返回了无效的 execution_count")
    return value


def _emit_event(handler: _EventHandler | None, event: dict[str, Any]) -> None:
    if handler is not None:
        handler(event)


def _close_socket(socket: Any) -> None:
    try:
        socket.close()
    except (ConnectionClosed, OSError):
        return


__all__ = [
    "execute_research_code",
    "list_research_kernels",
    "list_research_kernelspecs",
    "list_research_sessions",
    "run_research_notebook",
]
