def build_number_start_text() -> str:
    return (
        "<b>Ведущий:</b> Игра «Угадай число» началась.\n"
        "Я загадал число от <code>1</code> до <code>100</code>. "
        "Пишите варианты отдельными сообщениями в чат."
    )


def number_distance_hint(distance: int) -> str:
    if distance <= 2:
        return "🔥 очень близко"
    if distance <= 5:
        return "♨️ горячо"
    if distance <= 10:
        return "🌤 тепло"
    if distance <= 20:
        return "❄️ прохладно"
    return "🧊 холодно"
