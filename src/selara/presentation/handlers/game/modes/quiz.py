def build_quiz_start_text(*, question_no: int = 1) -> str:
    return (
        "<b>Ведущий:</b> Викторина началась.\n"
        f"Вопрос {question_no} уже на доске. Выбирайте ответ кнопками под сообщением игры."
    )
