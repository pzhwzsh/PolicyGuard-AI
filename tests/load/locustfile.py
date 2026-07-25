"""Read-heavy production smoke load; mutating workflows require separate test tenants."""

import os

from locust import HttpUser, between, events, task


class PolicyGuardUser(HttpUser):
    wait_time = between(0.2, 1.0)

    def on_start(self) -> None:
        self.headers = {}
        tenant = os.getenv("LOAD_TEST_TENANT", "")
        key = os.getenv("LOAD_TEST_TENANT_KEY", "")
        if tenant and key:
            self.headers = {"X-Tenant-ID": tenant, "X-Tenant-Key": key}

    @task(5)
    def health(self) -> None:
        self.client.get("/health", name="GET /health")

    @task(2)
    def readiness(self) -> None:
        self.client.get("/api/v1/readiness", headers=self.headers, name="GET /api/v1/readiness")

    @task(1)
    def harness_skills(self) -> None:
        self.client.get(
            "/api/v1/harness/skills",
            headers=self.headers,
            name="GET /api/v1/harness/skills",
        )


@events.quitting.add_listener
def enforce_slo(environment, **_kwargs) -> None:
    stats = environment.stats.total
    if stats.fail_ratio > 0.01 or stats.get_response_time_percentile(0.95) > 2000:
        environment.process_exit_code = 1
