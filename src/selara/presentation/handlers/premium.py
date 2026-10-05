from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from selara.application.selara_ai_product import (
    SELARA_AI_PRODUCT_KEY,
    SELARA_AI_TERMS_VERSION,
    SelaraAiProductUnavailable,
    get_selara_ai_product,
)
from selara.core.config import Settings
from selara.infrastructure.db.telegram_stars import (
    PaymentResult,
    PurchaseIntentRateLimited,
    SqlAlchemyTelegramStarsRepository,
)
from selara.presentation.auth import is_telegram_chat_admin, resolve_owner_admin_exemption

logger = logging.getLogger(__name__)
router = Router(name="premium")

_CHECKOUT_ACCESS_ERROR = "Для оплаты нужно быть администратором выбранного чата, а Selara должна оставаться в нём."
_CHECKOUT_RETRY_ERROR = "Не удалось проверить чат. Попробуйте открыть /premium и повторить оплату позже."
_PRECHECKOUT_VALIDATION_DEADLINE_SECONDS = 6.0
_PRECHECKOUT_ANSWER_DEADLINE_SECONDS = 2.0
_PAYMENT_RETRY_ALERT_ATTEMPT = 3
_PAYMENT_RETRY_MAX_SECONDS = 60
_PAYMENT_OWNER_ALERT_TIMEOUT_SECONDS = 3.0
_PAYMENT_CONFIRMATION_TIMEOUT_SECONDS = 3.0
_STAR_REFUND_TIMEOUT_SECONDS = 10.0


def _payment_retry_delay(attempt: int) -> int:
    return min(2 ** max(attempt - 1, 0), _PAYMENT_RETRY_MAX_SECONDS)


def _terms_text() -> str:
    return (
        "<b>Условия покупки Selara AI</b>\n\n"
        "1. Доступ приобретается для выбранной Telegram-группы и действует 30 дней с момента оплаты.\n"
        "2. В стоимость входят автоматические итоги дня и доступ к AI-функциям чата по действующим лимитам.\n"
        "3. Подписка не продлевается автоматически. Каждая новая успешная покупка "
        "добавляет 30 дней к активному сроку; после окончания доступ отключается.\n"
        "4. Оплата проходит в Telegram Stars. Покупатель подтверждает, что вправе "
        "оформить доступ для выбранного чата. Доступ после оплаты принадлежит чату "
        "и сохраняется до окончания срока, даже если покупатель перестанет быть "
        "администратором.\n"
        "5. Работа AI-функций зависит от доступности настроенного AI-провайдера и конфигурации бота. "
        "При временной недоступности или отключении провайдера функции могут быть приостановлены до конца оплаченного срока.\n"
        "6. Вопросы по платежу можно отправить через <code>/paysupport</code>.\n\n"
        "Нажимая кнопку принятия условий перед счётом, вы подтверждаете, что прочитали и принимаете эти условия."
    )


def _terms_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Вернуться к покупке", callback_data=f"premium:select:{chat_id}")
    return builder.as_markup()


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
        text=f"Продолжить и оплатить — принимаю условия · {price_stars} ⭐",
        callback_data=f"premium:accept:{chat_id}",
    )
    builder.button(text="Условия покупки", callback_data=f"premium:terms:{chat_id}")
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
        bot_member = await bot.get_chat_member(chat_id=chat_id, user_id=bot.id)
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
        f"Каждая повторная покупка добавляет ещё {product.duration_label}.\n"
        "Перед счётом можно прочитать /terms; продление не автоматическое.\n\n"
        "Выберите чат:",
        parse_mode="HTML",
        reply_markup=_selection_keyboard(chats),
    )


@router.message(Command("terms"))
async def selara_ai_terms(message: Message) -> None:
    await message.answer(_terms_text(), parse_mode="HTML")


@router.message(Command("paysupport"))
async def selara_ai_payment_support(message: Message) -> None:
    if message.chat.type != "private":
        await message.answer("По вопросам оплаты напишите боту в личку и отправьте /paysupport.")
        return
    await message.answer(
        "Поддержка по покупкам Selara AI: отправьте обращение командой "
        "<code>/feedback поддержка: описание вопроса по платежу</code>. "
        "Укажите дату оплаты, сумму в Stars и название чата. Обращение попадёт команде Selara. "
        "Не отправляйте коды входа или данные Telegram-аккаунта. "
        "Поддержка Telegram не видит платежи, сделанные у ботов.",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("premium:terms:"))
