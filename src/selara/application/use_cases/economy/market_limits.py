from __future__ import annotations

# Ограничения нужны не только для игрового баланса, но и чтобы произвольные
# Python-int из HTTP/Telegram не доходили до BIGINT-колонок PostgreSQL.
MAX_MARKET_QUANTITY = 1_000_000
MAX_MARKET_UNIT_PRICE = 1_000_000_000
MAX_MARKET_LISTING_ID = 2**63 - 1
