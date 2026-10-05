from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.exc import SQLAlchemyError

from selara.application.selara_ai_product import (
    SELARA_AI_PRODUCT_KEY,
    SelaraAiProductUnavailable,
    get_selara_ai_product,
)
from selara.core.config import Settings
from selara.infrastructure.db.telegram_stars import PaymentResult, SqlAlchemyTelegramStarsRepository
from selara.presentation.auth import is_telegram_chat_admin, resolve_owner_admin_exemption

logger = logging.getLogger(__name__)
router = Router(name="premium")

_CHECKOUT_ACCESS_ERROR = "Для оплаты нужно быть администратором выбранного чата, а Selara должна оставаться в нём."
_CHECKOUT_RETRY_ERROR = "Не удалось проверить чат. Попробуйте открыть /premium и повторить оплату позже."


def _product_for_settings(settings: Settings):
    if not settings.llm_enabled or not settings.llm_api_key.strip():
        raise SelaraAiProductUnavailable("AI provider is not enabled")
    return get_selara_ai_product(
        product_key=SELARA_AI_PRODUCT_KEY,
        price_stars=settings.selara_ai_price_stars,
    )


def _chat_label(title: str | None, chat_id: int | None = None) -> str:
    clean_title = (title or "").strip()
    if clean_title:
        return clean_title
    return "этого чата" if chat_id is not None else "выбранного чата"


def _format_date(value: datetime, timezone_name: str) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    return value.astimezone(zone).strftime("%d.%m.%Y %H:%M")


def _selection_keyboard(chats) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for chat in chats:
        label = (chat.title or "Без названия").strip()
        if len(label) > 55:
            label = f"{label[:52]}…"
        builder.button(text=label, callback_data=f"premium:select:{chat.telegram_chat_id}")
    builder.adjust(1)
    return builder.as_markup()


def _purchase_keyboard(*, chat_id: int, price_stars: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"Купить за {price_stars} ⭐",
        callback_data=f"premium:buy:{chat_id}",
    )
    builder.button(text="Отмена", callback_data="premium:cancel")
    builder.adjust(1)
    return builder.as_markup()


def _callback_chat_id(data: str | None, action: str) -> int | None:
    prefix = f"premium:{action}:"
    if not data or not data.startswith(prefix):
        return None
    raw_id = data[len(prefix):]
    try:
        chat_id = int(raw_id)
    except (TypeError, ValueError):
        return None
    return chat_id if str(chat_id) == raw_id else None


async def _is_purchase_authorized(*, bot: Bot, buyer_user_id: int, chat_id: int) -> tuple[bool, str | None]:
    """Require live buyer admin status and a bot membership able to send."""
    try:
        buyer_member = await bot.get_chat_member(chat_id=chat_id, user_id=buyer_user_id)
        if not is_telegram_chat_admin(buyer_member):
            return False, "buyer_not_admin"
        bot_user = await bot.get_me()
        bot_member = await bot.get_chat_member(chat_id=chat_id, user_id=bot_user.id)
    except Exception:
        logger.warning(
            "Selara AI purchase authority check failed chat_id=%s",
            chat_id,
            exc_info=True,
        )
        return False, "telegram_unavailable"

    status = getattr(bot_member, "status", None)
    if status in {"member", "administrator", "creator"}:
        return True, None
    if status == "restricted" and bool(getattr(bot_member, "is_member", False)):
        if bool(getattr(bot_member, "can_send_messages", False)):
            return True, None
    return False, "bot_unavailable"


async def _is_owner_exempt(*, bot: Bot, chat_id: int, settings: Settings) -> bool:
    return await resolve_owner_admin_exemption(
        bot=bot,
        chat_id=chat_id,
        admin_user_id=settings.admin_user_id,
    )


async def _edit_callback_message(query: CallbackQuery, text: str, *, reply_markup=None) -> None:
    message = query.message
    if message is None or message.chat.type != "private":
        return
    try:
        await message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except TelegramAPIError:
        logger.info("Selara AI checkout message could not be edited")


