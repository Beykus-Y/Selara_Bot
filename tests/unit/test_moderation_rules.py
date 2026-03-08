from selara.presentation.handlers.moderation import _can_manage_target, _role_add_allowed


def test_owner_can_manage_any_target() -> None:
    assert _can_manage_target(actor_role="owner", target_role="owner")
    assert _can_manage_target(actor_role="owner", target_role="admin")
    assert _can_manage_target(actor_role="owner", target_role=None)


def test_admin_cannot_manage_admin_or_owner() -> None:
    assert not _can_manage_target(actor_role="admin", target_role="owner")
    assert not _can_manage_target(actor_role="admin", target_role="admin")
    assert _can_manage_target(actor_role="admin", target_role="moderator")


def test_admin_cannot_assign_owner_or_admin() -> None:
    assert not _role_add_allowed(actor_role="admin", target_current_role=None, target_new_role="owner")
    assert not _role_add_allowed(actor_role="admin", target_current_role=None, target_new_role="admin")
    assert _role_add_allowed(actor_role="admin", target_current_role=None, target_new_role="moderator")

