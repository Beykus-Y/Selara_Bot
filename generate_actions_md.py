import ast
from pathlib import Path

SOURCE = Path("src/selara/presentation/handlers/text_commands.py")
OUTPUT = Path("ACTIONS.md")

CATEGORIES: list[tuple[str, list[str]]] = [
    ("Удары и насилие", [
        "slap", "burn", "kill", "hit", "whack", "crack", "beatup", "maul",
        "stomp", "manhandle", "throw", "clobber", "stamp", "headknock",
        "wallop", "smash", "smear", "wreck", "uebat", "bonk", "smite",
        "clout", "faceslap", "knockout",
    ]),
    ("Доминирование и контроль", [
        "restrain", "wallpin", "scruff", "windowthrow", "stairdump",
        "humiliate", "ridicule", "flame", "bully", "dominate", "bossaround",
        "shutdown", "shutup", "fuckoff", "evict", "scold", "kick", "push",
        "drag", "shoo", "hurlout",
    ]),
    ("Нежность и забота", [
        "purr", "nuzzle", "cutesy", "sobshoulder", "curlup", "snuffle",
        "rumble", "nestle", "sneakclose", "caress", "hug", "kiss", "pat",
        "comfort", "calm", "protect", "carry", "wrap", "ruffle", "stareat",
    ]),
    ("Игривое", [
        "bite", "pinch", "squeeze", "step", "tickle", "poke", "wink",
        "snowball", "nosesnap", "play", "piggyback",
    ]),
    ("Социальное", [
        "handshake", "highfive", "fistbump", "bow", "cheer", "praise",
        "congrats", "dance", "treat", "feed", "givecoffee", "giveflower",
        "teatime", "dateask", "serenade",
    ]),
]


def parse_file(path: Path) -> tuple[dict[str, str], set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    canonical: dict[str, str] = {}
    plus18: set[str] = set()
    for node in ast.walk(tree):
        name: str | None = None
        value = None
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    name = target.id
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            value = node.value
        if name == "_SOCIAL_ACTION_CANONICAL" and value is not None:
            canonical = ast.literal_eval(value)
        elif name == "_SOCIAL_ACTION_18_PLUS" and value is not None:
            plus18 = set(ast.literal_eval(value))
    return canonical, plus18


def build_markdown(canonical: dict[str, str], plus18: set[str]) -> str:
    covered: set[str] = set()
    columns: list[tuple[str, list[str]]] = []

    for title, keys in CATEGORIES:
        rows = [canonical[k] for k in keys if k in canonical]
        covered.update(k for k in keys if k in canonical)
        columns.append((title, rows))

    plus18_rows = [canonical[k] for k in sorted(plus18) if k in canonical]
    covered.update(plus18)
    if plus18_rows:
        columns.append(("18+", plus18_rows))

    leftover = [canonical[k] for k in canonical if k not in covered]
    if leftover:
        columns.append(("Прочее", leftover))

    max_rows = max(len(rows) for _, rows in columns)
    header = "| " + " | ".join(t for t, _ in columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    lines = [header, sep]
    for i in range(max_rows):
        cells = [rows[i] if i < len(rows) else "" for _, rows in columns]
        lines.append("| " + " | ".join(cells) + " |")

    return "# Список действий\n\n" + "\n".join(lines) + "\n"


def main() -> None:
    canonical, plus18 = parse_file(SOURCE)
    md = build_markdown(canonical, plus18)
    OUTPUT.write_text(md, encoding="utf-8")
    total = len(canonical)
    cats = len(CATEGORIES) + (1 if plus18 else 0)
    print(f"Created {OUTPUT} ({total} actions across {cats} categories)")


if __name__ == "__main__":
    main()