@router.message(Command("premium"))
async def premium_command(
    message: Message,
    bot: Bot,
    session_factory,
    settings: Settings,
) -> None:
    if message.chat.type != "private":
        username = settings.bot_username.strip().lstrip("@")
        url = f"https://t.me/{username}" if username else "https://t.me/"
        builder = InlineKeyboardBuilder()
        if username:
            builder.button(text="Открыть Selara в личке", url=url)
        await message.answer(
            "Покупка Selara AI оформляется в личке с ботом." + (" Откройте диалог и отправьте /premium." if not username else ""),
            reply_markup=builder.as_markup() if username else None,
        )
        return
    if message.from_user is None:
        return
    try:
        product = _product_for_settings(settings)
    except SelaraAiProductUnavailable:
        await message.answer("Покупка Selara AI пока недоступна: цена или AI-провайдер ещё не настроены.")
        logger.warning("Selara AI checkout unavailable reason=product_not_configured")
        return

    repository = SqlAlchemyTelegramStarsRepository(session_factory)
    chats = await repository.list_purchasable_chats(user_id=message.from_user.id)
    if not chats:
        await message.answer(
            "Не нашёл доступных чатов. Сначала отправьте сообщение в нужной группе, "
            "затем откройте /premium в личке с ботом."
        )
        return
    await message.answer(
        f"<b>{escape(product.title)}</b>\n"
        f"Цена: <b>{product.price_stars} ⭐</b>.\n"
        "Доступ оформляется для чата и включает автоматические итоги дня. "
        f"Каждая повторная покупка добавляет ещё {product.duration_label}.\n\n"
        "Выберите чат:",
        parse_mode="HTML",
        reply_markup=_selection_keyboard(chats),
    )


@router.callback_query(F.data.startswith("premium:select:"))
async def select_premium_chat(
    query: CallbackQuery,
    bot: Bot,
    session_factory,
    settings: Settings,
) -> None:
    await query.answer()
    if query.message is None or query.message.chat.type != "private" or query.from_user is None:
        return
    chat_id = _callback_chat_id(query.data, "select")
    if chat_id is None:
        await _edit_callback_message(query, "Не удалось распознать выбранный чат. Отправьте /premium заново.")
        return
    repository = SqlAlchemyTelegramStarsRepository(session_factory)
    if not await repository.user_has_known_chat(user_id=query.from_user.id, chat_id=chat_id):
        logger.info("Selara AI purchase selection denied chat_id=%s reason=not_known_to_user", chat_id)
        await _edit_callback_message(query, "Этот чат больше недоступен для покупки. Отправьте /premium заново.")
        return

    if await _is_owner_exempt(bot=bot, chat_id=chat_id, settings=settings):
        await _edit_callback_message(
            query,
            "Selara AI уже доступна этому чату через внутренний доступ. Покупка не требуется.",
        )
        return

    authorized, reason = await _is_purchase_authorized(
        bot=bot,
        buyer_user_id=query.from_user.id,
        chat_id=chat_id,
    )
    if not authorized:
        logger.info("Selara AI purchase admin validation denied chat_id=%s reason=%s", chat_id, reason)
        await _edit_callback_message(query, _CHECKOUT_ACCESS_ERROR)
        return

    try:
        product = _product_for_settings(settings)
    except SelaraAiProductUnavailable:
        await _edit_callback_message(query, "Покупка временно недоступна: цена или AI-провайдер ещё не настроены.")
        return
    entitlement = await repository.get_entitlement(chat_id=chat_id)
    now = datetime.now(timezone.utc)
    active_until = (
        entitlement.valid_until
        if entitlement is not None and entitlement.status == "active" and entitlement.valid_until > now
        else None
    )
    label = escape(_chat_label(await repository.get_chat_title(chat_id=chat_id), chat_id))
    if active_until is not None:
        text = (
            f"<b>{label}</b>\n"
            f"Selara AI уже активна до <b>{_format_date(active_until, settings.bot_timezone)}</b>.\n"
            f"Новая покупка продлит срок ещё на {product.duration_label} — <b>{product.price_stars} ⭐</b>."
        )
    else:
        text = (
            f"<b>{label}</b>\n"
            f"Selara AI будет доступна чату {product.duration_label} после оплаты.\n"
            f"Цена: <b>{product.price_stars} ⭐</b>. Продление не автоматическое."
        )
    await _edit_callback_message(
        query,
        text,
        reply_markup=_purchase_keyboard(chat_id=chat_id, price_stars=product.price_stars),
    )


