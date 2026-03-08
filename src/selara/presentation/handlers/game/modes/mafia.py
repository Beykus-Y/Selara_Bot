def build_mafia_start_text(*, round_no: int, night_seconds: int) -> str:
    return (
        f"<b>Ведущий:</b> Мини-мафия началась. Ночь {round_no}.\n"
        f"Ночные роли уже получили ЛС-карточки. У стола {night_seconds} сек. до рассвета."
    )
