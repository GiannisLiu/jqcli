import builtins
import json
import re
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

import jqcli.api.research_execution as execution_api
from jqcli.api.client import ApiClient
from jqcli.api.research_execution import (
    execute_research_code,
    list_research_kernels,
    list_research_kernelspecs,
    list_research_sessions,
    run_research_notebook,
)
from jqcli.errors import ApiError, NetworkError, NotAuthenticatedError, TimeoutError, UsageError


SSO_SCRIPT = """
var mob = 'private-user';
var sessionId = 'private-sso-token';
Cy.postRedirect('/research/login', {username: mob, token: sessionId});
"""
LOGIN_HTML = '<html><body data-base-url="/user/tester/"></body></html>'


def kernel_model(identifier="new-kernel", name="python3"):
    return {
        "id": identifier,
        "name": name,
        "last_activity": "2026-08-15T01:02:03Z",
        "execution_state": "idle",
        "connections": 0,
        "private": "must-not-leak",
    }


def session_model(path, identifier="new-session", kernel_id="new-kernel"):
    return {
        "id": identifier,
        "path": path,
        "name": path.rsplit("/", 1)[-1],
        "type": "notebook",
        "kernel": kernel_model(kernel_id),
        "private": "must-not-leak",
    }


def notebook_model(path="folder/demo.ipynb"):
    return {
        "name": path.rsplit("/", 1)[-1],
        "path": path,
        "type": "notebook",
        "writable": True,
        "created": "2026-08-01T01:02:03Z",
        "last_modified": "2026-08-02T03:04:05Z",
        "mimetype": None,
        "format": "json",
        "content": {
            "cells": [
                {"cell_type": "code", "source": ["value = 1\n", "value"], "outputs": []},
                {"cell_type": "markdown", "source": ["# title"]},
                {"cell_type": "code", "source": "value + 1", "outputs": []},
            ],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 2,
        },
    }


class FakeResearchServer:
    def __init__(self):
        self.requests = []
        self.kernels = []
        self.sessions = []
        self.contents = notebook_model()
        self.kernel_create_status = 201
        self.kernel_create_payload = kernel_model()
        self.session_create_status = 201
        self.session_create_payload = None
        self.delete_status = 204
        self.kernel_post_error = None
        self.session_post_error = None
        self.bootstrap_count = 0

    def handler(self, request):
        self.requests.append(request)
        path = request.url.path
        if path == "/default/research/redirect":
            self.bootstrap_count += 1
            return httpx.Response(200, text=SSO_SCRIPT)
        if path == "/research/login":
            return httpx.Response(200, text=LOGIN_HTML)
        if path == "/user/tester/api":
            return httpx.Response(
                200,
                json={"version": "5.4.1"},
                headers={"set-cookie": "_xsrf=private-xsrf; Path=/user/tester/"},
            )
        if path == "/user/tester/api/kernelspecs":
            return httpx.Response(
                200,
                json={
                    "default": "python3",
                    "kernelspecs": {
                        "python3": {
                            "spec": {
                                "argv": ["private-command"],
                                "display_name": "Python 3",
                                "language": "python",
                                "interrupt_mode": "signal",
                                "env": {"PRIVATE": "secret"},
                            },
                            "resources": {"logo-64x64": "private-path"},
                        }
                    },
                },
            )
        if path == "/user/tester/api/kernels":
            if request.method == "GET":
                return httpx.Response(200, json=self.kernels)
            assert request.method == "POST"
            assert request.headers["x-xsrftoken"] == "private-xsrf"
            if self.kernel_post_error is not None:
                raise self.kernel_post_error("simulated transport failure", request=request)
            return httpx.Response(self.kernel_create_status, json=self.kernel_create_payload)
        if path == "/user/tester/api/sessions":
            if request.method == "GET":
                return httpx.Response(200, json=self.sessions)
            assert request.method == "POST"
            assert request.headers["x-xsrftoken"] == "private-xsrf"
            body = json.loads(request.content)
            if self.session_post_error is not None:
                created = session_model(body["path"])
                self.sessions = [created]
                raise self.session_post_error("simulated transport failure", request=request)
            payload = self.session_create_payload or session_model(body["path"])
            return httpx.Response(self.session_create_status, json=payload)
        if path.startswith("/user/tester/api/kernels/") or path.startswith(
            "/user/tester/api/sessions/"
        ):
            assert request.method == "DELETE"
            assert request.headers["x-xsrftoken"] == "private-xsrf"
            return httpx.Response(self.delete_status)
        if path.startswith("/user/tester/api/contents/"):
            assert request.method == "GET"
            assert request.url.params["content"] == "1"
            return httpx.Response(200, json=self.contents)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    def client(self):
        return ApiClient(
            "https://example.test",
            token="private-bearer",
            cookie="main_session=private-cookie",
            transport=httpx.MockTransport(self.handler),
        )


