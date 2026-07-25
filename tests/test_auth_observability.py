from fastapi.testclient import TestClient

from policyguard.api.main import create_app
from policyguard.application.auth import Principal, bearer_token, principal_from_claims
from policyguard.infrastructure.observability import PrometheusRegistry


def test_oidc_claims_map_to_tenant_and_role_hierarchy() -> None:
    principal = principal_from_claims({
        "sub": "reviewer-1", "tenant_id": "tenant-a", "roles": ["reviewer"]
    })
    assert principal == Principal("reviewer-1", "tenant-a", frozenset({"reviewer"}))
    assert principal.permits("user")
    assert principal.permits("reviewer")
    assert not principal.permits("admin")
    assert bearer_token("Bearer signed-token") == "signed-token"


def test_prometheus_registry_uses_bounded_route_labels() -> None:
    registry = PrometheusRegistry()
    registry.begin()
    registry.finish("GET", "/api/v1/jobs/{job_id}", 200, 0.125)
    rendered = registry.render()
    assert 'route="/api/v1/jobs/{job_id}"' in rendered
    assert "policyguard_http_requests_total" in rendered
    assert "policyguard_http_requests_active 0" in rendered


def test_metrics_and_security_headers_are_exposed_in_local_mode(tmp_path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'observability.db'}")
    with TestClient(app) as client:
        health = client.get("/health")
        metrics = client.get("/metrics")
    assert health.headers["x-content-type-options"] == "nosniff"
    assert health.headers["x-frame-options"] == "DENY"
    assert health.headers["traceparent"].startswith("00-")
    assert metrics.status_code == 200
    assert "policyguard_http_requests_total" in metrics.text
