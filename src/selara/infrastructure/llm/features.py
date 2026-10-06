from enum import StrEnum


class AiFeature(StrEnum):
    LLM_ADMIN = "llm_admin"
    DAILY_SUMMARY = "daily_summary"
    LLM_CONTEXT_COMPRESSION = "llm_context_compression"
    AUTOCONFIG = "autoconfig"
    PERSONAL_CHAT = "personal_chat"
    # Internal operation of Personal AI: tracked for cost, never charged to the user's pool.
    PERSONAL_MEMORY_EXTRACT = "personal_memory_extract"
    # A pet talking in a group; always paid from its owner's Selara Personal, never from the chat.
    PET_TALK = "pet_talk"
    # Internal operation of pet dialogue memory: tracked for cost, never charged to any pool.
    PET_MEMORY_EXTRACT = "pet_memory_extract"
    # A spontaneous line a pet posts in its chat; same owner-paid pool as talking.
    PET_EVENT_TEXT = "pet_event_text"