def message(request, message_type, channel, content):
    return {
        "header": {"msg_type": message_type},
        "parent_header": {"msg_id": request["header"]["msg_id"]},
        "metadata": {},
        "content": content,
        "channel": channel,
    }


def binary_frame(payload, *buffers):
    json_bytes = json.dumps(payload).encode("utf-8")
    chunks = (json_bytes,) + buffers
    count = len(chunks)
    header_size = 4 * (count + 1)
    offsets = []
    offset = header_size
    for chunk in chunks:
        offsets.append(offset)
        offset += len(chunk)
    header = count.to_bytes(4, "big") + b"".join(value.to_bytes(4, "big") for value in offsets)
    return header + b"".join(chunks)


def success_messages(request, execution_count=1):
    idle = message(request, "status", "iopub", {"execution_state": "idle"})
    return [
        json.dumps(message(request, "status", "iopub", {"execution_state": "busy"})),
        json.dumps(message(request, "stream", "iopub", {"name": "stdout", "text": "hello\n"})),
        json.dumps(
            message(
                request,
                "execute_result",
                "iopub",
                {
                    "execution_count": execution_count,
                    "data": {"text/plain": "2"},
                    "metadata": {},
                },
            )
        ),
        json.dumps(
            message(
                request,
                "execute_reply",
                "shell",
                {"status": "ok", "execution_count": execution_count},
            )
        ),
        binary_frame(idle, b"ignored-buffer"),
    ]


class FakeSocket:
    def __init__(self, builder, *, timeout=False):
        self.builder = builder
        self.timeout = timeout
        self.messages = []
        self.sent = []
        self.closed = False

    def send(self, raw):
        request = json.loads(raw)
        self.sent.append(request)
        self.messages.extend(self.builder(request, len(self.sent)))

    def recv(self, timeout):
        assert timeout > 0
        if self.timeout:
            raise builtins.TimeoutError
        if not self.messages:
            raise AssertionError("fake websocket message queue is empty")
        return self.messages.pop(0)

    def close(self):
        self.closed = True


class FakeConnector:
    def __init__(self, builder=success_messages, *, timeout=False):
        self.socket = FakeSocket(builder, timeout=timeout)
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.socket


def delete_requests(server):
    return [request for request in server.requests if request.method == "DELETE"]


def test_list_runtime_metadata_is_sanitized_and_read_only():
    server = FakeResearchServer()
    server.kernels = [kernel_model("existing-kernel")]
    server.sessions = [session_model("existing.ipynb", "existing-session", "existing-kernel")]
    client = server.client()

    specs = list_research_kernelspecs(client)
    kernels = list_research_kernels(client)
    sessions = list_research_sessions(client)

    assert specs == {
        "default": "python3",
        "items": [
            {
                "name": "python3",
                "display_name": "Python 3",
                "language": "python",
                "interrupt_mode": "signal",
            }
        ],
        "total": 1,
    }
    assert kernels["items"][0]["id"] == "existing-kernel"
    assert sessions["items"][0]["kernel"]["id"] == "existing-kernel"
    assert "private-command" not in json.dumps(specs)
    assert delete_requests(server) == []


