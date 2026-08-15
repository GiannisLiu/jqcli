import json
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, quote

import httpx
import pytest

from jqcli.api.client import ApiClient
from jqcli.api.research import (
    create_research_directory,
    delete_research_item,
    get_research_item,
    list_research_items,
    move_research_item,
    normalize_research_path,
    save_research_item,
)
from jqcli.errors import ApiError, NotAuthenticatedError, UsageError


MOB = "13800138000"
SESSION_ID = "one-time-session-secret"
SSO_SCRIPT = f"""
<script>
var mob = '{MOB}';
var sessionId = "{SESSION_ID}";
Cy.postRedirect('/research/login', {{username: mob, token: sessionId}});
</script>
"""
LOGIN_HTML = '<html><body data-base-url="/user/tester/"></body></html>'


def contents_model(name: str, path: str, type_: str, *, content=None) -> dict:
    return {
        "name": name,
        "path": path,
        "type": type_,
        "writable": True,
        "created": "2026-08-01T01:02:03Z",
        "last_modified": "2026-08-02T03:04:05Z",
        "mimetype": None if type_ in {"directory", "notebook"} else "text/plain",
        "format": "json" if type_ == "notebook" else None,
        # Notebook 5.4.1 responses observed in production omit size.  Keeping it
        # absent here verifies that the normalized API still exposes size=None.
        "content": content,
        "server_extension": "must not leak",
    }


def client_with_contents(contents_handler):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/default/research/redirect":
            return httpx.Response(200, text=SSO_SCRIPT)
        if request.url.path == "/research/login":
            assert request.method == "POST"
            assert parse_qs(request.content.decode()) == {"username": [MOB], "token": [SESSION_ID]}
            return httpx.Response(200, text=LOGIN_HTML)
        if request.url.path.startswith("/user/tester/api/contents"):
            return contents_handler(request)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    return ApiClient("https://example.test", token="api-token", transport=httpx.MockTransport(handler)), seen


def client_with_write(
    write_handler,
    *,
    set_xsrf: bool = True,
    xsrf_cookie: str = '"%63srf%2Dvalue"',
    unrelated_xsrf_cookie: str | None = None,
    root_xsrf_cookie: str | None = None,
):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/default/research/redirect":
            return httpx.Response(200, text=SSO_SCRIPT)
        if request.url.path == "/research/login":
            return httpx.Response(200, text=LOGIN_HTML)
        if request.url.path == "/user/tester/api":
            cookie_headers = []
            if set_xsrf:
                cookie_headers.append(("set-cookie", f"_xsrf={xsrf_cookie}; Path=/user/tester/"))
            if unrelated_xsrf_cookie is not None:
                cookie_headers.append(
                    (
                        "set-cookie",
                        f"_xsrf={unrelated_xsrf_cookie}; Path=/unrelated/very/long/path/",
                    )
                )
            if root_xsrf_cookie is not None:
                cookie_headers.append(("set-cookie", f"_xsrf={root_xsrf_cookie}; Path=/"))
            headers = httpx.Headers(cookie_headers)
            return httpx.Response(200, json={"version": "5.4.1"}, headers=headers)
        if request.url.path.startswith("/user/tester/api/contents/"):
            return write_handler(request)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    return ApiClient("https://example.test", transport=httpx.MockTransport(handler)), seen


def request_cookies(request: httpx.Request) -> dict[str, str]:
    parsed = SimpleCookie()
    parsed.load(request.headers.get("cookie", ""))
    return {name: morsel.value for name, morsel in parsed.items()}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", ""),
        ("///", ""),
        ("/folder/file.ipynb/", "folder/file.ipynb"),
        (r"\folder\nested\file.py", "folder/nested/file.py"),
    ],
)
def test_normalize_research_path(value, expected):
    assert normalize_research_path(value) == expected


@pytest.mark.parametrize("value", ["folder//file", "folder/./file", "folder/../file", "folder/\x00file"])
def test_normalize_research_path_rejects_unsafe_components(value):
    with pytest.raises(UsageError):
        normalize_research_path(value)


