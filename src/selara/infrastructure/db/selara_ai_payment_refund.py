from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from selara.infrastructure.db.base import Base


class SelaraAiPaymentRefundModel(Base):
    """Single audited refund attempt for a rejected Telegram Stars payment."""

    __tablename__ = "selara_ai_payment_refunds"

    payment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("selara_ai_payments.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    requested_by_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'refunded', 'failed')",
            name="ck_selara_ai_payment_refunds_status",
        ),
        CheckConstraint(
            "(status = 'pending' AND completed_at IS NULL) OR "
            "(status IN ('refunded', 'failed') AND completed_at IS NOT NULL)",
            name="ck_selara_ai_payment_refunds_completion",
        ),
    )
