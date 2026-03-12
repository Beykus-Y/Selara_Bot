from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal


@dataclass(frozen=True)
class UserSnapshot:
    telegram_user_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    is_bot: bool
    chat_display_name: str | None = None


@dataclass(frozen=True)
class ChatSnapshot:
    telegram_chat_id: int
    chat_type: str
    title: str | None


@dataclass(frozen=True)
class ActivityStats:
    chat_id: int
    user_id: int
    message_count: int
    last_seen_at: datetime
    first_seen_at: datetime | None = None
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    chat_display_name: str | None = None


@dataclass(frozen=True)
class ChatActivitySummary:
    chat_id: int
    participants_count: int
    total_messages: int
    last_activity_at: datetime | None


LeaderboardMode = Literal["mix", "activity", "karma"]
LeaderboardPeriod = Literal["all", "7d", "hour", "day", "week", "month"]
TextAliasMode = Literal["aliases_if_exists", "both", "standard_only"]


@dataclass(frozen=True)
class LeaderboardItem:
    user_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    activity_value: int
    karma_value: int
    hybrid_score: float
    last_seen_at: datetime | None
    chat_display_name: str | None = None


@dataclass(frozen=True)
class VoteResult:
    accepted: bool
    reason: str | None
    target_karma_all_time: int | None
    target_karma_7d: int | None


BotRole = str
ModerationAction = Literal["pred", "warn", "unwarn", "ban", "unban"]
RelationshipProposalStatus = Literal["pending", "accepted", "rejected", "cancelled", "expired"]
RelationshipKind = Literal["pair", "marriage"]
RelationshipActionCode = Literal["care", "date", "gift", "support", "love", "flirt", "surprise", "vow"]


@dataclass(frozen=True)
class UserChatOverview:
    chat_id: int
    chat_type: str
    chat_title: str | None
    bot_role: BotRole | None
    message_count: int | None
    last_seen_at: datetime | None


@dataclass(frozen=True)
class UserChatProfile:
    chat_id: int
    user_id: int
    description: str | None
    avatar_frame_code: str | None = None
    emoji_status_code: str | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class UserChatAward:
    id: int
    chat_id: int
    user_id: int
    title: str
    granted_by_user_id: int | None
    created_at: datetime


@dataclass(frozen=True)
class IrisImportState:
    chat_id: int
    user_id: int
    imported_at: datetime
    imported_by_user_id: int | None
    source_bot_username: str
    source_target_username: str
    karma_base_all_time: int


@dataclass(frozen=True)
class ChatRoleAssignment:
    chat_id: int
    user_id: int
    role: BotRole
    assigned_by_user_id: int | None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class ChatRoleDefinition:
    chat_id: int
    role_code: str
    title_ru: str
    rank: int
    permissions: tuple[str, ...]
    is_system: bool
    template_key: str | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class ChatCommandAccessRule:
    chat_id: int
    command_key: str
    min_role_code: str
    updated_by_user_id: int | None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class ModerationState:
    chat_id: int
    user_id: int
    pending_preds: int
    warn_count: int
    total_preds: int
    total_warns: int
    total_bans: int
    is_banned: bool
    last_reason: str | None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class ModerationResult:
    state: ModerationState
    action: ModerationAction
    auto_warns_added: int
    auto_ban_triggered: bool


@dataclass(frozen=True)
class RelationshipProposal:
    id: int
    kind: RelationshipKind
    proposer_user_id: int
    target_user_id: int
    user_low_id: int
    user_high_id: int
    status: RelationshipProposalStatus
    chat_id: int | None
    created_at: datetime
    expires_at: datetime | None
    responded_at: datetime | None = None


@dataclass(frozen=True)
class MarriageState:
    id: int
    user_low_id: int
    user_high_id: int
    chat_id: int | None
    married_at: datetime
    affection_points: int
    last_affection_at: datetime | None
    last_affection_by_user_id: int | None


