"""Structured, cached query rewriting for cross-language legal retrieval."""

import json
import re
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path

import httpx

from policyguard.application.hybrid import reciprocal_rank_fusion
from policyguard.domain.models import KnowledgeFilter, SearchHit

DRIFT_CONSTRAINTS = (
    "advertiser",
    "publisher",
    "endorser",
    "platform",
    "广告主",
    "广告经营者",
    "广告发布者",
    "代言人",
    "平台",
    "fine",
    "penalty",
    "liability",
    "罚款",
    "处罚",
    "责任",
    "approval",
    "license",
    "审批",
    "许可",
)


def query_language(query: str) -> str:
    cjk_count = sum("\u4e00" <= char <= "\u9fff" for char in query)
    latin_count = sum(char.isascii() and char.isalpha() for char in query)
    if cjk_count > latin_count / 2:
        return "zh"
    if latin_count:
        return "en"
    return "unknown"


def jurisdiction_source_language(jurisdiction: str) -> str:
    return "zh" if jurisdiction.upper() == "CN" else "en"


def is_cross_language_query(query: str, jurisdiction: str) -> bool:
    detected = query_language(query)
    return detected != "unknown" and detected != jurisdiction_source_language(jurisdiction)


def should_rewrite(query: str, jurisdiction: str, top_score: float | None = None) -> bool:
    language_mismatch = is_cross_language_query(query, jurisdiction)
    weak_retrieval = top_score is not None and top_score < 0.45
    return language_mismatch or weak_retrieval


@dataclass(frozen=True, slots=True)
class RewrittenQuery:
    query_id: str
    original_query: str
    canonical_query: str
    alternatives: tuple[str, ...]
    legal_terms: tuple[str, ...]
    jurisdiction: str

    def retrieval_queries(self) -> list[str]:
        values = [self.original_query, self.canonical_query, *self.alternatives]
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))[:4]

    def added_constraints(self) -> list[str]:
        """Return high-risk constraints introduced by the rewrite but absent in the question."""
        original = self.original_query.casefold()
        rewritten = " ".join(
            [self.canonical_query, *self.alternatives, *self.legal_terms]
        ).casefold()
        added = [term for term in DRIFT_CONSTRAINTS if term in rewritten and term not in original]
        original_numbers = set(re.findall(r"\b\d+(?:\.\d+)?%?\b", original))
        rewritten_numbers = set(re.findall(r"\b\d+(?:\.\d+)?%?\b", rewritten))
        added.extend(sorted(rewritten_numbers - original_numbers))
        return list(dict.fromkeys(added))


class JsonQueryRewriteCache:
    def __init__(self, path: Path) -> None:
        self.path = path

    def key(self, model: str, jurisdiction: str, query: str) -> str:
        return sha256(f"{model}|{jurisdiction}|{query}".encode()).hexdigest()

    def load(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)


@dataclass(frozen=True, slots=True)
class OpenAICompatibleQueryRewriter:
    base_url: str
    api_key: str
    model: str
    reasoning_effort: str = "medium"
    timeout_seconds: float = 120

    def rewrite_batch(self, items: list[dict]) -> tuple[list[RewrittenQuery], dict]:
        response = httpx.post(
            self.base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "temperature": 0,
                "reasoning_effort": self.reasoning_effort,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Rewrite user questions for retrieval from official advertising law. "
                            "Do not answer. Preserve numbers, entities, product type, and intent. "
                            "Never introduce legal actors, liabilities, sanctions, dates, approval "
                            "requirements, or product categories absent from the original query. "
                            "For CN, produce concise Simplified Chinese legal terminology; "
                            "for US/EU, "
                            "use concise English legal terminology. Return JSON only: "
                            '{"rewrites":[{"query_id":string,"canonical_query":string,'
                            '"alternatives":[string],"legal_terms":[string]}]}. '
                            "At most two alternatives and six legal terms per item."
                        ),
                    },
                    {"role": "user", "content": json.dumps(items, ensure_ascii=False)},
                ],
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        return self.parse(payload["choices"][0]["message"].get("content", ""), items), payload.get(
            "usage", {}
        )

    @staticmethod
    def parse(content: str, items: list[dict]) -> list[RewrittenQuery]:
        payload = json.loads(content.strip())
        raw = payload.get("rewrites")
        if not isinstance(raw, list):
            raise ValueError("query_rewrites_must_be_a_list")
        source = {item["query_id"]: item for item in items}
        rewrites = []
        for item in raw:
            query_id = str(item.get("query_id", ""))
            if query_id not in source:
                raise ValueError("query_rewrite_unknown_id")
            canonical = str(item.get("canonical_query", "")).strip()
            if not canonical or len(canonical) > 500:
                raise ValueError("query_rewrite_invalid_canonical_query")
            original = source[query_id]
            alternatives = tuple(
                str(value).strip()[:500]
                for value in item.get("alternatives", [])[:2]
                if str(value).strip()
            )
            terms = tuple(
                str(value).strip()[:100]
                for value in item.get("legal_terms", [])[:6]
                if str(value).strip()
            )
            rewrites.append(
                RewrittenQuery(
                    query_id=query_id,
                    original_query=original["query"],
                    canonical_query=canonical,
                    alternatives=alternatives,
                    legal_terms=terms,
                    jurisdiction=original["jurisdiction"],
                )
            )
        return rewrites


def cached_rewrite_batch(
    rewriter: OpenAICompatibleQueryRewriter,
    cache: JsonQueryRewriteCache,
    items: list[dict],
) -> tuple[list[RewrittenQuery], dict]:
    data = cache.load()
    found = []
    missing = []
    for item in items:
        key = cache.key(rewriter.model, item["jurisdiction"], item["query"])
        cached = data.get(key)
        if cached:
            found.append(
                RewrittenQuery(
                    **{
                        **cached,
                        "alternatives": tuple(cached["alternatives"]),
                        "legal_terms": tuple(cached["legal_terms"]),
                    }
                )
            )
        else:
            missing.append(item)
    usage = {"cache_hits": len(found), "cache_misses": len(missing)}
    if missing:
        created, provider_usage = rewriter.rewrite_batch(missing)
        usage.update(provider_usage)
        for rewrite in created:
            original = next(item for item in missing if item["query_id"] == rewrite.query_id)
            key = cache.key(rewriter.model, original["jurisdiction"], original["query"])
            data[key] = asdict(rewrite)
        cache.save(data)
        found.extend(created)
    by_id = {item.query_id: item for item in found}
    return [by_id[item["query_id"]] for item in items], usage


def multi_query_search(
    retriever,
    rewrite: RewrittenQuery,
    top_k: int,
    scope: KnowledgeFilter,
) -> list[SearchHit]:
    result_sets = [
        retriever.search(query, top_k=top_k, scope=scope) for query in rewrite.retrieval_queries()
    ]
    return reciprocal_rank_fusion(result_sets, top_k=top_k, smoothing=60)


def configured_query_rewriter(settings) -> OpenAICompatibleQueryRewriter | None:
    if getattr(settings, "app_env", "") == "test" or not settings.query_rewrite_enabled:
        return None
    if not (settings.llm_base_url and settings.llm_api_key and settings.llm_model):
        return None
    return OpenAICompatibleQueryRewriter(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        reasoning_effort=settings.llm_reasoning_effort,
        timeout_seconds=settings.llm_timeout_seconds,
    )