async def show_selara_ai_terms(query: CallbackQuery) -> None:
    await query.answer()
    if query.message is None or query.message.chat.type != "private":
        return
    chat_id = _callback_chat_id(query.data, "terms")
    if chat_id is None:
        await _edit_callback_message(query, _terms_text(), reply_markup=None)
        return
    await _edit_callback_message(query, _terms_text(), reply_markup=_terms_keyboard(chat_id))


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
            f"Новая покупка продлит срок ещё на {product.duration_label} — "
            f"<b>{product.price_stars} ⭐</b>.\n"
            "Перед оплатой нужно подтвердить принятие условий покупки."
        )
    else:
        text = (
            f"<b>{label}</b>\n"
            f"Selara AI будет доступна чату {product.duration_label} после оплаты.\n"
            f"Цена: <b>{product.price_stars} ⭐</b>. Продление не автоматическое.\n"
            "Перед оплатой нужно подтвердить принятие условий покупки."
        )
    await _edit_callback_message(
        query,
        text,
        reply_markup=_purchase_keyboard(chat_id=chat_id, price_stars=product.price_stars),
    )


@router.callback_query(F.data.startswith("premium:accept:"))
async def accept_terms_and_buy_selara_ai(
    query: CallbackQuery,
    bot: Bot,
    session_factory,
    settings: Settings,
) -> None:
    await query.answer()
    if query.message is None or query.message.chat.type != "private" or query.from_user is None:
        return
    chat_id = _callback_chat_id(query.data, "accept")
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
            terms_version=SELARA_AI_TERMS_VERSION,
            terms_accepted_at=datetime.now(timezone.utc),
        )
    except PurchaseIntentRateLimited:
        logger.info("Selara AI invoice creation rate limited chat_id=%s", chat_id)
        await _edit_callback_message(query, "Счёт для этого чата уже создавался недавно. Подождите минуту и попробуйте снова.")
        return
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


async def _validate_pre_checkout(
    query: PreCheckoutQuery,
    *,
    bot: Bot,
    repository: SqlAlchemyTelegramStarsRepository,
) -> tuple[bool, str]:
    intent = await repository.get_purchase_intent(invoice_payload=query.invoice_payload)
    if intent is None:
        logger.warning("Telegram Stars pre-checkout rejected reason=unknown_intent")
        return False, "Счёт недействителен. Отправьте /premium и создайте новый."
    if intent.buyer_user_id != query.from_user.id:
        logger.warning("Telegram Stars pre-checkout rejected reason=wrong_buyer")
        return False, "Этот счёт предназначен другому пользователю."
    if intent.product_key != SELARA_AI_PRODUCT_KEY:
        logger.warning("Telegram Stars pre-checkout rejected reason=unsupported_product")
        return False, "Этот продукт больше недоступен. Отправьте /premium."
    if intent.amount_stars != query.total_amount:
        logger.warning("Telegram Stars pre-checkout rejected reason=wrong_amount")
        return False, "Сумма счёта не совпадает. Запустите /premium заново."
    if intent.currency != query.currency or intent.currency != "XTR":
        logger.warning("Telegram Stars pre-checkout rejected reason=wrong_currency")
        return False, "Валюта счёта не совпадает. Запустите /premium заново."
    if intent.expires_at <= datetime.now(timezone.utc):
        logger.warning("Telegram Stars pre-checkout rejected reason=expired_intent")
        return False, "Срок действия счёта истёк. Отправьте /premium для нового счёта."
    if intent.status == "consumed":
        logger.warning("Telegram Stars pre-checkout rejected reason=intent_already_used")
        return False, "Этот счёт уже оплачен. Отправьте /premium для новой покупки."
    if not intent.terms_version or intent.terms_accepted_at is None:
        logger.warning("Telegram Stars pre-checkout rejected reason=terms_not_accepted")
        return False, "Перед оплатой нужно прочитать и принять условия через /premium."

    authorized, reason = await _is_purchase_authorized(
        bot=bot,
        buyer_user_id=query.from_user.id,
        chat_id=intent.chat_id,
    )
    if not authorized:
        logger.info("Telegram Stars pre-checkout authority denied chat_id=%s reason=%s", intent.chat_id, reason)
        return False, (
            _CHECKOUT_ACCESS_ERROR
            if reason in {"buyer_not_admin", "bot_unavailable"}
            else _CHECKOUT_RETRY_ERROR
        )

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
            logger.info(
                "Telegram Stars pre-checkout authority denied chat_id=%s reason=%s",
                result.chat_id,
                target_reason,
            )
            return False, _CHECKOUT_ACCESS_ERROR

    if result.reason == "target_unavailable":
        return False, _CHECKOUT_ACCESS_ERROR
    if result.accepted:
        return True, ""
    if result.reason in {"wrong_buyer", "wrong_amount", "wrong_currency", "expired_intent"}:
        return False, "Счёт больше не действителен. Отправьте /premium и создайте новый."
    if result.reason == "intent_already_used":
        return False, "Этот счёт уже оплачен. Отправьте /premium для новой покупки."
    return False, _CHECKOUT_RETRY_ERROR