def test_list_root_bootstraps_sso_follows_cookie_redirect_and_filters_items():
    child = contents_model("策略.ipynb", "策略.ipynb", "notebook", content={"cells": []})
    root = contents_model("", "", "directory", content=[child])
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/default/research/redirect":
            assert request_cookies(request) == {"main_session": "keep"}
            return httpx.Response(200, text=SSO_SCRIPT)
        if request.url.path == "/research/login":
            assert request.method == "POST"
            assert parse_qs(request.content.decode()) == {"username": [MOB], "token": [SESSION_ID]}
            assert request_cookies(request) == {"main_session": "keep"}
            return httpx.Response(
                302,
                headers={"Location": "/research/tree", "Set-Cookie": "research_session=ready; Path=/"},
            )
        if request.url.path == "/research/tree":
            assert request.method == "GET"
            assert request_cookies(request) == {"main_session": "keep", "research_session": "ready"}
            return httpx.Response(200, text=LOGIN_HTML)
        if request.url.path == "/user/tester/api/contents":
            assert dict(request.url.params) == {"content": "1"}
            assert request_cookies(request) == {"main_session": "keep", "research_session": "ready"}
            return httpx.Response(200, json=root)
        raise AssertionError(request.url)

    client = ApiClient(
        "https://example.test",
        cookie="main_session=keep",
        transport=httpx.MockTransport(handler),
    )

    result = list_research_items(client)

    assert result == {
        "path": "",
        "items": [
            {
                "name": "策略.ipynb",
                "path": "策略.ipynb",
                "type": "notebook",
                "writable": True,
                "created": "2026-08-01T01:02:03Z",
                "last_modified": "2026-08-02T03:04:05Z",
                "mimetype": None,
                "format": "json",
                "size": None,
            }
        ],
        "total": 1,
    }
    assert seen == [
        ("GET", "/default/research/redirect"),
        ("POST", "/research/login"),
        ("GET", "/research/tree"),
        ("GET", "/user/tester/api/contents"),
    ]


