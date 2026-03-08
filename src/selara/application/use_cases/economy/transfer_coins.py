from __future__ import annotations

from datetime import datetime, timezone

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.common import get_account_or_error, resolve_scope_or_error, to_meta_json
from selara.application.use_cases.economy.results import TransferCoinsResult


MIN_TRANSFER = 50
MAX_TRANSFER = 1500


async def execute(
    repo: EconomyRepository,
    *,
    economy_mode: str,
    chat_id: int | None,
    sender_user_id: int,
    receiver_user_id: int,
    amount: int,
    transfer_daily_limit: int,
    transfer_tax_percent: int,
    event_at: datetime | None = None,
) -> TransferCoinsResult:
    now = event_at or datetime.now(timezone.utc)

    if sender_user_id == receiver_user_id:
        return TransferCoinsResult(
            accepted=False,
            reason="Нельзя перевести монеты самому себе.",
            amount=0,
            tax_amount=0,
            sender_balance=None,
            receiver_balance=None,
        )

    if amount < MIN_TRANSFER or amount > MAX_TRANSFER:
        return TransferCoinsResult(
            accepted=False,
            reason=f"Сумма перевода должна быть в диапазоне {MIN_TRANSFER}..{MAX_TRANSFER}.",
            amount=0,
            tax_amount=0,
            sender_balance=None,
            receiver_balance=None,
        )

    scope, error = await resolve_scope_or_error(repo, economy_mode=economy_mode, chat_id=chat_id, user_id=sender_user_id)
    if scope is None:
        return TransferCoinsResult(
            accepted=False,
            reason=error or "Не удалось определить режим экономики",
            amount=0,
            tax_amount=0,
            sender_balance=None,
            receiver_balance=None,
        )

    sender, _ = await get_account_or_error(repo, scope=scope, user_id=sender_user_id)
    receiver, _ = await get_account_or_error(repo, scope=scope, user_id=receiver_user_id)

    today = now.date()
    sent_amount, _ = await repo.get_transfer_daily(account_id=sender.id, limit_date=today)
    if sent_amount + amount > transfer_daily_limit:
        return TransferCoinsResult(
            accepted=False,
            reason=f"Превышен дневной лимит переводов ({transfer_daily_limit}).",
            amount=0,
            tax_amount=0,
            sender_balance=None,
            receiver_balance=None,
        )

    if sender.balance < amount:
        return TransferCoinsResult(
            accepted=False,
            reason="Недостаточно монет для перевода.",
            amount=0,
            tax_amount=0,
            sender_balance=None,
            receiver_balance=None,
        )

    tax_amount = int(amount * max(0, transfer_tax_percent) / 100)
    received = max(0, amount - tax_amount)

    sender_balance = await repo.add_balance(account_id=sender.id, delta=-amount)
    receiver_balance = await repo.add_balance(account_id=receiver.id, delta=received)
    await repo.touch_transfer_daily(account_id=sender.id, limit_date=today, sent_delta=amount, count_delta=1)

    await repo.add_ledger(
        account_id=sender.id,
        direction="out",
        amount=amount,
        reason="transfer_out",
        meta_json=to_meta_json({"to": receiver_user_id, "tax": tax_amount}),
    )
    await repo.add_ledger(
        account_id=receiver.id,
        direction="in",
        amount=received,
        reason="transfer_in",
        meta_json=to_meta_json({"from": sender_user_id}),
    )

    return TransferCoinsResult(
        accepted=True,
        reason=None,
        amount=amount,
        tax_amount=tax_amount,
        sender_balance=sender_balance,
        receiver_balance=receiver_balance,
    )
