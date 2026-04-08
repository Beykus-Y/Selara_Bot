from aiogram import F, Router
from aiogram.types import Message

router = Router(name="message-archive")


@router.edited_message(F.chat.type.in_(("group", "supergroup")))
async def edited_message_archive_passthrough(_message: Message) -> None:
    return
