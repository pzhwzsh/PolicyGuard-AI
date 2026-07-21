import json
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit

import httpx


def probe_json_endpoint(
    client: httpx.Client, name: str, method: str, url: str, **kwargs
) -> dict:
    started = perf_counter()
    try:
        response = client.request(method, url, **kwargs)
        response.raise_for_status()
        response.json()
        return {
            "name": name, "status": "ok", "status_code": response.status_code,
            "latency_ms": round((perf_counter() - started) * 1000, 3),
            "host": urlsplit(url).hostname,
        }
    except Exception as exc:
        result = {
            "name": name, "status": "failed", "error": type(exc).__name__,
            "latency_ms": round((perf_counter() - started) * 1000, 3),
            "host": urlsplit(url).hostname,
        }
        if isinstance(exc, httpx.HTTPStatusError):
            result["status_code"] = exc.response.status_code
        return result


def run_external_smoke(settings, root: Path, client: httpx.Client | None = None) -> dict:
    owned = client is None
    client = client or httpx.Client(timeout=20, follow_redirects=True)
    checks = []
    if settings.llm_base_url and settings.llm_api_key and settings.llm_model:
        checks.append(probe_json_endpoint(
            client, "llm", "POST", settings.llm_base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json={
                "model": settings.llm_model, "temperature": 0, "max_tokens": 8,
                "messages": [{"role": "user", "content": "Return JSON: {\"ok\":true}"}],
            },
        ))
    else:
        checks.append({"name": "llm", "status": "skipped", "reason": "not_configured"})
    if settings.embedding_base_url and settings.embedding_api_key and settings.embedding_model:
        checks.append(probe_json_endpoint(
            client, "embedding", "POST",
            settings.embedding_base_url.rstrip("/") + "/embeddings",
            headers={"Authorization": f"Bearer {settings.embedding_api_key}"},
            json={"model": settings.embedding_model, "input": ["truthful advertising"]},
        ))
    else:
        checks.append({"name": "embedding", "status": "skipped", "reason": "not_configured"})
    if settings.rapidocr_base_url:
        checks.append(probe_json_endpoint(
            client, "rapidocr", "GET", settings.rapidocr_base_url.rstrip("/") + "/health"
        ))
    else:
        checks.append({"name": "rapidocr", "status": "skipped", "reason": "not_configured"})
    registry = json.loads((root / "config/source_registry.json").read_text(encoding="utf-8"))
    for source in registry["sources"]:
        started = perf_counter()
        try:
            response = client.get(source["source_url"])
            ok = response.status_code < 400 and len(response.content) > 100
            access_blocked = response.status_code in {401, 403, 429}
            checks.append({
                "name": f"source:{source['id']}",
                "status": "ok" if ok else "blocked" if access_blocked else "failed",
                "status_code": response.status_code, "bytes": len(response.content),
                "latency_ms": round((perf_counter() - started) * 1000, 3),
                "host": urlsplit(source["source_url"]).hostname,
            })
        except Exception as exc:
            checks.append({
                "name": f"source:{source['id']}", "status": "failed",
                "error": type(exc).__name__, "host": urlsplit(source["source_url"]).hostname,
            })
    if owned:
        client.close()
    configured = [item for item in checks if item["status"] != "skipped"]
    return {
        "checks": checks,
        "ok": sum(item["status"] == "ok" for item in configured),
        "blocked": sum(item["status"] == "blocked" for item in configured),
        "failed": sum(item["status"] == "failed" for item in configured),
        "skipped": sum(item["status"] == "skipped" for item in checks),
        "strict_success": bool(configured) and not any(
            item["status"] == "failed" for item in configured
        ),
        "contains_credentials": False,
    }
