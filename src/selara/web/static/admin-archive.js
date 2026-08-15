const MAX_PREVIEW_BYTES = 10 * 1024 * 1024;

function setPreviewState(preview, state, message) {
  preview.dataset.state = state;
  preview.toggleAttribute("aria-busy", state === "loading");
  const status = preview.querySelector("[data-archive-photo-status]");
  if (status) {
    status.textContent = message;
  }
}

function previewErrorMessage(response) {
  if (response.status === 401) {
    return "Сессия истекла. Обновите страницу и войдите снова.";
  }
  if (response.status === 413) {
    return "Фото больше лимита preview в 10 МБ.";
  }
  return "Не удалось загрузить фото из Telegram. Возможно, файл уже недоступен.";
}

async function loadArchivePhoto(preview, button) {
  if (preview.dataset.state === "loading") {
    return;
  }

  const existingFrame = preview.querySelector("[data-archive-photo-frame]");
  if (preview.dataset.state === "loaded" && existingFrame) {
    existingFrame.hidden = true;
    button.textContent = "Показать фото";
    setPreviewState(preview, "collapsed", "Фото скрыто. Повторная загрузка не потребуется.");
    return;
  }
  if (preview.dataset.state === "collapsed" && existingFrame) {
    existingFrame.hidden = false;
    button.textContent = "Скрыть фото";
    setPreviewState(preview, "loaded", "Фото загружено");
    return;
  }

  const previewUrl = button.dataset.previewUrl;
  if (!previewUrl) {
    setPreviewState(preview, "error", "Ссылка на preview отсутствует.");
    return;
  }

  button.disabled = true;
  button.textContent = "Загрузка…";
  setPreviewState(preview, "loading", "Загружаем защищённый preview…");

  let objectUrl;
  try {
    const response = await fetch(previewUrl, {
      cache: "force-cache",
      credentials: "same-origin",
      headers: { Accept: "image/*" },
    });
    if (!response.ok) {
      throw new Error(previewErrorMessage(response));
    }
    const contentType = response.headers.get("content-type") || "";
    const blob = await response.blob();
    if (!contentType.startsWith("image/") || !blob.type.startsWith("image/")) {
      throw new Error("Сервер вернул файл неизвестного типа.");
    }
    if (blob.size > MAX_PREVIEW_BYTES) {
      throw new Error("Фото больше лимита preview в 10 МБ.");
    }

    objectUrl = URL.createObjectURL(blob);
    const image = new Image();
    image.alt = button.dataset.previewAlt || "Фото из архивного сообщения";
    image.decoding = "async";
    image.src = objectUrl;
    await image.decode();

    const frame = preview.querySelector("[data-archive-photo-frame]");
    if (!frame) {
      throw new Error("Контейнер preview недоступен.");
    }
    frame.replaceChildren(image);
    frame.hidden = false;
    button.disabled = false;
    button.textContent = "Скрыть фото";
    setPreviewState(preview, "loaded", "Фото загружено");
  } catch (error) {
    button.disabled = false;
    button.textContent = "Повторить загрузку";
    setPreviewState(
      preview,
      "error",
      error instanceof Error ? error.message : "Не удалось загрузить фото.",
    );
  } finally {
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
    }
  }
}

for (const preview of document.querySelectorAll("[data-archive-photo-preview]")) {
  const button = preview.querySelector("[data-archive-photo-load]");
  if (button) {
    button.addEventListener("click", () => loadArchivePhoto(preview, button));
  }
}

function scrollToHighlightedArchiveMessage() {
  const list = document.querySelector("[data-archive-highlight-target]");
  if (!list) {
    return;
  }
  const targetId = list.dataset.archiveHighlightTarget;
  const target = document.getElementById(`archive-msg-${targetId}`);
  if (!target) {
    return;
  }
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  target.scrollIntoView({ block: "center", behavior: reducedMotion ? "auto" : "smooth" });
  target.classList.add("is-jump-target");
  target.focus({ preventScroll: true });
  window.setTimeout(() => {
    target.classList.remove("is-jump-target");
  }, 2600);
}

scrollToHighlightedArchiveMessage();