def test_execute_collects_outputs_uses_exact_origin_cookie_only_and_cleans_up():
    server = FakeResearchServer()
    connector = FakeConnector()
    events = []

    result = execute_research_code(
        server.client(),
        "print('hello'); 1 + 1",
        kernel_name="python3",
        execution_timeout=10,
        on_event=events.append,
        _websocket_connect=connector,
    )

    assert result == {
        "status": "ok",
        "execution_count": 1,
        "outputs": [
            {"output_type": "stream", "name": "stdout", "text": "hello\n"},
            {
                "output_type": "execute_result",
                "execution_count": 1,
                "data": {"text/plain": "2"},
                "metadata": {},
            },
        ],
    }
    assert [event["event"] for event in events] == [
        "status",
        "output",
        "output",
        "execute_reply",
        "status",
    ]
    url, kwargs = connector.calls[0]
    parsed = urlsplit(url)
    assert parsed.scheme == "wss"
    assert parsed.netloc == "example.test"
    assert parsed.path == "/user/tester/api/kernels/new-kernel/channels"
    assert len(parse_qs(parsed.query)["session_id"][0]) == 32
    assert kwargs["origin"] == "https://example.test"
    assert kwargs["proxy"] is None
    assert set(kwargs["additional_headers"]) == {"Cookie"}
    assert "private-bearer" not in str(kwargs)
    assert connector.socket.sent[0]["content"]["allow_stdin"] is False
    assert connector.socket.sent[0]["content"]["code"] == "print('hello'); 1 + 1"
    assert connector.socket.closed is True
    assert len(delete_requests(server)) == 1
    assert delete_requests(server)[0].url.path == "/user/tester/api/sessions/new-session"
    session_posts = [
        request
        for request in server.requests
        if request.method == "POST" and request.url.path == "/user/tester/api/sessions"
    ]
    assert len(session_posts) == 1
    posted_session = json.loads(session_posts[0].content)
    assert posted_session["name"] == ""
    assert posted_session["type"] == "notebook"
    assert posted_session["kernel"] == {"name": "python3"}
    assert re.fullmatch(
        r"__jqcli_exec_[0-9a-f]{32}\.ipynb",
        posted_session["path"],
    )
    assert not any(
        request.method == "POST" and request.url.path == "/user/tester/api/kernels"
        for request in server.requests
    )
    assert "new-kernel" not in json.dumps(result)
    assert "private-cookie" not in json.dumps(result)


def test_execute_returns_kernel_error_as_notebook_output_and_cleans_up():
    def builder(request, _):
        return [
            json.dumps(
                message(
                    request,
                    "error",
                    "iopub",
                    {"ename": "ValueError", "evalue": "bad", "traceback": ["trace"]},
                )
            ),
            json.dumps(
                message(
                    request,
                    "execute_reply",
                    "shell",
                    {"status": "error", "execution_count": 3},
                )
            ),
            json.dumps(message(request, "status", "iopub", {"execution_state": "idle"})),
        ]

    server = FakeResearchServer()
    result = execute_research_code(
        server.client(), "raise ValueError('bad')", _websocket_connect=FakeConnector(builder)
    )

    assert result["status"] == "error"
    assert result["outputs"] == [
        {
            "output_type": "error",
            "ename": "ValueError",
            "evalue": "bad",
            "traceback": ["trace"],
        }
    ]
    assert len(delete_requests(server)) == 1


def test_execute_timeout_and_input_request_both_cleanup():
    timeout_server = FakeResearchServer()
    with pytest.raises(TimeoutError):
        execute_research_code(
            timeout_server.client(),
            "input()",
            execution_timeout=0.5,
            _websocket_connect=FakeConnector(timeout=True),
        )
    assert len(delete_requests(timeout_server)) == 1

    def input_builder(request, _):
        return [
            json.dumps(
                message(
                    request,
                    "input_request",
                    "stdin",
                    {"prompt": "secret", "password": True},
                )
            )
        ]

    input_server = FakeResearchServer()
    with pytest.raises(ApiError, match="stdin"):
        execute_research_code(
            input_server.client(), "input()", _websocket_connect=FakeConnector(input_builder)
        )
    assert len(delete_requests(input_server)) == 1