def test_list_supports_joinquant_named_sso_form_object():
    script = f"""
    <script>
    var mob = '{MOB}';
    var sessionId = '{SESSION_ID}';
    var data = {{username: mob, token: sessionId}}
    Cy.postRedirect('/research/login', data);
    </script>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/default/research/redirect":
            return httpx.Response(200, text=script)
        if request.url.path == "/research/login":
            assert parse_qs(request.content.decode()) == {"username": [MOB], "token": [SESSION_ID]}
            return httpx.Response(200, text=LOGIN_HTML)
        if request.url.path == "/user/tester/api/contents":
            return httpx.Response(200, json=contents_model("", "", "directory", content=[]))
        raise AssertionError(request.url)

    client = ApiClient("https://example.test", cookie="sid=abc", transport=httpx.MockTransport(handler))

    assert list_research_items(client) == {"path": "", "items": [], "total": 0}


def test_list_subdirectory_encodes_chinese_and_spaces_without_encoding_slashes():
    directory = contents_model("子 目录", "研究 资料/子 目录", "directory", content=[])

    def contents_handler(request: httpx.Request) -> httpx.Response:
        encoded = quote("研究 资料/子 目录", safe="/").encode()
        assert request.url.raw_path.split(b"?", 1)[0] == b"/user/tester/api/contents/" + encoded
        assert dict(request.url.params) == {"content": "1"}
        return httpx.Response(200, json=directory)

    client, seen = client_with_contents(contents_handler)

    result = list_research_items(client, r"/研究 资料\子 目录/")

    assert result == {"path": "研究 资料/子 目录", "items": [], "total": 0}
    assert [request.url.path for request in seen].count("/default/research/redirect") == 1


def test_get_item_controls_content_and_bootstraps_once_per_call():
    notebook_content = {"cells": [{"cell_type": "code", "source": ["print(1)"]}], "nbformat": 4}

    def contents_handler(request: httpx.Request) -> httpx.Response:
        requested = request.url.params["content"]
        return httpx.Response(
            200,
            json=contents_model(
                "demo.ipynb",
                "demo.ipynb",
                "notebook",
                content=notebook_content if requested == "1" else None,
            ),
        )

    client, seen = client_with_contents(contents_handler)

    without_content = get_research_item(client, "/demo.ipynb/")
    with_content = get_research_item(client, "demo.ipynb", include_content=True)

    assert "content" not in without_content
    assert with_content["content"] == notebook_content
    assert [request.url.params["content"] for request in seen if "/api/contents" in request.url.path] == ["0", "1"]
    assert [request.url.path for request in seen].count("/default/research/redirect") == 2


@pytest.mark.parametrize(
    "script",
    [
        "var mob='private-mob'; var sessionId='private-token';",
        "var mob='private-mob'; Cy.postRedirect('/research/login', {username:mob, token:sessionId});",
        "var mob='private-mob'; var sessionId='private-token'; Cy.postRedirect('/research/login', {user:mob});",
    ],
)
def test_malformed_sso_is_not_authenticated_and_does_not_leak_secrets(script):
    client = ApiClient(
        "https://example.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=script)),
    )

    with pytest.raises(NotAuthenticatedError) as captured:
        list_research_items(client)

    rendered = f"{captured.value!s} {captured.value!r} {captured.value.message} {captured.value.details}"
    assert "private-mob" not in rendered
    assert "private-token" not in rendered


def test_cross_origin_sso_url_is_rejected_before_credentials_are_posted():
    script = f"""
    var mob='{MOB}';
    var sessionId='{SESSION_ID}';
    Cy.postRedirect('https://evil.example/research/login', {{username:mob,token:sessionId}});
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=script)

    client = ApiClient("https://example.test", transport=httpx.MockTransport(handler))

    with pytest.raises(ApiError) as captured:
        list_research_items(client)

    assert len(seen) == 1
    rendered = f"{captured.value!s} {captured.value!r} {captured.value.message} {captured.value.details}"
    assert MOB not in rendered
    assert SESSION_ID not in rendered


@pytest.mark.parametrize("status_code", [302, 307])
def test_cross_origin_sso_redirect_is_rejected_before_following(status_code):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/default/research/redirect":
            return httpx.Response(200, text=SSO_SCRIPT)
        if request.url.path == "/research/login":
            assert parse_qs(request.content.decode()) == {"username": [MOB], "token": [SESSION_ID]}
            return httpx.Response(status_code, headers={"location": "https://evil.example/capture"})
        raise AssertionError("cross-origin redirect must not be followed")

    client = ApiClient("https://example.test", cookie="sid=abc", transport=httpx.MockTransport(handler))

    with pytest.raises(ApiError) as captured:
        list_research_items(client)

    assert [request.url.host for request in seen] == ["example.test", "example.test"]
    rendered = f"{captured.value!s} {captured.value!r} {captured.value.message}"
    assert MOB not in rendered
    assert SESSION_ID not in rendered


def test_login_rejection_maps_to_not_authenticated_without_secret_leakage():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/default/research/redirect":
            return httpx.Response(200, text=SSO_SCRIPT)
        assert request.url.path == "/research/login"
        return httpx.Response(403, text="bad token")

    client = ApiClient("https://example.test", transport=httpx.MockTransport(handler))

    with pytest.raises(NotAuthenticatedError) as captured:
        get_research_item(client, "demo.ipynb")

    rendered = f"{captured.value!s} {captured.value!r} {captured.value.message} {captured.value.details}"
    assert MOB not in rendered
    assert SESSION_ID not in rendered


