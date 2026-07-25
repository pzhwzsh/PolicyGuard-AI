from policyguard.scripts.resilience_drill import run_drill


def test_resilience_drill_proves_fallback_rollback_and_restore() -> None:
    report = run_drill()
    assert report["passed"] is True
    assert {item["name"] for item in report["checks"]} == {
        "provider_fallback", "automatic_canary_rollback", "verified_restore_dry_run"
    }
