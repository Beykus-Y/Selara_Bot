"""Conservative prose-copy check; shared names, numbers and short labels are valid."""
import re
from html.parser import HTMLParser


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"style", "script"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {"style", "script"}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def reject_copied_prose(pages: list[str], accompanying_text: str) -> None:
    if not accompanying_text.strip():
        return
    words = re.findall(r"\w+", accompanying_text.casefold().replace("ё", "е"))
    if len(words) < 12:
        return
    phrases = {tuple(words[i:i+12]) for i in range(len(words)-11)}
    for page in pages:
        parser = _VisibleText()
        parser.feed(page)
        visual_words = re.findall(r"\w+", " ".join(parser.parts).casefold().replace("ё", "е"))
        if any(tuple(visual_words[i:i+12]) in phrases for i in range(len(visual_words)-11)):
            raise ValueError("Инфографика повторяет текст: 12 слов подряд совпадают. Покажи связи, динамику или соотношения вместо абзацев.")