@pytest.mark.parametrize(
    ("login_html", "error_type"),
    [
        ("<html><body></body></html>", NotAuthenticatedError),
        ('<html><body data-base-url="/services/user/tester/"></body></html>', ApiError),
        ('<html><body data-base-url="/user/../"></body></html>', ApiError),
        ('<html><body data-base-url="https://evil.example/user/tester/"></body></html>', ApiError),
    ],
)
def test_login_page_requires_one_safe_jupyter_user_base(login_html, error_type):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/default/research/redirect":
            return httpx.Response(200, text=SSO_SCRIPT)
        return httpx.Response(200, text=login_html)

    client = ApiClient("https://example.test", transport=httpx.MockTransport(handler))

    with pytest.raises(error_type):
        list_research_items(client)


def test_invalid_contents_payload_raises_api_error():
    client, _ = client_with_contents(lambda request: httpx.Response(200, json=["not", "a", "model"]))

    with pytest.raises(ApiError):
        get_research_item(client, "demo.ipynb")


def test_list_rejects_non_directory_contents_model():
    file_model = contents_model("file.py", "file.py", "file", content="print(1)")
    client, _ = client_with_contents(lambda request: httpx.Response(200, json=file_model))

    with pytest.raises(ApiError):
        list_research_items(client, "file.py")


