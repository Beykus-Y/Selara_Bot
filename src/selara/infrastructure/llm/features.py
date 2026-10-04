from enum import StrEnum


class AiFeature(StrEnum):
    LLM_ADMIN = "llm_admin"
    DAILY_SUMMARY = "daily_summary"
    LLM_CONTEXT_COMPRESSION = "llm_context_compression"
    AUTOCONFIG = "autoconfig"