@router.callback_query(F.data.startswith("premium:buy:"))
async def buy_selara_ai(
    query: CallbackQuery,
    bot: Bot,
    session_factory,
    settings: Settings,
) -> None:
    await query.answer()
    if query.message is None or query.message.chat.type != "private" or query.from_user is None:
        return
    chat_id = _callback_chat_id(query.data, "buy")
    if chat_id is None:
        await _edit_callback_message(query, "Не удалось распознать чат. Отправьте /premium заново.")
        return
    try:
        product = _product_for_settings(settings)
    except SelaraAiProductUnavailable:
        await _edit_callback_message(query, "Покупка временно недоступна: цена или AI-провайдер ещё не настроены.")
        return

    repository = SqlAlchemyTelegramStarsRepository(session_factory)
    if not await repository.user_has_known_chat(user_id=query.from_user.id, chat_id=chat_id):
        logger.info("Selara AI purchase invoice denied chat_id=%s reason=not_known_to_user", chat_id)
        await _edit_callback_message(query, "Этот чат больше недоступен для покупки. Отправьте /premium заново.")
        return
    if await _is_owner_exempt(bot=bot, chat_id=chat_id, settings=settings):
        await _edit_callback_message(
            query,
            "Selara AI уже доступна этому чату через внутренний доступ. Покупка не требуется.",
        )
        return
    authorized, reason = await _is_purchase_authorized(
        bot=bot,
        buyer_user_id=query.from_user.id,
        chat_id=chat_id,
    )
    if not authorized:
        logger.info("Selara AI purchase admin validation denied chat_id=%s reason=%s", chat_id, reason)
        await _edit_callback_message(query, _CHECKOUT_ACCESS_ERROR)
        return

    chat_title = await repository.get_chat_title(chat_id=chat_id)
    try:
        intent = await repository.create_purchase_intent(
            buyer_user_id=query.from_user.id,
            source_chat_id=chat_id,
            chat_id=chat_id,
            chat_title=chat_title,
            product=product,
        )
    except Exception:
        logger.exception("Selara AI purchase intent creation failed chat_id=%s", chat_id)
        await _edit_callback_message(query, "Не удалось создать счёт. Попробуйте позже.")
        return

    try:
        await bot.send_invoice(
            chat_id=query.from_user.id,
            title=product.title,
            description=product.description,
            payload=intent.invoice_payload,
            provider_token="",
            currency=product.currency,
            prices=[LabeledPrice(label=product.title, amount=product.price_stars)],
        )
        try:
            await repository.mark_invoice_sent(intent_id=intent.id)
        except Exception as exc:
            logger.exception("Selara AI invoice sent timestamp could not be saved chat_id=%s", chat_id)
        logger.info("Selara AI invoice sent chat_id=%s", chat_id)
        await _edit_callback_message(query, "Счёт отправлен отдельным сообщением в этот личный чат.")
    except TelegramAPIError as exc:
        logger.warning(
            "Selara AI invoice send failed chat_id=%s exception_type=%s",
            chat_id,
            type(exc).__name__,
        )
        await _edit_callback_message(query, "Не удалось отправить счёт. Отправьте /premium и попробуйте ещё раз.")


@router.callback_query(F.data == "premium:cancel")
async def cancel_premium_purchase(query: CallbackQuery) -> None:
    await query.answer()
    await _edit_callback_message(query, "Покупка отменена. Чтобы начать заново, отправьте /premium.")


@router.pre_checkout_query()
async def selara_ai_pre_checkout(
    query: PreCheckoutQuery,
    bot: Bot,
    session_factory,
) -> None:
    repository = SqlAlchemyTelegramStarsRepository(session_factory)
    accepted = False
    error_message = "Счёт недействителен. Отправьте /premium и создайте новый."
    try:
        intent = await repository.get_purchase_intent(invoice_payload=query.invoice_payload)
        if intent is None:
            logger.warning("Telegram Stars pre-checkout rejected reason=unknown_intent")
        elif (
            intent.pre_checkout_query_id == query.id
            and intent.pre_checkout_accepted_at is not None
        ):
            result = await repository.accept_pre_checkout(
                invoice_payload=query.invoice_payload,
                buyer_user_id=query.from_user.id,
                amount_stars=query.total_amount,
                currency=query.currency,
                query_id=query.id,
                checked_chat_id=intent.chat_id,
            )
            accepted = result.accepted
        elif intent.buyer_user_id != query.from_user.id:
            logger.warning("Telegram Stars pre-checkout rejected reason=wrong_buyer")
            error_message = "Этот счёт предназначен другому пользователю."
        elif intent.amount_stars != query.total_amount or intent.currency != query.currency:
            reason = "wrong_amount" if intent.amount_stars != query.total_amount else "wrong_currency"
            logger.warning("Telegram Stars pre-checkout rejected reason=%s", reason)
            error_message = "Сумма счёта не совпадает. Запустите /premium заново."
        elif intent.status != "open" or intent.pre_checkout_accepted_at is not None:
            logger.warning("Telegram Stars pre-checkout rejected reason=intent_already_used")
            error_message = "Этот счёт уже использован. Отправьте /premium для новой покупки."
        elif intent.expires_at <= datetime.now(timezone.utc):
            logger.warning("Telegram Stars pre-checkout rejected reason=expired_intent")
            error_message = "Срок действия счёта истёк. Отправьте /premium для нового счёта."
        else:
            authorized, reason = await _is_purchase_authorized(
                bot=bot,
                buyer_user_id=query.from_user.id,
                chat_id=intent.chat_id,
            )
            if not authorized:
                error_message = _CHECKOUT_ACCESS_ERROR if reason in {"buyer_not_admin", "bot_unavailable"} else _CHECKOUT_RETRY_ERROR
                logger.info("Telegram Stars pre-checkout authority denied chat_id=%s reason=%s", intent.chat_id, reason)
            else:
                result = await repository.accept_pre_checkout(
                    invoice_payload=query.invoice_payload,
                    buyer_user_id=query.from_user.id,
                    amount_stars=query.total_amount,
                    currency=query.currency,
                    query_id=query.id,
                    checked_chat_id=intent.chat_id,
                )
                if result.reason == "target_changed" and result.chat_id is not None:
                    target_authorized, target_reason = await _is_purchase_authorized(
                        bot=bot,
                        buyer_user_id=query.from_user.id,
                        chat_id=result.chat_id,
                    )
                    if target_authorized:
                        result = await repository.accept_pre_checkout(
                            invoice_payload=query.invoice_payload,
                            buyer_user_id=query.from_user.id,
                            amount_stars=query.total_amount,
                            currency=query.currency,
                            query_id=query.id,
                            checked_chat_id=result.chat_id,
                        )
                    else:
                        error_message = _CHECKOUT_ACCESS_ERROR
                        logger.info(
                            "Telegram Stars pre-checkout authority denied chat_id=%s reason=%s",
                            result.chat_id,
                            target_reason,
                        )
                accepted = result.accepted
                if not accepted and result.reason == "target_unavailable":
                    error_message = _CHECKOUT_ACCESS_ERROR
    except Exception as exc:
        logger.error(
            "Telegram Stars pre-checkout validation unavailable exception_type=%s",
            type(exc).__name__,
        )
        error_message = _CHECKOUT_RETRY_ERROR

    try:
        await query.answer(ok=accepted, error_message=None if accepted else error_message)
    except TelegramAPIError:
        logger.exception("Telegram Stars pre-checkout answer failed")


