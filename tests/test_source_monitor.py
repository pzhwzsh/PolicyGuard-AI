import json
from pathlib import Path

import httpx

from policyguard.application.source_monitor import (
    OfficialSourceMonitor,
    canonical_source_content,
)


def test_source_monitor_records_baseline_then_change(tmp_path: Path) -> None:
    bodies = [b"version-one", b"version-two"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=bodies.pop(0), headers={"etag": "v"})

    monitor = OfficialSourceMonitor(
        httpx.Client(transport=httpx.MockTransport(handler)),
        tmp_path / "state.json",
        tmp_path / "history.jsonl",
        tmp_path / "snapshots",
    )
    source = [{"source_url": "https://official.test/rule", "title": "Rule"}]
    first = monitor.check(source)
    second = monitor.check(source)
    assert first[0].status == "baseline"
    assert second[0].status == "changed"
    assert first[0].new_hash != second[0].new_hash
    assert second[0].diff_path
    assert Path(second[0].diff_path).is_file()
    assert len((tmp_path / "history.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_source_monitor_uses_conditional_request(tmp_path: Path) -> None:
    state = {
        "https://official.test/rule": {
            "content_hash": "abc",
            "etag": "etag-1",
            "last_modified": None,
        }
    }
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["if-none-match"] == "etag-1"
        return httpx.Response(304)

    monitor = OfficialSourceMonitor(
        httpx.Client(transport=httpx.MockTransport(handler)),
        state_path,
        tmp_path / "history.jsonl",
    )
    result = monitor.check([{"source_url": "https://official.test/rule"}])
    assert result[0].status == "unchanged"
    assert result[0].new_hash == "abc"


def test_source_monitor_rejects_persistently_pending_response(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(202)

    monitor = OfficialSourceMonitor(
        httpx.Client(transport=httpx.MockTransport(handler)),
        tmp_path / "state.json",
        tmp_path / "history.jsonl",
        sleeper=lambda _: None,
    )
    result = monitor.check([{"source_url": "https://official.test/rule"}])
    assert result[0].status == "failed"
    assert "unexpected_source_status_202" in result[0].error
    assert calls == 3
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8")) == {}


def test_source_monitor_polls_202_location_until_content_is_ready(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(202, headers={"location": "https://official.test/job/1"})
        assert str(request.url) == "https://official.test/job/1"
        return httpx.Response(200, content=b"ready")

    monitor = OfficialSourceMonitor(
        httpx.Client(transport=httpx.MockTransport(handler)),
        tmp_path / "state.json",
        tmp_path / "history.jsonl",
        sleeper=lambda _: None,
    )
    result = monitor.check([{"source_url": "https://official.test/rule"}])
    assert result[0].status == "baseline"
    assert calls == 2


def test_html_hash_ignores_script_and_style_noise() -> None:
    first = canonical_source_content(
        b"<html><style>.x{}</style><body>Official rule<script>nonce=1</script></body></html>",
        "html",
    )
    second = canonical_source_content(
        b"<html><style>.y{}</style><body>Official rule<script>nonce=2</script></body></html>",
        "html",
    )
    assert first == second == b"Official rule"
