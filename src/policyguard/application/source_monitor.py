"""Official-source change detection without automatic legal-text replacement."""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from difflib import unified_diff
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from time import sleep

import httpx


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript", "svg", "template"}:
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg", "template"}:
            self.hidden_depth = max(0, self.hidden_depth - 1)

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth and data.strip():
            self.parts.append(data.strip())


def canonical_source_content(content: bytes, source_format: str) -> bytes:
    if source_format != "html":
        return content
    parser = _VisibleTextParser()
    parser.feed(content.decode("utf-8", errors="replace"))
    return "\n".join(" ".join(part.split()) for part in parser.parts).encode()


@dataclass(frozen=True, slots=True)
class SourceCheckResult:
    source_url: str
    status: str
    checked_at: str
    status_code: int | None
    old_hash: str | None
    new_hash: str | None
    etag: str | None
    last_modified: str | None
    snapshot_path: str | None
    diff_path: str | None
    error: str | None = None


class OfficialSourceMonitor:
    def __init__(
        self,
        client: httpx.Client,
        state_path: Path,
        history_path: Path,
        snapshot_dir: Path | None = None,
        pending_attempts: int = 3,
        sleeper=sleep,
    ) -> None:
        self.client = client
        self.state_path = state_path
        self.history_path = history_path
        self.snapshot_dir = snapshot_dir
        self.pending_attempts = pending_attempts
        self.sleeper = sleeper

    def check(self, sources: list[dict]) -> list[SourceCheckResult]:
        state = self._load_state()
        results = [self._check_one(source, state) for source in sources]
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as handle:
            for result in results:
                handle.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
        return results

    def _check_one(self, source: dict, state: dict) -> SourceCheckResult:
        url = source["source_url"]
        previous = state.get(url, {})
        headers = {}
        if previous.get("etag"):
            headers["If-None-Match"] = previous["etag"]
        if previous.get("last_modified"):
            headers["If-Modified-Since"] = previous["last_modified"]
        checked_at = datetime.now(UTC).isoformat()
        try:
            response = self._get_with_pending_poll(url, headers)
            if response.status_code == 304:
                status = "unchanged"
                new_hash = previous.get("content_hash")
                snapshot_path = previous.get("snapshot_path")
                diff_path = None
            else:
                response.raise_for_status()
                if response.status_code != 200:
                    raise RuntimeError(f"unexpected_source_status_{response.status_code}")
                if not response.content.strip():
                    raise RuntimeError("empty_source_response")
                canonical = canonical_source_content(
                    response.content, source.get("format", "binary")
                )
                if not canonical.strip():
                    raise RuntimeError("empty_canonical_source_content")
                new_hash = sha256(canonical).hexdigest()
                status = (
                    "changed"
                    if previous.get("content_hash") not in {None, new_hash}
                    else "baseline"
                )
                snapshot_path = self._save_snapshot(new_hash, response.content)
                diff_path = (
                    self._save_diff(
                        previous.get("content_hash"),
                        new_hash,
                        previous.get("snapshot_path"),
                        response.content,
                        source.get("format", "binary"),
                    )
                    if status == "changed"
                    else None
                )
            state[url] = {
                "title": source.get("title"),
                "publisher": source.get("publisher"),
                "content_hash": new_hash,
                "etag": response.headers.get("etag") or previous.get("etag"),
                "last_modified": response.headers.get("last-modified")
                or previous.get("last_modified"),
                "last_checked_at": checked_at,
                "snapshot_path": snapshot_path,
                "pending_diff_path": diff_path,
            }
            return SourceCheckResult(
                source_url=url,
                status=status,
                checked_at=checked_at,
                status_code=response.status_code,
                old_hash=previous.get("content_hash"),
                new_hash=new_hash,
                etag=state[url].get("etag"),
                last_modified=state[url].get("last_modified"),
                snapshot_path=snapshot_path,
                diff_path=diff_path,
            )
        except Exception as exc:
            return SourceCheckResult(
                source_url=url,
                status="failed",
                checked_at=checked_at,
                status_code=getattr(getattr(exc, "response", None), "status_code", None),
                old_hash=previous.get("content_hash"),
                new_hash=None,
                etag=previous.get("etag"),
                last_modified=previous.get("last_modified"),
                snapshot_path=previous.get("snapshot_path"),
                diff_path=None,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _save_diff(
        self,
        old_hash: str | None,
        new_hash: str,
        old_snapshot_path: str | None,
        new_content: bytes,
        source_format: str,
    ) -> str | None:
        if self.snapshot_dir is None or not old_hash or not old_snapshot_path:
            return None
        old_path = Path(old_snapshot_path)
        if not old_path.is_file():
            return None
        old = canonical_source_content(old_path.read_bytes(), source_format).decode(
            "utf-8", errors="replace"
        )
        new = canonical_source_content(new_content, source_format).decode(
            "utf-8", errors="replace"
        )
        diff = "\n".join(unified_diff(
            old.splitlines(), new.splitlines(),
            fromfile=f"{old_hash}.txt", tofile=f"{new_hash}.txt", lineterm="",
        ))
        diff_dir = self.snapshot_dir.parent / "diffs"
        diff_dir.mkdir(parents=True, exist_ok=True)
        path = diff_dir / f"{old_hash[:12]}-{new_hash[:12]}.diff"
        path.write_text(diff, encoding="utf-8")
        return str(path)

    def _get_with_pending_poll(self, url: str, headers: dict) -> httpx.Response:
        response = self.client.get(url, headers=headers)
        for attempt in range(1, self.pending_attempts + 1):
            if response.status_code != 202:
                return response
            if attempt == self.pending_attempts:
                break
            retry_after = response.headers.get("retry-after", "1")
            try:
                delay = min(max(float(retry_after), 0.0), 5.0)
            except ValueError:
                delay = 1.0
            self.sleeper(delay)
            poll_url = response.headers.get("location") or url
            response = self.client.get(poll_url, headers=headers)
        return response

    def _save_snapshot(self, content_hash: str, content: bytes) -> str | None:
        if self.snapshot_dir is None:
            return None
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        path = self.snapshot_dir / f"{content_hash}.bin"
        if not path.exists():
            path.write_bytes(content)
        return str(path)

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            return {}
        return json.loads(self.state_path.read_text(encoding="utf-8"))
