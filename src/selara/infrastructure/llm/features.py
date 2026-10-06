from enum import StrEnum


class AiFeature(StrEnum):
    LLM_ADMIN = "llm_admin"
    DAILY_SUMMARY = "daily_summary"
    LLM_CONTEXT_COMPRESSION = "llm_context_compression"
    AUTOCONFIG = "autoconfig"
    PERSONAL_CHAT = "personal_chat"
    # Internal operation of Personal AI: tracked for cost, never charged to the user's pool.
    PERSONAL_MEMORY_EXTRACT = "personal_memory_extract"
