from datetime import date, datetime
from typing import Protocol

from selara.domain.entities import (
    ActivityStats,
    BotRole,
    ChatAuditLogEntry,
    ChatActivitySummary,
    ChatTrigger,
    CustomSocialAction,
    ChatTextAlias,
    ChatTextAliasUpsertResult,
    ChatSnapshot,
    FamilyBundle,
    FamilyGraph,
    GraphRelationType,
    GraphRelationship,
    InlinePrivateMessage,
    PairState,
    RelationshipActionCode,
    RelationshipKind,
    RelationshipState,
    MarriageState,
    ModerationAction,
    ModerationResult,
    ModerationState,
    LeaderboardItem,
    LeaderboardMode,
    LeaderboardPeriod,
    RelationshipProposal,
    TextAliasMode,
    UserChatOverview,
    UserChatAward,
    UserChatProfile,
    UserSnapshot,
)


class ActivityRepository(Protocol):
    async def upsert_activity(
        self,
        *,
        chat: ChatSnapshot,
        user: UserSnapshot,
        event_at: datetime,
    ) -> ActivityStats: ...

    async def get_user_stats(self, *, chat_id: int, user_id: int) -> ActivityStats | None: ...
    async def get_user_activity_daily_series(self, *, chat_id: int, user_id: int, days: int) -> list[tuple[date, int]]: ...
    async def get_chat_activity_summary(self, *, chat_id: int) -> ChatActivitySummary: ...

    async def get_top(self, *, chat_id: int, limit: int) -> list[ActivityStats]: ...

    async def get_last_seen(self, *, chat_id: int, user_id: int) -> datetime | None: ...

    async def get_chat_settings(self, *, chat_id: int): ...

    async def upsert_chat_settings(self, *, chat: ChatSnapshot, values: dict[str, object]): ...
    async def get_chat_alias_mode(self, *, chat_id: int) -> TextAliasMode: ...
    async def set_chat_alias_mode(self, *, chat: ChatSnapshot, mode: TextAliasMode) -> TextAliasMode: ...
    async def list_chat_aliases(self, *, chat_id: int) -> list[ChatTextAlias]: ...
    async def upsert_chat_alias(
        self,
        *,
        chat: ChatSnapshot,
        command_key: str,
        source_trigger_norm: str,
        alias_text_norm: str,
        actor_user_id: int | None,
        force: bool,
    ) -> ChatTextAliasUpsertResult: ...
    async def remove_chat_alias(self, *, chat_id: int, alias_text_norm: str) -> bool: ...

    async def record_vote(
        self,
        *,
        chat: ChatSnapshot,
        voter: UserSnapshot,
        target: UserSnapshot,
        vote_value: int,
        event_at: datetime,
    ) -> None: ...

    async def count_votes_by_voter_since(self, *, chat_id: int, voter_user_id: int, since: datetime) -> int: ...

    async def get_karma_value(
        self,
        *,
        chat_id: int,
        user_id: int,
        period: LeaderboardPeriod,
        since: datetime | None = None,
    ) -> int: ...

    async def get_representation_stats(
        self,
        *,
        chat_id: int,
        user_id: int,
        since: datetime | None = None,
    ) -> tuple[int, int, datetime | None]: ...

    async def get_leaderboard(
        self,
        *,
        chat_id: int,
        mode: LeaderboardMode,
        period: LeaderboardPeriod,
        since: datetime | None,
        limit: int,
        karma_weight: float,
        activity_weight: float,
    ) -> list[LeaderboardItem]: ...

    async def set_announcement_subscription(
        self,
        *,
        chat: ChatSnapshot,
        user: UserSnapshot,
        enabled: bool,
    ) -> None: ...

    async def get_announcement_recipients(self, *, chat_id: int) -> list[UserSnapshot]: ...
    async def set_chat_display_name(
        self,
        *,
        chat: ChatSnapshot,
        user: UserSnapshot,
        display_name: str | None,
    ) -> None: ...
    async def get_chat_display_name(self, *, chat_id: int, user_id: int) -> str | None: ...
    async def get_chat_title_prefix(self, *, chat_id: int, user_id: int) -> str | None: ...
    async def set_chat_title_prefix(
        self,
        *,
        chat: ChatSnapshot,
        user: UserSnapshot,
        title_prefix: str | None,
    ) -> str | None: ...
    async def get_user_chat_profile(self, *, chat_id: int, user_id: int) -> UserChatProfile | None: ...
    async def set_user_chat_profile_description(
        self,
        *,
        chat: ChatSnapshot,
        user: UserSnapshot,
        description: str | None,
    ) -> UserChatProfile | None: ...
    async def add_user_chat_award(
        self,
        *,
        chat: ChatSnapshot,
        target: UserSnapshot,
        title: str,
        granted_by_user_id: int | None,
        created_at: datetime,
    ) -> UserChatAward: ...
    async def list_user_chat_awards(self, *, chat_id: int, user_id: int, limit: int = 10) -> list[UserChatAward]: ...
    async def remove_user_chat_award(self, *, chat_id: int, award_id: int) -> bool: ...
    async def get_active_pair(self, *, user_id: int, chat_id: int | None = None) -> PairState | None: ...
    async def get_active_marriage(self, *, user_id: int, chat_id: int | None = None) -> MarriageState | None: ...
    async def get_active_relationship(self, *, user_id: int, chat_id: int | None = None) -> RelationshipState | None: ...
    async def create_marriage_proposal(
        self,
        *,
        chat: ChatSnapshot,
        proposer: UserSnapshot,
        target: UserSnapshot,
        kind: RelationshipKind,
        expires_at: datetime | None,
        event_at: datetime,
    ) -> tuple[RelationshipProposal | None, str | None]: ...
    async def respond_marriage_proposal(
        self,
        *,
        proposal_id: int,
        actor_user_id: int,
        accept: bool,
        event_at: datetime,
    ) -> tuple[RelationshipProposal | None, MarriageState | None, str | None]: ...
    async def respond_relationship_proposal(
        self,
        *,
        proposal_id: int,
        actor_user_id: int,
        accept: bool,
        event_at: datetime,
    ) -> tuple[RelationshipProposal | None, RelationshipState | None, str | None]: ...
    async def touch_pair_affection(
        self,
        *,
        pair_id: int,
        actor_user_id: int,
        affection_delta: int,
        event_at: datetime,
    ) -> PairState | None: ...
    async def touch_marriage_affection(
        self,
        *,
        marriage_id: int,
        actor_user_id: int,
        affection_delta: int,
        event_at: datetime,
    ) -> MarriageState | None: ...
    async def touch_relationship_affection(
        self,
        *,
        relationship: RelationshipState,
        actor_user_id: int,
        affection_delta: int,
        event_at: datetime,
    ) -> RelationshipState | None: ...
    async def get_relationship_action_last_used_at(
        self,
        *,
        relationship: RelationshipState,
        actor_user_id: int,
        action_code: RelationshipActionCode,
    ) -> datetime | None: ...
    async def set_relationship_action_last_used_at(
        self,
        *,
        relationship: RelationshipState,
        actor_user_id: int,
        action_code: RelationshipActionCode,
        used_at: datetime,
    ) -> datetime: ...
    async def dissolve_pair(self, *, user_id: int, chat_id: int | None = None) -> PairState | None: ...
    async def dissolve_marriage(self, *, user_id: int, chat_id: int | None = None) -> MarriageState | None: ...

    async def bootstrap_chat_owner_role(self, *, chat: ChatSnapshot, user: UserSnapshot) -> tuple[BotRole | None, bool]: ...

    async def get_bot_role(self, *, chat_id: int, user_id: int) -> BotRole | None: ...

    async def set_bot_role(
        self,
        *,
        chat: ChatSnapshot,
        target: UserSnapshot,
        role: BotRole,
        assigned_by_user_id: int | None,
    ) -> None: ...

    async def remove_bot_role(self, *, chat_id: int, user_id: int) -> bool: ...

    async def list_bot_roles(self, *, chat_id: int) -> list[tuple[UserSnapshot, BotRole]]: ...
    async def list_user_admin_chats(self, *, user_id: int) -> list[UserChatOverview]: ...
    async def list_user_manageable_game_chats(self, *, user_id: int) -> list[UserChatOverview]: ...
    async def list_user_activity_chats(self, *, user_id: int, limit: int = 50) -> list[UserChatOverview]: ...

    async def find_chat_user_by_username(self, *, chat_id: int, username: str) -> UserSnapshot | None: ...
    async def find_shared_group_user_by_username(self, *, sender_user_id: int, username: str) -> UserSnapshot | None: ...

    async def create_inline_private_message(
        self,
        *,
        id: str,
        chat_id: int | None,
        chat_instance: str | None,
        sender_id: int,
        receiver_ids: list[int],
        receiver_usernames: list[str],
        text: str,
        created_at: datetime,
    ) -> InlinePrivateMessage: ...

    async def get_inline_private_message(self, *, id: str) -> InlinePrivateMessage | None: ...

    async def set_inline_private_message_context(
        self,
        *,
        id: str,
        chat_id: int | None,
        chat_instance: str | None,
    ) -> bool: ...
    async def list_recent_inline_private_receivers(self, *, sender_user_id: int, limit: int = 10) -> list[UserSnapshot]: ...
    async def list_recent_inline_private_receiver_usernames(self, *, sender_user_id: int, limit: int = 10) -> list[str]: ...

    async def get_user_snapshot(self, *, user_id: int) -> UserSnapshot | None: ...
    async def list_chat_triggers(self, *, chat_id: int) -> list[ChatTrigger]: ...
    async def get_chat_trigger(self, *, trigger_id: int) -> ChatTrigger | None: ...
    async def upsert_chat_trigger(
        self,
        *,
        chat: ChatSnapshot,
        trigger_id: int | None,
        keyword: str,
        match_type: str,
        response_text: str | None,
        media_file_id: str | None,
        media_type: str | None,
        actor_user_id: int | None,
    ) -> ChatTrigger: ...
    async def remove_chat_trigger(self, *, chat_id: int, trigger_id: int) -> bool: ...
    async def list_custom_social_actions(self, *, chat_id: int) -> list[CustomSocialAction]: ...
    async def get_custom_social_action(self, *, chat_id: int, trigger_text_norm: str) -> CustomSocialAction | None: ...
    async def upsert_custom_social_action(
        self,
        *,
        chat: ChatSnapshot,
        trigger_text: str,
        response_template: str,
        actor_user_id: int | None,
    ) -> CustomSocialAction: ...
    async def remove_custom_social_action(self, *, chat_id: int, trigger_text_norm: str) -> bool: ...
    async def upsert_graph_relationship(
        self,
        *,
        chat: ChatSnapshot,
        user_a: UserSnapshot,
        user_b: UserSnapshot,
        relation_type: GraphRelationType,
        actor_user_id: int | None,
    ) -> GraphRelationship: ...
    async def validate_parent_link(
        self,
        *,
        chat_id: int,
        actor_user_id: int,
        target_user_id: int,
    ) -> str | None: ...
    async def remove_graph_relationship(
        self,
        *,
        chat_id: int,
        user_a: int,
        user_b: int,
        relation_type: GraphRelationType,
    ) -> bool: ...
    async def list_graph_relationships(self, *, chat_id: int, user_id: int | None = None) -> list[GraphRelationship]: ...
    async def list_family_bundle(self, *, chat_id: int, user_id: int) -> FamilyBundle: ...
    async def list_family_graph(self, *, chat_id: int, user_id: int) -> FamilyGraph: ...
    async def add_audit_log(
        self,
        *,
        chat: ChatSnapshot,
        action_code: str,
        description: str,
        actor_user_id: int | None = None,
        target_user_id: int | None = None,
        meta_json: dict | None = None,
    ) -> ChatAuditLogEntry: ...
    async def list_audit_logs(self, *, chat_id: int, limit: int = 100) -> list[ChatAuditLogEntry]: ...

    async def apply_moderation_action(
        self,
        *,
        chat: ChatSnapshot,
        actor: UserSnapshot,
        target: UserSnapshot,
        action: ModerationAction,
        reason: str | None = None,
        amount: int = 1,
    ) -> ModerationResult: ...

    async def get_moderation_state(self, *, chat_id: int, user_id: int) -> ModerationState | None: ...
