from aiogram.types import Message


def is_trackable_message(message: Message, supported_chat_types: set[str]) -> bool:
    return bool(
        message.from_user
        and not message.from_user.is_bot
        and message.chat
        and message.chat.type in supported_chat_types
    )
