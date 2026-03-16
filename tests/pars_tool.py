import json
import sys
import io
from pathlib import Path

# Ensure UTF-8 output by wrapping stdout
if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')



def rarity_to_stars(rarity: str) -> tuple[int, str]:
    """Преобразовать редкость в количество звезд и их визуальное представление."""
    rarity_map = {
        "common": (3, "[3★]"),
        "rare": (4, "[4★]"),
        "epic": (4, "[4★ ЭПИК]"),
        "legendary": (5, "[5★]"),
    }
    return rarity_map.get(rarity, (0, "[?]"))


def rarity_ru(rarity: str) -> str:
    """Локализовать редкость на русский."""
    rarity_names = {
        "common": "Обычный",
        "rare": "Редкий",
        "epic": "Эпический",
        "legendary": "Легендарный",
    }
    return rarity_names.get(rarity, "Неизвестный")


def main() -> None:
    if len(sys.argv) < 2:
        print("Использование: python pars_tool.py <путь_к_json> [--output <файл>]")
        sys.exit(1)

    json_path = Path(sys.argv[1])
    output_file = None
    
    # Check for output file option
    if len(sys.argv) >= 4 and sys.argv[2] == "--output":
        output_file = Path(sys.argv[3])

    if not json_path.is_file():
        print(f"Файл не найден: {json_path}")
        sys.exit(1)

    try:
        with json_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError as error:
        print(f"Ошибка чтения JSON: {error}")
        sys.exit(1)

    title = data.get("title", "Без названия")
    cards = data.get("cards", [])

    # Build output lines
    lines = []
    lines.append(f"\n{'='*60}")
    lines.append(f"{title}")
    lines.append(f"{'='*60}\n")

    # Count by rarity
    from collections import Counter
    rarity_counts = Counter(card.get("rarity", "unknown") for card in cards)
    lines.append(f"Всего персонажей: {len(cards)}")
    for rarity in ["common", "rare", "epic", "legendary"]:
        if rarity in rarity_counts:
            stars, stars_visual = rarity_to_stars(rarity)
            count = rarity_counts[rarity]
            lines.append(f"  {stars_visual} {rarity_ru(rarity)}: {count}")
    lines.append("")

    # List all characters
    for index, card in enumerate(cards, start=1):
        name = card.get("name", "Неизвестный")
        rarity = card.get("rarity", "unknown")
        stars, stars_visual = rarity_to_stars(rarity)
        rarity_name_ru = rarity_ru(rarity)
        
        lines.append(f"{index}. {name:20} | {stars_visual} | {rarity_name_ru}")

    # Output
    output_text = "\n".join(lines)
    
    if output_file:
        # Write to file with UTF-8 encoding
        with output_file.open("w", encoding="utf-8") as f:
            f.write(output_text)
        print(f"Результаты сохранены в: {output_file}")
    else:
        # Print to stdout
        print(output_text)


if __name__ == "__main__":
    main()