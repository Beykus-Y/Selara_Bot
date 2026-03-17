import io
import json
import sys
from collections import defaultdict
from pathlib import Path


if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


RARITY_LABELS = {
    "common": "common / 3★",
    "rare": "rare / 4★",
    "epic": "epic / 4★",
    "legendary": "legendary / 5★",
    "mythic": "mythic / 6★",
}

REGION_LABELS = {
    "mondstadt": "Мондштадт",
    "liyue": "Ли Юэ",
    "inazuma": "Инадзума",
    "sumeru": "Сумеру",
    "fontaine": "Фонтейн",
    "natlan": "Натлан",
    "nod_krai": "Нод-Край",
    "snezhnaya": "Снежная",
    "khaenriah": "Каэнри'ах",
    "unknown": "Неизвестно",
}

ELEMENT_LABELS = {
    "hydro": "Гидро",
    "electro": "Электро",
    "pyro": "Пиро",
    "cryo": "Крио",
    "anemo": "Анемо",
    "dendro": "Дендро",
    "geo": "Гео",
    "unknown": "Неизвестно",
}


def _load_banner(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _iter_banner_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(path.glob("*.json"))


def _format_percent(value: float) -> str:
    return f"{value:.4f}%"


def _format_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _format_card_meta(card: dict) -> str:
    parts: list[str] = []
    region_code = str(card.get("region_code", "") or "").strip()
    element_code = str(card.get("element_code", "") or "").strip()
    if region_code:
        parts.append(f"регион={REGION_LABELS.get(region_code, region_code)}")
    if element_code:
        parts.append(f"стихия={ELEMENT_LABELS.get(element_code, element_code)}")
    return f" | {' | '.join(parts)}" if parts else ""


def _build_report(path: Path) -> str:
    payload = _load_banner(path)
    title = payload.get("title", path.stem)
    code = payload.get("code", path.stem)
    cooldown_seconds = payload.get("cooldown_seconds", 0)
    cards = payload.get("cards", [])
    total_weight = sum(float(card.get("weight", 0) or 0) for card in cards)

    lines: list[str] = []
    lines.append(f"{'=' * 72}")
    lines.append(f"{title} [{code}]")
    lines.append(f"{'=' * 72}")
    lines.append(f"Кулдаун: {cooldown_seconds} сек")
    lines.append(f"Карточек: {len(cards)}")
    lines.append(f"Сумма весов: {_format_number(total_weight)}")
    lines.append("")

    rarity_weights: dict[str, float] = defaultdict(float)
    rarity_counts: dict[str, int] = defaultdict(int)
    for card in cards:
        rarity = card["rarity"]
        rarity_weights[rarity] += card["weight"]
        rarity_counts[rarity] += 1

    lines.append("Шансы по редкостям:")
    for rarity in ("common", "rare", "epic", "legendary", "mythic"):
        weight = rarity_weights.get(rarity, 0)
        if weight <= 0:
            continue
        chance = weight / total_weight * 100
        lines.append(
            f"  {RARITY_LABELS.get(rarity, rarity)}: {_format_percent(chance)} "
            f"(вес {_format_number(weight)}, карточек {rarity_counts[rarity]})"
        )

    lines.append("")
    lines.append("Шансы по карточкам:")
    for card in sorted(cards, key=lambda item: (-item["weight"], item["name"])):
        chance = card["weight"] / total_weight * 100
        lines.append(
            f"  {card['name']:<28} | {card['rarity']:<10} | "
            f"weight={_format_number(float(card['weight'])):<3} | chance={_format_percent(chance)}"
            f"{_format_card_meta(card)}"
        )

    return "\n".join(lines)


def main() -> None:
    if len(sys.argv) < 2:
        print("Использование: python gacha_chances_tool.py <json-файл|папка-с-banner-json>")
        sys.exit(1)

    target = Path(sys.argv[1])
    if not target.exists():
        print(f"Путь не найден: {target}")
        sys.exit(1)

    banner_files = _iter_banner_files(target)
    if not banner_files:
        print(f"JSON-файлы не найдены: {target}")
        sys.exit(1)

    reports = [_build_report(path) for path in banner_files]
    print("\n\n".join(reports))


if __name__ == "__main__":
    main()