async def selara_ai_pre_checkout(
    query: PreCheckoutQuery,
    bot: Bot,
    session_factory,
) -> None:
    repository = SqlAlchemyTelegramStarsRepository(session_factory)
    accepted = False
    error_message = "Счёт недействителен. Отправьте /premium и создайте новый."
    try:
        async with asyncio.timeout(_PRECHECKOUT_VALIDATION_DEADLINE_SECONDS):
            accepted, error_message = await _validate_pre_checkout(
                query,
                bot=bot,
                repository=repository,
            )
    except TimeoutError:
        logger.warning("Telegram Stars pre-checkout validation timed out")
        error_message = _CHECKOUT_RETRY_ERROR
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error(
            "Telegram Stars pre-checkout validation unavailable exception_type=%s",
            type(exc).__name__,
        )
        error_message = _CHECKOUT_RETRY_ERROR

    try:
        async with asyncio.timeout(_PRECHECKOUT_ANSWER_DEADLINE_SECONDS):
            await query.answer(ok=accepted, error_message=None if accepted else error_message)
    except (TelegramAPIError, TimeoutError) as exc:
        logger.warning(
            "Telegram Stars pre-checkout answer failed exception_type=%s",
            type(exc).__name__,
        )


async def selara_ai_successful_payment(
    message: Message,
    session_factory,
    settings: Settings,
    bot: Bot,
) -> None:
    payment = message.successful_payment
    if payment is None or message.from_user is None:
        return
    logger.info("Telegram Stars successful_payment received")
    repository = SqlAlchemyTelegramStarsRepository(session_factory)
    attempt = 0
    owner_alert_sent = False
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
        except Exception as exc:
            # Never acknowledge a confirmed payment before its economic effect
            # is durable. Alert once, then keep a capped exponential backoff.
            attempt += 1
            delay = _payment_retry_delay(attempt)
            logger.error(
                "Telegram Stars payment processing failed; retaining update for retry "
                "attempt=%s retry_in_seconds=%s exception_type=%s",
                attempt,
                delay,
                type(exc).__name__,
            )
            if attempt >= _PAYMENT_RETRY_ALERT_ATTEMPT and not owner_alert_sent:
                owner_alert_sent = True
                await _send_payment_owner_alert(
                    bot=bot,
                    settings=settings,
                    text=(
                        "🚨 Не удалось записать подтверждённый Telegram Stars платёж. "
                        "Обработка и polling приостановлены до успешной записи.\n"
                        f"Charge: {payment.telegram_payment_charge_id}\n"
                        f"Попыток: {attempt}; последняя ошибка: {type(exc).__name__}."
                    ),
                    log_event="persistence_retry",
                )
            await asyncio.sleep(delay)

    if result.state == "rejected":
        await _notify_owner_of_rejected_payment(bot=bot, settings=settings, result=result)
        await _send_payment_reconciliation_message(message, result)
        return
    if result.chat_id is None or result.valid_until is None:
        # The update is already durably recorded. Retry delivery only; never
        # re-run the economic effect to recover this user-facing message.
        await _send_payment_owner_alert(
            bot=bot,
            settings=settings,
            text=(
                "🚨 Telegram Stars платёж сохранён, но подтверждение доступа не удалось собрать. "
                f"Payment record: {result.payment_id or 'unknown'}; требуется ручная проверка."
            ),
            log_event="confirmation_context_missing",
        )
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
        async with asyncio.timeout(_PAYMENT_CONFIRMATION_TIMEOUT_SECONDS):
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
        async with asyncio.timeout(_PAYMENT_CONFIRMATION_TIMEOUT_SECONDS):
            await message.answer(
                "Telegram подтвердил оплату, но не удалось автоматически активировать Selara AI. "
                "Платёж сохранён для проверки; обратитесь к владельцу бота."
            )
    except Exception as exc:
        logger.error(
            "Selara AI payment support message delivery failed exception_type=%s",
            type(exc).__name__,
        )


async def _send_payment_owner_alert(*, bot: Bot, settings: Settings, text: str, log_event: str) -> None:
    admin_user_id = settings.admin_user_id
    if admin_user_id is None:
        logger.error("Telegram Stars owner alert skipped event=%s reason=admin_not_configured", log_event)
        return
    try:
        async with asyncio.timeout(_PAYMENT_OWNER_ALERT_TIMEOUT_SECONDS):
            await bot.send_message(chat_id=admin_user_id, text=text)
    except Exception as exc:
        logger.error(
            "Telegram Stars owner alert delivery failed event=%s exception_type=%s",
            log_event,
            type(exc).__name__,
        )


