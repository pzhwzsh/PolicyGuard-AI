"""Declarative skill discovery without dynamic code import."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ALLOWED_PERMISSIONS = {
    "context:read", "memory:read", "memory:write", "policy:search",
    "sandbox:execute", "mcp:call",
}


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    name: str
    version: str
    description: str
    permissions: tuple[str, ...]
    plan: tuple[dict[str, Any], ...]
    root: Path


class SkillRegistry:
    def __init__(self, root: Path, available_tools: set[str]) -> None:
        self.root = root.resolve()
        self.available_tools = available_tools

    def discover(self) -> tuple[list[SkillDefinition], list[dict[str, str]]]:
        skills: list[SkillDefinition] = []
        failures: list[dict[str, str]] = []
        if not self.root.is_dir():
            return skills, failures
        for path in sorted(self.root.glob("*/manifest.json")):
            try:
                skills.append(self._load(path))
            except (ValueError, json.JSONDecodeError, KeyError) as exc:
                failures.append({"path": str(path), "error": str(exc)})
        names = [skill.name for skill in skills]
        if len(names) != len(set(names)):
            raise ValueError("skill_name_collision")
        return skills, failures

    def get(self, name: str) -> SkillDefinition:
        skills, _ = self.discover()
        for skill in skills:
            if skill.name == name:
                return skill
        raise LookupError("skill_not_found")

    def _load(self, path: Path) -> SkillDefinition:
        resolved = path.resolve()
        if self.root not in resolved.parents or resolved.is_symlink():
            raise ValueError("skill_path_invalid")
        payload = json.loads(path.read_text(encoding="utf-8"))
        name = payload["name"]
        version = payload["version"]
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", name):
            raise ValueError("skill_name_invalid")
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ValueError("skill_version_invalid")
        permissions = tuple(payload.get("permissions", []))
        if any(permission not in ALLOWED_PERMISSIONS for permission in permissions):
            raise ValueError("skill_permission_not_allowed")
        plan = tuple(payload["plan"])
        if not 1 <= len(plan) <= 24:
            raise ValueError("skill_plan_size_invalid")
        for step in plan:
            if step.get("type") == "tool" and step.get("tool") not in self.available_tools:
                raise ValueError("skill_tool_not_available")
            permission = step.get("permission")
            if permission and permission not in permissions:
                raise ValueError("skill_step_permission_undeclared")
        if not (path.parent / "SKILL.md").is_file():
            raise ValueError("skill_instructions_missing")
        return SkillDefinition(
            name=name,
            version=version,
            description=payload["description"],
            permissions=permissions,
            plan=plan,
            root=path.parent,
        )
