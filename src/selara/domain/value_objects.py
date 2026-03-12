from selara.domain.entities import ActivityStats


def display_name(stats: ActivityStats) -> str:
    return display_name_from_parts(
        user_id=stats.user_id,
        username=stats.username,
        first_name=stats.first_name,
        last_name=stats.last_name,
        chat_display_name=stats.chat_display_name,
    )


def display_name_from_parts(
    *,
    user_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
    chat_display_name: str | None = None,
) -> str:
    alias = (chat_display_name or "").strip()
    # "user:<id>" is a technical fallback, not a real display name override.
    if alias and alias != f"user:{user_id}":
        return alias

    full_name = " ".join(filter(None, [first_name, last_name])).strip()
    if full_name:
        return full_name

    if username:
        return f"@{username}"

    return f"user:{user_id}"
