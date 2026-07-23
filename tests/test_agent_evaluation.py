from policyguard.application.agent import AgentBudget, ControlledAgent
from policyguard.application.agent_evaluation import evaluate_agent, evaluate_pipeline
from policyguard.application.tools import SuggestConservativeRewriteTool, ToolRegistry


class RewriteThenFinish:
    def next_action(self, context, tools, trace):
        if "last_observation" not in context:
            return {
                "type": "tool_call", "tool": "suggest_conservative_rewrite",
                "arguments": {"product": context["product"]}, "_usage": {"total_tokens": 5},
            }
        return {
            "type": "finish", "result": context["last_observation"]["output"],
            "_usage": {"total_tokens": 3},
        }


def test_agent_and_pipeline_use_the_same_success_contract() -> None:
    cases = [{
        "case_id": "risk", "product": {"title": "100%安全", "description": ""},
        "expected_removed": ["100%安全"],
    }]
    tool = SuggestConservativeRewriteTool()
    pipeline = evaluate_pipeline(cases, lambda product: tool.execute({"product": product}).output)
    agent = evaluate_agent(
        cases,
        lambda: ControlledAgent(
            RewriteThenFinish(), ToolRegistry([SuggestConservativeRewriteTool()]),
            AgentBudget(max_steps=3, max_tool_calls=1),
        ),
    )
    assert pipeline["metrics"]["success_rate"] == 1.0
    assert agent["metrics"]["success_rate"] == 1.0
    assert agent["metrics"]["total_tokens"] == 8


def test_retrieved_legal_basis_is_not_mislabeled_as_human_reviewed() -> None:
    result = SuggestConservativeRewriteTool().execute({
        "product": {"title": "100%安全", "description": ""},
        "evidence": [{"market": "CN", "candidate_evidence": [{
            "section_id": "article-4", "heading": "Article 4", "text": "Source text",
            "source_url": "https://example.test/article-4",
        }]}],
    })
    basis = result.output["operations"][0]["legal_basis"][0]
    assert basis["evidence_status"] == "retrieval_candidate_unverified"
    assert basis["requires_human_review"] is True