@dataclass(frozen=True)
class PairState:
    id: int
    user_low_id: int
    user_high_id: int
    chat_id: int | None
    paired_at: datetime
    affection_points: int
    last_affection_at: datetime | None
    last_affection_by_user_id: int | None


@dataclass(frozen=True)
class RelationshipState:
    kind: RelationshipKind
    id: int
    user_low_id: int
    user_high_id: int
    chat_id: int | None
    started_at: datetime
    affection_points: int
    last_affection_at: datetime | None
    last_affection_by_user_id: int | None


@dataclass(frozen=True)
class ChatTextAlias:
    id: int
    chat_id: int
    command_key: str
    alias_text_norm: str
    source_trigger_norm: str
    created_by_user_id: int | None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class ChatTextAliasUpsertResult:
    alias: ChatTextAlias | None
    conflict_alias: ChatTextAlias | None
    created: bool
    reassigned: bool


@dataclass(frozen=True)
class InlinePrivateMessage:
    id: str
    chat_id: int | None
    chat_instance: str | None
    sender_id: int
    receiver_ids: tuple[int, ...]
    receiver_usernames: tuple[str, ...]
    text: str
    created_at: datetime


TriggerMatchType = Literal["exact", "contains", "starts_with"]
GraphRelationType = Literal["spouse", "parent", "child", "pet"]
AchievementScope = Literal["chat", "global"]
AchievementRarity = Literal["common", "uncommon", "rare", "epic", "legendary"]


@dataclass(frozen=True)
class AchievementDefinition:
    id: str
    scope: AchievementScope
    title: str
    description: str
    hidden: bool
    rarity: AchievementRarity
    icon: str
    sort_order: int
    enabled: bool
    condition_type: str
    condition_payload: dict[str, Any]
    tags: tuple[str, ...]


@dataclass(frozen=True)
class AchievementAward:
    achievement_id: str
    scope: AchievementScope
    awarded_at: datetime
    award_reason: str | None = None
    meta_json: dict[str, Any] | None = None


@dataclass(frozen=True)
class AchievementAwardResult:
    awarded: bool
    achievement_id: str
    scope: AchievementScope
    awarded_at: datetime | None
    holders_count: int
    holders_percent: float


@dataclass(frozen=True)
class AchievementView:
    achievement_id: str
    scope: AchievementScope
    title: str
    description: str
    icon: str
    rarity: AchievementRarity
    hidden: bool
    awarded: bool
    awarded_at: datetime | None
    holders_count: int
    holders_percent: float
    sort_order: int


@dataclass(frozen=True)
class ChatTrigger:
    id: int
    chat_id: int
    keyword: str
    keyword_norm: str
    match_type: TriggerMatchType
    response_text: str | None
    media_file_id: str | None
    media_type: str | None
    created_by_user_id: int | None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class CustomSocialAction:
    id: int
    chat_id: int
    trigger_text: str
    trigger_text_norm: str
    response_template: str
    created_by_user_id: int | None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class GraphRelationship:
    id: int
    chat_id: int
    user_a: int
    user_b: int
    relation_type: GraphRelationType
    created_by_user_id: int | None
    created_at: datetime
    updated_at: datetime | None = None


@dataclass(frozen=True)
class FamilyBundle:
    subject_user_id: int
    spouse_user_id: int | None
    parents: tuple[int, ...]
    grandparents: tuple[int, ...]
    step_parents: tuple[int, ...]
    siblings: tuple[int, ...]
    children: tuple[int, ...]
    pets: tuple[int, ...]


@dataclass(frozen=True)
class FamilyGraphEdge:
    source_user_id: int
    target_user_id: int
    relation_type: str
    label: str
    is_direct: bool = True


@dataclass(frozen=True)
class FamilyGraph:
    focus_user_id: int
    node_user_ids: tuple[int, ...]
    edges: tuple[FamilyGraphEdge, ...]


@dataclass(frozen=True)
class ChatAuditLogEntry:
    id: int
    chat_id: int
    actor_user_id: int | None
    target_user_id: int | None
    action_code: str
    description: str
    meta_json: dict | None
    created_at: datetime
