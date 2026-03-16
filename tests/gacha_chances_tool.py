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


def _build_report(path: Path) -> str:
    payload = _load_banner(path)
    title = payload.get("title", path.stem)
    code = payload.get("code", path.stem)
    cooldown_seconds = payload.get("cooldown_seconds", 0)
    cards = payload.get("cards", [])
    total_weight = sum(int(card.get("weight", 0)) for card in cards)

    lines: list[str] = []
    lines.append(f"{'=' * 72}")
    lines.append(f"{title} [{code}]")
    lines.append(f"{'=' * 72}")
    lines.append(f"Кулдаун: {cooldown_seconds} сек")
    lines.append(f"Карточек: {len(cards)}")
    lines.append(f"Сумма весов: {total_weight}")
    lines.append("")

    rarity_weights: dict[str, int] = defaultdict(int)
    rarity_counts: dict[str, int] = defaultdict(int)
    for card in cards:
        rarity = card["rarity"]
        rarity_weights[rarity] += card["weight"]
        rarity_counts[rarity] += 1

    lines.append("Шансы по редкостям:")
    for rarity in ("common", "rare", "epic", "legendary"):
        weight = rarity_weights.get(rarity, 0)
        if weight <= 0:
            continue
        chance = weight / total_weight * 100
        lines.append(
            f"  {RARITY_LABELS.get(rarity, rarity)}: {_format_percent(chance)} "
            f"(вес {weight}, карточек {rarity_counts[rarity]})"
        )

    lines.append("")
    lines.append("Шансы по карточкам:")
    for card in sorted(cards, key=lambda item: (-item["weight"], item["name"])):
        chance = card["weight"] / total_weight * 100
        lines.append(
            f"  {card['name']:<28} | {card['rarity']:<10} | "
            f"weight={card['weight']:<3} | chance={_format_percent(chance)}"
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