async def _notify_owner_of_rejected_payment(*, bot: Bot, settings: Settings, result: PaymentResult) -> None:
    payment_id = result.payment_id
    if payment_id is None:
        logger.error("Rejected Telegram Stars payment has no persisted payment id")
        return
    await _send_payment_owner_alert(
        bot=bot,
        settings=settings,
        text=(
            "⚠️ Telegram подтвердил Stars платёж, но Selara AI не активирована.\n"
            f"Payment record: {payment_id}; reason: {result.reason or 'unknown'}.\n"
            f"Чтобы вернуть Stars для этой отклонённой оплаты: /stars_refund {payment_id}"
        ),
        log_event="rejected_payment",
    )


@router.message(Command("stars_refund"))
async def refund_rejected_stars_payment(
    message: Message,
    bot: Bot,
    session_factory,
    settings: Settings,
) -> None:
    if message.chat.type != "private" or message.from_user is None:
        return
    if settings.admin_user_id is None or message.from_user.id != settings.admin_user_id:
        await message.answer("Команда доступна только владельцу бота в личном чате.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit() or int(parts[1]) <= 0:
        await message.answer("Использование: /stars_refund <payment_id>.")
        return

    payment_id = int(parts[1])
    repository = SqlAlchemyTelegramStarsRepository(session_factory)
    claim = await repository.claim_rejected_payment_refund(
        payment_id=payment_id,
        requested_by_user_id=message.from_user.id,
    )
    if claim.state == "not_found":
        await message.answer("Платёж с таким номером не найден.")
        return
    if claim.state == "not_rejected":
        await message.answer("Эта оплата не отклонена; команда возвращает только отклонённые платежи.")
        return
    if claim.state == "refunded":
        await message.answer("Для этого платежа возврат уже записан.")
        return
    if claim.state == "pending":
        await message.answer(
            "Возврат уже начат, но Telegram не подтвердил результат. Проверьте Stars-транзакции; "
            "повторно команду не запускайте."
        )
        return
    if claim.state == "failed":
        await message.answer(
            "Telegram отклонил предыдущую попытку. Проверьте платёж вручную перед дальнейшими действиями."
        )
        return
    if claim.buyer_user_id is None or claim.telegram_payment_charge_id is None:
        await message.answer("Не хватает данных платежа для возврата; требуется ручная проверка.")
        return

    try:
        async with asyncio.timeout(_STAR_REFUND_TIMEOUT_SECONDS):
            refunded = await bot.refund_star_payment(
                user_id=claim.buyer_user_id,
                telegram_payment_charge_id=claim.telegram_payment_charge_id,
            )
    except TelegramBadRequest as exc:
        await repository.finish_rejected_payment_refund(
            payment_id=payment_id,
            succeeded=False,
            result_code=type(exc).__name__,
        )
        logger.warning("Telegram Stars refund rejected payment_id=%s exception_type=%s", payment_id, type(exc).__name__)
        await message.answer("Telegram отклонил возврат. Запись сохранена для ручной проверки.")
        return
    except Exception as exc:
        # A timeout or transport error may happen after Telegram applied the
        # refund. Keep the durable row pending so a retry cannot double-refund.
        logger.error(
            "Telegram Stars refund outcome is uncertain payment_id=%s exception_type=%s",
            payment_id,
            type(exc).__name__,
        )
        await message.answer(
            "Ответ Telegram не получен. Возврат отмечен как ожидающий проверки; повторно не запускайте команду."
        )
        return

    if not refunded:
        await repository.finish_rejected_payment_refund(
            payment_id=payment_id,
            succeeded=False,
            result_code="api_returned_false",
        )
        await message.answer("Telegram не подтвердил возврат. Запись сохранена для ручной проверки.")
        return

    await repository.finish_rejected_payment_refund(
        payment_id=payment_id,
        succeeded=True,
        result_code="refunded",
    )
    logger.info("Telegram Stars refund completed payment_id=%s", payment_id)
    await message.answer(f"Возврат {claim.amount_stars or 0} ⭐ подтверждён Telegram.")


def build_payment_router() -> Router:
    payment_router = Router(name="selara_ai_payments")
    payment_router.pre_checkout_query.register(selara_ai_pre_checkout)
    payment_router.message.register(refund_rejected_stars_payment, Command("stars_refund"))
    payment_router.message.register(selara_ai_successful_payment, F.successful_payment)
    return payment_router
