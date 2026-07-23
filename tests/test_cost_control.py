from policyguard.application.cost_control import WorkflowModelBudget


def test_workflow_model_budget_rejects_excess_calls_and_tokens() -> None:
    budget = WorkflowModelBudget(max_calls=1, max_estimated_input_tokens=20)
    assert budget.reserve("short input") is True
    assert budget.reserve("second call") is False

    tight = WorkflowModelBudget(max_calls=3, max_estimated_input_tokens=2)
    assert tight.reserve("这是一个明显超过预算的输入") is False
    assert tight.snapshot()["calls"] == 0

