"""Deterministic context assembly, compression, memory tiers, and cache accounting."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any


def estimate_tokens(value: Any) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    non_cjk = max(len(text) - cjk, 0)
    return max(1, cjk + (non_cjk + 3) // 4)


@dataclass(frozen=True, slots=True)
class ContextItem:
    item_id: str
    kind: str
    content: Any
    priority: int = 50
    pinned: bool = False
    source: str = "runtime"


@dataclass(frozen=True, slots=True)
class ContextBudget:
    max_tokens: int = 12_000
    reserved_output_tokens: int = 2_000
    max_tool_result_tokens: int = 2_500

    @property
    def input_limit(self) -> int:
        return max(self.max_tokens - self.reserved_output_tokens, 1)


def _compress_content(content: Any, target_tokens: int) -> dict[str, Any]:
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    target_chars = max(target_tokens * 4, 120)
    if len(text) <= target_chars:
        return {"text": text, "compressed": False}
    head = text[: int(target_chars * 0.68)]
    tail = text[-int(target_chars * 0.22) :]
    return {
        "text": f"{head}\n…[deterministic compression]…\n{tail}",
        "compressed": True,
        "original_sha256": sha256(text.encode("utf-8")).hexdigest(),
        "original_chars": len(text),
    }


class ContextManager:
    def __init__(self, budget: ContextBudget) -> None:
        self.budget = budget

    def assemble(self, items: list[ContextItem]) -> dict[str, Any]:
        ranked = sorted(items, key=lambda item: (not item.pinned, -item.priority, item.item_id))
        selected: list[dict[str, Any]] = []
        used = 0
        original = sum(estimate_tokens(item.content) for item in items)
        compressed_count = 0
        dropped: list[str] = []
        by_kind: dict[str, int] = {}
        for item in ranked:
            item_tokens = estimate_tokens(item.content)
            remaining = self.budget.input_limit - used
            if remaining <= 0:
                if item.pinned:
                    raise ValueError("context_pinned_items_exceed_budget")
                dropped.append(item.item_id)
                continue
            content = item.content
            compressed = False
            if item.kind == "tool_result" and item_tokens > self.budget.max_tool_result_tokens:
                result = _compress_content(content, self.budget.max_tool_result_tokens)
                content, compressed = result, True
                item_tokens = estimate_tokens(content)
            if item_tokens > remaining:
                if item.pinned and remaining < 30:
                    raise ValueError("context_pinned_items_exceed_budget")
                if remaining >= 30:
                    result = _compress_content(content, remaining)
                    content, compressed = result, True
                    item_tokens = min(estimate_tokens(content), remaining)
                else:
                    dropped.append(item.item_id)
                    continue
            selected.append({
                **asdict(item),
                "content": content,
                "tokens": item_tokens,
                "compressed": compressed,
            })
            used += item_tokens
            compressed_count += int(compressed)
            by_kind[item.kind] = by_kind.get(item.kind, 0) + item_tokens
        return {
            "items": selected,
            "metrics": {
                "input_limit": self.budget.input_limit,
                "used_tokens": used,
                "reserved_output_tokens": self.budget.reserved_output_tokens,
                "original_tokens": original,
                "saved_tokens": max(original - used, 0),
                "compressed_items": compressed_count,
                "dropped_items": dropped,
                "tokens_by_kind": by_kind,
            },
        }


class JsonMemoryStore:
    TIERS = {"working", "episodic", "long_term"}

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def put(self, tier: str, key: str, value: Any, *, reviewed: bool = False) -> None:
        if tier not in self.TIERS:
            raise ValueError("memory_tier_invalid")
        if tier == "long_term" and not reviewed:
            raise ValueError("long_term_memory_requires_review")
        state = self._load()
        state.setdefault(tier, {})[key] = {
            "value": value,
            "reviewed": reviewed,
            "content_sha256": sha256(
                json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
        }
        self._save(state)

    def items(self, tier: str) -> dict[str, Any]:
        if tier not in self.TIERS:
            raise ValueError("memory_tier_invalid")
        return self._load().get(tier, {})

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {tier: {} for tier in self.TIERS}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, value: dict[str, Any]) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)


class JsonContextCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.hits = 0
        self.misses = 0

    def get_or_put(self, value: Any) -> tuple[str, bool]:
        digest = sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {}
        hit = digest in state
        if hit:
            self.hits += 1
        else:
            self.misses += 1
            state[digest] = {"token_estimate": estimate_tokens(value)}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
            temporary.replace(self.path)
        return digest, hit

    def metrics(self) -> dict[str, Any]:
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 4) if total else 0.0,
        }
