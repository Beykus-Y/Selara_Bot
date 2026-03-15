import json
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 2:
        print("Использование: python print_chars.py <путь_к_json>")
        sys.exit(1)

    json_path = Path(sys.argv[1])

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

    print(title)

    for index, card in enumerate(cards, start=1):
        name = card.get("name")
        if name is not None:
            print(f"{index}. {name}")


if __name__ == "__main__":
    main()