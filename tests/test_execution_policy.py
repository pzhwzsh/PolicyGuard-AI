import pytest

from policyguard.application.execution_policy import choose_remediation_mode


def test_pipeline_is_default_safe_path() -> None:
    decision = choose_remediation_mode("pipeline", experimental_opt_in=False)
    assert decision.mode == "pipeline"
    assert decision.experimental is False


def test_agent_requires_explicit_experimental_opt_in() -> None:
    with pytest.raises(ValueError, match="agent_requires_explicit_experimental_opt_in"):
        choose_remediation_mode("agent", experimental_opt_in=False)
    assert choose_remediation_mode("agent", experimental_opt_in=True).experimental is True
