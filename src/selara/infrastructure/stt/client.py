from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

log = logging.getLogger(__name__)

# Максимальный размер файла — 25 MB (лимит Whisper API)
_MAX_FILE_SIZE = 25 * 1024 * 1024
# Минимальная длина транскрипта чтобы отфильтровать «шумовые» ответы
_MIN_TEXT_LENGTH = 1
# Таймаут запроса по умолчанию (секунды)
_DEFAULT_TIMEOUT = 30.0


@dataclass(frozen=True, slots=True)
class SttConfig:
    api_key: str
    model: str
    base_url: str | None = None
    timeout_seconds: float = _DEFAULT_TIMEOUT
    language: str = "ru"

    def __post_init__(self) -> None:
        if not self.api_key or not self.api_key.strip():
            raise ValueError("STT_API_KEY не задан.")
        if not self.model or not self.model.strip():
            raise ValueError("STT_MODEL не задан.")
        if self.timeout_seconds <= 0:
            raise ValueError("STT_TIMEOUT_SECONDS должен быть > 0.")


class SttClientError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SttClient:
    """Клиент для распознавания голосовых сообщений через Whisper-совместимый API.

    Работает с OpenAI, Groq и любым другим провайдером с OpenAI-совместимым API.
    Настраивается через env-переменные (см. SttConfig).
    """

    def __init__(self, config: SttConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
        )

    async def transcribe(self, audio_bytes: bytes, *, filename: str = "voice.ogg") -> str:
        """Транскрибировать аудио из байт. Возвращает текст или кидает SttClientError.

        Args:
            audio_bytes: Сырые байты аудиофайла (ogg, mp3, wav, m4a, webm).
            filename: Имя файла с расширением — нужно для определения формата API.

        Raises:
            SttClientError: При любой ошибке (сеть, API, невалидные данные).
        """
        self._validate_audio(audio_bytes, filename)

        log.debug("STT: отправка %d байт, модель=%s", len(audio_bytes), self._config.model)

        try:
            result = await self._client.audio.transcriptions.create(
                model=self._config.model,
                file=(filename, io.BytesIO(audio_bytes)),
                language=self._config.language,
                response_format="text",
            )
        except APITimeoutError as exc:
            raise SttClientError("STT-сервис не ответил вовремя.") from exc
        except APIConnectionError as exc:
            raise SttClientError("Не удалось подключиться к STT-сервису.") from exc
        except APIStatusError as exc:
            raise SttClientError(_extract_api_error(exc)) from exc

        text = result.strip() if isinstance(result, str) else ""

        if len(text) < _MIN_TEXT_LENGTH:
            raise SttClientError("Не удалось распознать речь — аудио слишком тихое или пустое.")

        log.debug("STT: распознано %d символов", len(text))
        return text

    async def transcribe_with_retry(
        self,
        audio_bytes: bytes,
        *,
        filename: str = "voice.ogg",
        retries: int = 2,
        retry_delay: float = 1.0,
    ) -> str:
        """Транскрибировать с автоматическими повторными попытками при сетевых ошибках.

        Повторяет только при APIConnectionError/APITimeoutError, не при ошибках API (4xx/5xx).
        """
        if retries < 0:
            raise ValueError("retries должен быть >= 0.")

        last_error: SttClientError | None = None
        for attempt in range(retries + 1):
            try:
                return await self.transcribe(audio_bytes, filename=filename)
            except SttClientError as exc:
                last_error = exc
                is_last = attempt == retries
                is_retryable = any(
                    keyword in exc.message
                    for keyword in ("не ответил", "подключиться")
                )
                if is_last or not is_retryable:
                    raise
                log.warning("STT: попытка %d/%d не удалась: %s", attempt + 1, retries + 1, exc.message)
                await asyncio.sleep(retry_delay)

        raise last_error  # type: ignore[misc]

    @staticmethod
    def _validate_audio(audio_bytes: bytes, filename: str) -> None:
        if not audio_bytes:
            raise SttClientError("Передан пустой аудиофайл.")

        if len(audio_bytes) > _MAX_FILE_SIZE:
            mb = len(audio_bytes) / (1024 * 1024)
            raise SttClientError(
                f"Файл слишком большой ({mb:.1f} МБ). Максимум — 25 МБ."
            )

        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        allowed = {"ogg", "mp3", "wav", "m4a", "webm", "flac", "mp4", "mpeg", "mpga", "oga"}
        if ext not in allowed:
            raise SttClientError(
                f"Неподдерживаемый формат аудио: .{ext}. "
                f"Допустимые форматы: {', '.join(sorted(allowed))}."
            )


def _extract_api_error(exc: APIStatusError) -> str:
    try:
        body = exc.response.json()
    except Exception:
        body = None

    if isinstance(body, dict):
        error = body.get("error", {})
        if isinstance(error, dict):
            msg = error.get("message", "")
            if msg:
                return f"STT API: {msg}"
        message = body.get("message", "")
        if message:
            return f"STT API: {message}"

    code = exc.status_code
    if code == 401:
        return "STT API: неверный API-ключ."
    if code == 429:
        return "STT API: превышен лимит запросов. Попробуйте позже."
    if code >= 500:
        return "STT API: внутренняя ошибка сервиса."
    return f"STT API вернул ошибку: HTTP {code}."