def test_save_research_item_primes_xsrf_and_puts_exact_contents_model():
    notebook_content = {"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 2}
    saved = contents_model("研究 笔记.ipynb", "目录/研究 笔记.ipynb", "notebook")

    def write_handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        expected_path = quote("目录/研究 笔记.ipynb", safe="/").encode()
        assert request.url.raw_path == b"/user/tester/api/contents/" + expected_path
        assert request.headers["x-xsrftoken"] == "csrf-value"
        assert json.loads(request.content) == {
            "type": "notebook",
            "format": "json",
            "content": notebook_content,
        }
        return httpx.Response(201, json=saved)

    client, seen = client_with_write(write_handler)

    result = save_research_item(
        client,
        r"/目录\研究 笔记.ipynb/",
        content=notebook_content,
        item_type="notebook",
        content_format="json",
    )

    assert result["path"] == "目录/研究 笔记.ipynb"
    assert "content" not in result
    assert [(request.method, request.url.path) for request in seen] == [
        ("GET", "/default/research/redirect"),
        ("POST", "/research/login"),
        ("GET", "/user/tester/api"),
        ("PUT", "/user/tester/api/contents/目录/研究 笔记.ipynb"),
    ]


def test_create_research_directory_uses_named_put():
    created = contents_model("新目录", "父目录/新目录", "directory")

    def write_handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert json.loads(request.content) == {"type": "directory"}
        assert request.headers["x-xsrftoken"] == "csrf-value"
        return httpx.Response(201, json=created)

    client, _ = client_with_write(write_handler)

    result = create_research_directory(client, "父目录/新目录")

    assert result["type"] == "directory"
    assert result["path"] == "父目录/新目录"


def test_move_research_item_patches_normalized_destination():
    moved = contents_model("新 名称.py", "目标/新 名称.py", "file")

    def write_handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        assert request.url.path == "/user/tester/api/contents/来源/旧.py"
        assert json.loads(request.content) == {"path": "目标/新 名称.py"}
        assert request.headers["x-xsrftoken"] == "csrf-value"
        return httpx.Response(200, json=moved)

    client, _ = client_with_write(write_handler)

    result = move_research_item(client, "来源/旧.py", r"/目标\新 名称.py/")

    assert result["path"] == "目标/新 名称.py"


def test_delete_research_item_returns_none_for_jupyter_204():
    def write_handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/user/tester/api/contents/临时.py"
        assert request.headers["x-xsrftoken"] == "csrf-value"
        return httpx.Response(204)

    client, _ = client_with_write(write_handler)

    assert delete_research_item(client, "临时.py") is None


def test_delete_research_item_does_not_treat_login_redirect_as_success():
    client, _ = client_with_write(
        lambda request: httpx.Response(302, headers={"location": "/hub/login"})
    )

    with pytest.raises(NotAuthenticatedError):
        delete_research_item(client, "临时.py")


def test_delete_research_item_requires_documented_204_status():
    client, _ = client_with_write(lambda request: httpx.Response(200, json={"ok": True}))

    with pytest.raises(ApiError):
        delete_research_item(client, "临时.py")


def test_write_fails_before_mutation_when_xsrf_cookie_is_missing():
    write_requests: list[httpx.Request] = []
    client, seen = client_with_write(lambda request: write_requests.append(request), set_xsrf=False)

    with pytest.raises(ApiError):
        create_research_directory(client, "safe-directory")

    assert write_requests == []
    assert [(request.method, request.url.path) for request in seen] == [
        ("GET", "/default/research/redirect"),
        ("POST", "/research/login"),
        ("GET", "/user/tester/api"),
    ]


def test_write_decodes_percent_encoded_xsrf_outer_quotes():
    created = contents_model("folder", "folder", "directory")

    def write_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-xsrftoken"] == "csrf-value"
        return httpx.Response(201, json=created)

    client, _ = client_with_write(write_handler, xsrf_cookie="%22csrf-value%22")

    assert create_research_directory(client, "folder")["path"] == "folder"


def test_write_uses_xsrf_cookie_matching_the_contents_endpoint():
    created = contents_model("folder", "folder", "directory")

    def write_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-xsrftoken"] == "csrf-value"
        assert request_cookies(request)["_xsrf"] == "%63srf%2Dvalue"
        return httpx.Response(201, json=created)

    client, _ = client_with_write(
        write_handler,
        unrelated_xsrf_cookie="wrong-token",
    )

    assert create_research_directory(client, "folder")["path"] == "folder"


def test_write_xsrf_header_matches_server_last_wins_cookie_value():
    created = contents_model("folder", "folder", "directory")

    def write_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-xsrftoken"] == "root-value"
        assert request_cookies(request)["_xsrf"] == "root-value"
        return httpx.Response(201, json=created)

    client, _ = client_with_write(
        write_handler,
        xsrf_cookie="specific-value",
        root_xsrf_cookie="root-value",
    )

    assert create_research_directory(client, "folder")["path"] == "folder"


def test_invalid_xsrf_cookie_does_not_leak_or_send_a_write():
    write_requests: list[httpx.Request] = []
    client, _ = client_with_write(
        lambda request: write_requests.append(request),
        xsrf_cookie="private-xsrf-secret%0Ainjected",
    )

    with pytest.raises(ApiError) as captured:
        create_research_directory(client, "folder")

    assert write_requests == []
    rendered = f"{captured.value!s} {captured.value!r} {captured.value.message} {captured.value.details}"
    assert "private-xsrf-secret" not in rendered


def test_write_operations_reject_the_root_before_bootstrap():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be sent for an invalid write target")

    client = ApiClient("https://example.test", transport=httpx.MockTransport(handler))

    with pytest.raises(UsageError):
        save_research_item(client, "/", content="", item_type="file", content_format="text")
    with pytest.raises(UsageError):
        create_research_directory(client, "")
    with pytest.raises(UsageError):
        move_research_item(client, "source", "/")
    with pytest.raises(UsageError):
        delete_research_item(client, "///")


@pytest.mark.parametrize(
    ("item_type", "content_format", "content"),
    [
        ("directory", "json", {}),
        ("notebook", "text", {}),
        ("notebook", "json", []),
        ("notebook", "json", {"cells": [b"not-json"]}),
        ("notebook", "json", {"metadata": {"score": float("nan")}}),
        ("file", "json", "{}"),
        ("file", "text", {"not": "text"}),
        ("file", "base64", b"bm90LWEtdGV4dC12YWx1ZQ=="),
    ],
)
def test_save_research_item_rejects_unsupported_payloads_before_bootstrap(
    item_type, content_format, content
):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("invalid save payload must be rejected before API access")

    client = ApiClient("https://example.test", transport=httpx.MockTransport(handler))

    with pytest.raises(ApiError):
        save_research_item(
            client,
            "item",
            content=content,
            item_type=item_type,
            content_format=content_format,
        )
