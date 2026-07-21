import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from policyguard.application.agent import AgentBudget, ControlledAgent, configured_agent_planner
from policyguard.application.agent_evaluation import evaluate_agent, evaluate_pipeline
from policyguard.application.tools import SuggestConservativeRewriteTool, ToolRegistry
from policyguard.config import get_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-agent", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()
    root = Path(__file__).parents[3]
    cases = json.loads(
        (root / "data/evaluation/agent-remediation-v1.json").read_text(encoding="utf-8")
    )["samples"]
    tool = SuggestConservativeRewriteTool()
    output = {
        "pipeline": evaluate_pipeline(
            cases, lambda product: tool.execute({"product": product}).output
        )
    }
    if args.run_agent:
        planner = configured_agent_planner(settings)
        if planner is None:
            raise SystemExit("SKIPPED: configure LLM settings before --run-agent")
        output["agent"] = evaluate_agent(
            cases,
            lambda: ControlledAgent(
                planner,
                ToolRegistry([SuggestConservativeRewriteTool()]),
                AgentBudget(max_steps=3, max_tool_calls=1),
            ),
        )
    target = root / "data/benchmarks/agent-vs-pipeline.json"
    if target.exists():
        previous = json.loads(target.read_text(encoding="utf-8"))
        history = previous.pop("history", [])
        history.append({"recorded_at": datetime.now(UTC).isoformat(), **previous})
        output["history"] = history[-20:]
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
