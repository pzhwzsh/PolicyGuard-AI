from dataclasses import dataclass
from functools import lru_cache
from os import getenv

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str
    app_env: str
    app_host: str
    app_port: int
    log_level: str
    database_url: str
    source_dir: str
    evaluation_dataset: str
    embedding_base_url: str
    embedding_api_key: str
    embedding_model: str
    embedding_fallback_model: str
    embedding_provider: str
    embedding_timeout_seconds: float
    rerank_base_url: str
    rerank_api_key: str
    rerank_model: str
    rerank_fallback_model: str
    rerank_timeout_seconds: float
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_reasoning_effort: str
    llm_timeout_seconds: float
    llm_fallback_model: str
    provider_max_attempts: int
    provider_backoff_seconds: float
    workflow_model_call_budget: int
    workflow_input_token_budget: int
    llm_input_price_per_million: float
    llm_fallback_input_price_per_million: float
    query_rewrite_enabled: bool
    query_rewrite_cache: str
    upload_dir: str
    mineru_base_url: str
    paddleocr_base_url: str
    rapidocr_base_url: str
    ppstructure_base_url: str
    document_parser_api_key: str
    admin_api_key: str
    reviewer_api_key: str
    tenant_keys_json: str
    image_generation_base_url: str
    image_generation_api_key: str
    image_generation_model: str
    image_generation_timeout_seconds: float
    rate_limit_per_minute: int
    max_upload_bytes: int
    max_active_jobs_per_tenant: int
    oidc_issuer_url: str
    oidc_audience: str
    oidc_roles_claim: str
    oidc_tenant_claim: str
    metrics_api_key: str
    otel_exporter_otlp_endpoint: str
    harness_sandbox_enabled: bool
    harness_sandbox_image: str
    mcp_servers_json: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_authorization_code: str
    smtp_from_email: str
    auth_code_pepper: str
    session_cookie_secure: bool


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv()
    return Settings(
        app_name=getenv("APP_NAME", "PolicyGuard AI"),
        app_env=getenv("APP_ENV", "development"),
        app_host=getenv("APP_HOST", "127.0.0.1"),
        app_port=int(getenv("APP_PORT", "8000")),
        log_level=getenv("LOG_LEVEL", "INFO"),
        database_url=getenv("DATABASE_URL", "sqlite:///./data/policyguard.db"),
        source_dir=getenv("SOURCE_DIR", "./data/sources"),
        evaluation_dataset=getenv("EVALUATION_DATASET", "./data/evaluation/rag-baseline.json"),
        embedding_base_url=getenv("EMBEDDING_BASE_URL", ""),
        embedding_api_key=getenv("EMBEDDING_API_KEY", ""),
        embedding_model=getenv("EMBEDDING_MODEL", ""),
        embedding_fallback_model=getenv("EMBEDDING_FALLBACK_MODEL", ""),
        embedding_provider=getenv("EMBEDDING_PROVIDER", "openai_compatible"),
        embedding_timeout_seconds=float(getenv("EMBEDDING_TIMEOUT_SECONDS", "30")),
        rerank_base_url=getenv("RERANK_BASE_URL", ""),
        rerank_api_key=getenv("RERANK_API_KEY", ""),
        rerank_model=getenv("RERANK_MODEL", ""),
        rerank_fallback_model=getenv("RERANK_FALLBACK_MODEL", ""),
        rerank_timeout_seconds=float(getenv("RERANK_TIMEOUT_SECONDS", "30")),
        llm_base_url=getenv("LLM_BASE_URL", ""),
        llm_api_key=getenv("LLM_API_KEY", ""),
        llm_model=getenv("LLM_MODEL", ""),
        llm_reasoning_effort=getenv("LLM_REASONING_EFFORT", "medium"),
        llm_timeout_seconds=float(getenv("LLM_TIMEOUT_SECONDS", "45")),
        llm_fallback_model=getenv("LLM_FALLBACK_MODEL", ""),
        provider_max_attempts=int(getenv("PROVIDER_MAX_ATTEMPTS", "3")),
        provider_backoff_seconds=float(getenv("PROVIDER_BACKOFF_SECONDS", "0.5")),
        workflow_model_call_budget=int(getenv("WORKFLOW_MODEL_CALL_BUDGET", "3")),
        workflow_input_token_budget=int(getenv("WORKFLOW_INPUT_TOKEN_BUDGET", "12000")),
        llm_input_price_per_million=float(getenv("LLM_INPUT_PRICE_PER_MILLION", "0")),
        llm_fallback_input_price_per_million=float(
            getenv("LLM_FALLBACK_INPUT_PRICE_PER_MILLION", "0")
        ),
        query_rewrite_enabled=getenv("QUERY_REWRITE_ENABLED", "true").casefold() == "true",
        query_rewrite_cache=getenv("QUERY_REWRITE_CACHE", "./data/query-cache/rewrites.json"),
        upload_dir=getenv("UPLOAD_DIR", "./data/uploads"),
        mineru_base_url=getenv("MINERU_BASE_URL", ""),
        paddleocr_base_url=getenv("PADDLEOCR_BASE_URL", ""),
        rapidocr_base_url=getenv("RAPIDOCR_BASE_URL", ""),
        ppstructure_base_url=getenv("PPSTRUCTURE_BASE_URL", ""),
        document_parser_api_key=getenv("DOCUMENT_PARSER_API_KEY", ""),
        admin_api_key=getenv("ADMIN_API_KEY", ""),
        reviewer_api_key=getenv("REVIEWER_API_KEY", ""),
        tenant_keys_json=getenv("TENANT_KEYS_JSON", ""),
        image_generation_base_url=getenv("IMAGE_GENERATION_BASE_URL", ""),
        image_generation_api_key=getenv("IMAGE_GENERATION_API_KEY", ""),
        image_generation_model=getenv("IMAGE_GENERATION_MODEL", ""),
        image_generation_timeout_seconds=float(getenv("IMAGE_GENERATION_TIMEOUT_SECONDS", "180")),
        rate_limit_per_minute=int(getenv("RATE_LIMIT_PER_MINUTE", "120")),
        max_upload_bytes=int(getenv("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024))),
        max_active_jobs_per_tenant=int(getenv("MAX_ACTIVE_JOBS_PER_TENANT", "10")),
        oidc_issuer_url=getenv("OIDC_ISSUER_URL", ""),
        oidc_audience=getenv("OIDC_AUDIENCE", ""),
        oidc_roles_claim=getenv("OIDC_ROLES_CLAIM", "roles"),
        oidc_tenant_claim=getenv("OIDC_TENANT_CLAIM", "tenant_id"),
        metrics_api_key=getenv("METRICS_API_KEY", ""),
        otel_exporter_otlp_endpoint=getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
        harness_sandbox_enabled=getenv("HARNESS_SANDBOX_ENABLED", "false").casefold() == "true",
        harness_sandbox_image=getenv("HARNESS_SANDBOX_IMAGE", "python:3.12-alpine"),
        mcp_servers_json=getenv("MCP_SERVERS_JSON", "[]"),
        smtp_host=getenv("SMTP_HOST", "smtp.qq.com"),
        smtp_port=int(getenv("SMTP_PORT", "465")),
        smtp_username=getenv("SMTP_USERNAME", ""),
        smtp_authorization_code=getenv("SMTP_AUTHORIZATION_CODE", ""),
        smtp_from_email=getenv("SMTP_FROM_EMAIL", ""),
        auth_code_pepper=getenv("AUTH_CODE_PEPPER", "development-only-change-me"),
        session_cookie_secure=getenv("SESSION_COOKIE_SECURE", "false").casefold() == "true",
    )
