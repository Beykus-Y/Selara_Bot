from aiogram.types import Message, User


def format_user_label(user: User | None, fallback_user_id: int | None = None) -> str:
    if user is not None:
        if user.username:
            return f"@{user.username}"

        full_name = " ".join(filter(None, [user.first_name, user.last_name])).strip()
        if full_name:
            return full_name

        return f"user:{user.id}"

    if fallback_user_id is not None:
        return f"user:{fallback_user_id}"

    return "пользователь"


def resolve_last_seen_target(message: Message) -> tuple[int | None, str]:
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        return target.id, format_user_label(target)

    if message.from_user:
        return message.from_user.id, format_user_label(message.from_user)

    return None, "пользователь"
