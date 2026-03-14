from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PlayerModel(Base):
    __tablename__ = "gacha_players"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    adventure_rank: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    adventure_xp: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_primogems: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_pull_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlayerBannerCooldownModel(Base):
    __tablename__ = "gacha_player_banner_cooldowns"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    banner: Mapped[str] = mapped_column(String(32), primary_key=True)
    next_pull_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PullHistoryModel(Base):
    __tablename__ = "gacha_pull_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    banner: Mapped[str] = mapped_column(String(32), nullable=False)
    character_code: Mapped[str] = mapped_column(String(64), nullable=False)
    character_name: Mapped[str] = mapped_column(String(128), nullable=False)
    rarity: Mapped[str] = mapped_column(String(16), nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    primogems: Mapped[int] = mapped_column(Integer, nullable=False)
    adventure_xp: Mapped[int] = mapped_column(Integer, nullable=False)
    image_url: Mapped[str] = mapped_column(String(512), nullable=False)
    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class PlayerCardCollectionModel(Base):
    __tablename__ = "gacha_player_cards"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    banner: Mapped[str] = mapped_column(String(32), primary_key=True)
    character_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    copies_owned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