def test_invalid_created_session_schema_is_cleaned_before_websocket():
    server = FakeResearchServer()
    server.session_create_payload = session_model("__jqcli_exec_placeholder.ipynb")
    server.session_create_payload["kernel"]["name"] = 123
    connector = FakeConnector()

    with pytest.raises(ApiError):
        execute_research_code(server.client(), "1", _websocket_connect=connector)

    assert connector.calls == []
    assert len(delete_requests(server)) == 1


def test_create_redirect_is_not_followed_and_no_websocket_credentials_are_sent():
    server = FakeResearchServer()

    def redirecting_handler(request):
        if request.url.path == "/user/tester/api/sessions" and request.method == "POST":
            server.requests.append(request)
            return httpx.Response(302, headers={"location": "/hub/login"})
        return server.handler(request)

    client = ApiClient(
        "https://example.test",
        cookie="main_session=private-cookie",
        transport=httpx.MockTransport(redirecting_handler),
    )
    connector = FakeConnector()

    with pytest.raises(NotAuthenticatedError):
        execute_research_code(client, "1", _websocket_connect=connector)

    assert connector.calls == []
    assert delete_requests(server) == []


def test_message_budget_is_cumulative_and_cleanup_still_runs(monkeypatch):
    server = FakeResearchServer()
    monkeypatch.setattr(execution_api, "_MAX_EXECUTION_MESSAGE_COUNT", 1)

    with pytest.raises(ApiError, match="安全处理上限"):
        execute_research_code(server.client(), "1", _websocket_connect=FakeConnector())

    assert len(delete_requests(server)) == 1


def test_notebook_message_budget_is_shared_across_cells(monkeypatch):
    server = FakeResearchServer()
    monkeypatch.setattr(execution_api, "_MAX_EXECUTION_MESSAGE_COUNT", 5)

    with pytest.raises(ApiError, match="安全处理上限"):
        run_research_notebook(
            server.client(),
            "folder/demo.ipynb",
            cell_indexes=[0, 2],
            _websocket_connect=FakeConnector(),
        )

    assert len(delete_requests(server)) == 1


def test_non_finite_kernel_json_is_rejected_and_cleanup_still_runs():
    def builder(request, _):
        return [
            json.dumps(
                message(request, "status", "iopub", {"execution_state": float("nan")})
            )
        ]

    server = FakeResearchServer()
    with pytest.raises(ApiError, match="JSON"):
        execute_research_code(server.client(), "1", _websocket_connect=FakeConnector(builder))
    assert len(delete_requests(server)) == 1


def test_cleanup_requires_documented_204_status():
    server = FakeResearchServer()
    server.delete_status = 200

    with pytest.raises(ApiError, match="清理"):
        execute_research_code(server.client(), "1", _websocket_connect=FakeConnector())


def test_run_uses_one_bootstrap_synthetic_session_selected_cells_and_never_saves():
    server = FakeResearchServer()
    server.contents["content"]["metadata"]["kernelspec"] = {"name": "notebook-python"}

    def builder(request, count):
        return success_messages(request, execution_count=count)

    connector = FakeConnector(builder)
    result = run_research_notebook(
        server.client(),
        "folder/demo.ipynb",
        cell_indexes=[2, 0],
        execution_timeout=10,
        _websocket_connect=connector,
    )

    assert result["status"] == "ok"
    assert result["path"] == "folder/demo.ipynb"
    assert result["total_code_cells"] == 2
    assert result["selected_code_cells"] == 2
    assert result["executed_cells"] == 2
    assert [cell["cell_index"] for cell in result["cells"]] == [0, 2]
    assert result["saved"] is False
    assert [request["content"]["code"] for request in connector.socket.sent] == [
        "value = 1\nvalue",
        "value + 1",
    ]
    session_posts = [
        request
        for request in server.requests
        if request.method == "POST" and request.url.path == "/user/tester/api/sessions"
    ]
    assert len(session_posts) == 1
    posted_body = json.loads(session_posts[0].content)
    posted_path = posted_body["path"]
    assert re.fullmatch(r"folder/__jqcli_run_[0-9a-f]{32}\.ipynb", posted_path)
    assert posted_path != "folder/demo.ipynb"
    assert posted_body["kernel"] == {"name": "notebook-python"}
    assert server.bootstrap_count == 1
    assert len(delete_requests(server)) == 1
    assert delete_requests(server)[0].url.path == "/user/tester/api/sessions/new-session"
    assert not any(request.method == "PUT" for request in server.requests)
    assert "new-session" not in json.dumps(result)
    assert "new-kernel" not in json.dumps(result)