@router.message(F.chat.type == "private", F.successful_payment)
async def selara_ai_successful_payment(
    message: Message,
    session_factory,
    settings: Settings,
) -> None:
    payment = message.successful_payment
    if payment is None or message.from_user is None:
        return
    logger.info("Telegram Stars successful_payment received")
    repository = SqlAlchemyTelegramStarsRepository(session_factory)
    while True:
        try:
            result = await repository.process_successful_payment(
                buyer_user_id=message.from_user.id,
                invoice_payload=payment.invoice_payload,
                telegram_payment_charge_id=payment.telegram_payment_charge_id,
                provider_payment_charge_id=payment.provider_payment_charge_id,
                amount_stars=payment.total_amount,
                currency=payment.currency,
                payment_at=message.date,
            )
            break
        except asyncio.CancelledError:
            raise
        except (SQLAlchemyError, OSError, TimeoutError):
            logger.error("Telegram Stars payment persistence failed; retaining update for retry")
            await asyncio.sleep(5)
        except Exception as exc:
            # A confirmed Telegram payment must not be acknowledged before its
            # economic effect is durable. Keep retrying this one update and log
            # the type without dumping the Telegram update or payment metadata.
            logger.error(
                "Telegram Stars payment processing failed; retaining update for retry exception_type=%s",
                type(exc).__name__,
            )
            await asyncio.sleep(5)

    if result.state == "rejected":
        await _send_payment_reconciliation_message(message, result)
        return
    if result.chat_id is None or result.valid_until is None:
        # The update is already durably recorded. Retry delivery only; never
        # re-run the economic effect to recover this user-facing message.
        await _send_payment_reconciliation_message(message, result)
        return
    try:
        chat_title = await repository.get_chat_title(chat_id=result.chat_id)
    except Exception:
        logger.exception("Selara AI payment confirmation could not load chat title chat_id=%s", result.chat_id)
        chat_title = None
    label = escape(_chat_label(chat_title, result.chat_id))
    until = _format_date(result.valid_until, settings.bot_timezone)
    try:
        await message.answer(
            f"✅ Selara AI активирована для <b>{label}</b> до <b>{until}</b>.\n"
            "Включены автоматические итоги дня и AI-функции чата. Продление не автоматическое.",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.error(
            "Selara AI payment confirmation delivery failed chat_id=%s exception_type=%s",
            result.chat_id,
            type(exc).__name__,
        )


async def _send_payment_reconciliation_message(message: Message, result: PaymentResult) -> None:
    try:
        await message.answer(
            "Telegram подтвердил оплату, но не удалось автоматически активировать Selara AI. "
            "Платёж сохранён для проверки; обратитесь к владельцу бота."
        )
    except TelegramAPIError:
        logger.exception("Selara AI payment support message delivery failed")