@pytest.mark.parametrize("indexes", [[1], [99], [0, 0], [-1]])
def test_run_rejects_invalid_cell_selection_before_remote_mutation(indexes):
    server = FakeResearchServer()

    with pytest.raises(UsageError):
        run_research_notebook(server.client(), "folder/demo.ipynb", cell_indexes=indexes)

    assert not any(
        request.method == "POST" and request.url.path == "/user/tester/api/sessions"
        for request in server.requests
    )
    assert delete_requests(server) == []


def test_lost_session_post_is_recovered_only_by_synthetic_path_and_cleaned():
    server = FakeResearchServer()
    server.session_post_error = httpx.ReadTimeout

    with pytest.raises(NetworkError):
        run_research_notebook(
            server.client(),
            "folder/demo.ipynb",
            cell_indexes=[0],
            _websocket_connect=FakeConnector(),
        )

    assert len(server.sessions) == 1
    assert re.fullmatch(r"folder/__jqcli_run_[0-9a-f]{32}\.ipynb", server.sessions[0]["path"])
    assert len(delete_requests(server)) == 1
    assert delete_requests(server)[0].url.path == "/user/tester/api/sessions/new-session"


def test_lost_exec_session_post_is_recovered_and_cleaned():
    server = FakeResearchServer()
    server.session_post_error = httpx.ReadTimeout

    with pytest.raises(NetworkError):
        execute_research_code(server.client(), "1", _websocket_connect=FakeConnector())

    assert len(server.sessions) == 1
    assert re.fullmatch(r"__jqcli_exec_[0-9a-f]{32}\.ipynb", server.sessions[0]["path"])
    assert len(delete_requests(server)) == 1
    assert delete_requests(server)[0].url.path == "/user/tester/api/sessions/new-session"


def test_synthetic_session_collision_fails_before_create_or_delete(monkeypatch):
    server = FakeResearchServer()
    server.sessions = [session_model("__jqcli_exec_fixed.ipynb", "user-session", "user-kernel")]
    monkeypatch.setattr(
        execution_api,
        "_synthetic_session_path",
        lambda *args, **kwargs: "__jqcli_exec_fixed.ipynb",
    )

    with pytest.raises(ApiError, match="冲突"):
        execute_research_code(server.client(), "1", _websocket_connect=FakeConnector())

    assert not any(
        request.method == "POST" and request.url.path == "/user/tester/api/sessions"
        for request in server.requests
    )
    assert delete_requests(server) == []


@pytest.mark.parametrize("code", ["", " \t\r\n"])
def test_execute_rejects_empty_code_before_bootstrap(code):
    server = FakeResearchServer()

    with pytest.raises(UsageError):
        execute_research_code(server.client(), code, _websocket_connect=FakeConnector())

    assert server.requests == []


def test_invalid_list_and_binary_schemas_fail_closed():
    server = FakeResearchServer()
    server.kernels = {"not": "a list"}
    with pytest.raises(ApiError):
        list_research_kernels(server.client())

    def malformed_binary(request, _):
        return [b"\x00\x00\x00\x01\x00\x00\x00\xff"]

    server = FakeResearchServer()
    with pytest.raises(ApiError):
        execute_research_code(
            server.client(), "1", _websocket_connect=FakeConnector(malformed_binary)
        )
    assert len(delete_requests(server)) == 1
