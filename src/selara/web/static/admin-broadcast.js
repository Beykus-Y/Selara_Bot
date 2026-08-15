const initBroadcastComposer = () => {
  const form = document.querySelector("[data-broadcast-form]");
  if (!form || form.dataset.broadcastReady === "true") return;
  form.dataset.broadcastReady = "true";

  const list = form.querySelector("#broadcast-target-list");
  const body = form.querySelector("[data-broadcast-body]");
  const compiledBody = form.querySelector("[data-broadcast-compiled-body]");
  const counter = form.querySelector("[data-broadcast-counter]");
  const mediaModes = Array.from(form.querySelectorAll("[data-broadcast-media-mode]"));
  const photoField = form.querySelector("[data-broadcast-photo-field]");
  const photoInput = form.querySelector("[data-broadcast-photo-input]");
  const photoPicker = photoInput.closest(".broadcast-file-picker");
  const photoName = form.querySelector("[data-broadcast-photo-name]");
  const reactionsToggle = form.querySelector("[data-broadcast-reactions-toggle]");
  const reactionControls = form.querySelector("[data-broadcast-reaction-controls]");
  const reactionList = form.querySelector("[data-broadcast-reaction-list]");
  const addReaction = form.querySelector("[data-broadcast-add-reaction]");
  const targetSearch = form.querySelector("[data-broadcast-target-search]");
  const selectedCount = form.querySelector("[data-broadcast-selected-count]");
  const formError = form.querySelector("[data-broadcast-form-error]");
  const submitButton = form.querySelector("[data-broadcast-submit]");
  const previewMessage = document.querySelector("[data-broadcast-preview-message]");
  const previewEmpty = document.querySelector("[data-broadcast-preview-empty]");
  const previewBody = document.querySelector("[data-broadcast-preview-body]");
  const previewPhoto = document.querySelector("[data-broadcast-preview-photo]");
  const previewReactions = document.querySelector("[data-broadcast-preview-reactions]");
  const previewMode = document.querySelector("[data-broadcast-preview-mode]");
  const previewAudience = document.querySelector("[data-broadcast-preview-audience]");
  const confirmDialog = form.querySelector("[data-broadcast-confirm-dialog]");
  const confirmCount = form.querySelector("[data-broadcast-confirm-count]");
  const confirmPreview = form.querySelector("[data-broadcast-confirm-preview]");
  const confirmCancel = form.querySelector("[data-broadcast-confirm-cancel]");
  const confirmSubmit = form.querySelector("[data-broadcast-confirm-submit]");
  let previewUrl = null;
  let confirmationGranted = false;
  let reactionSequence = reactionList.querySelectorAll("[data-broadcast-reaction-row]").length;

  const chatCheckboxes = () => list
    ? Array.from(list.querySelectorAll('input[type="checkbox"][name="chat_ids"]'))
    : [];
  const targetItems = () => list
    ? Array.from(list.querySelectorAll("[data-broadcast-target-item]"))
    : [];
  const reactionRows = () => Array.from(reactionList.querySelectorAll("[data-broadcast-reaction-row]"));
  const photoModeEnabled = () => mediaModes.some((input) => input.checked && input.value === "photo");

  const escapeHtml = (value) => value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");

  const previewTagNames = new Map([
    ["B", "strong"],
    ["STRONG", "strong"],
    ["I", "em"],
    ["EM", "em"],
    ["U", "u"],
    ["INS", "u"],
    ["S", "s"],
    ["STRIKE", "s"],
    ["DEL", "s"],
    ["CODE", "code"],
    ["PRE", "pre"],
    ["BLOCKQUOTE", "blockquote"],
    ["A", "a"],
    ["TG-SPOILER", "span"],
    ["SPAN", "span"],
    ["BR", "br"],
  ]);
  const previewLinkProtocols = new Set(["http:", "https:", "tg:", "mailto:", "tel:"]);

  const appendSafePreviewNode = (target, sourceNode) => {
    if (sourceNode.nodeType === Node.TEXT_NODE) {
      target.append(document.createTextNode(sourceNode.textContent || ""));
      return;
    }
    if (sourceNode.nodeType !== Node.ELEMENT_NODE) return;
    if (["SCRIPT", "STYLE", "IMG", "SVG", "IFRAME"].includes(sourceNode.tagName)) return;

    const mappedName = previewTagNames.get(sourceNode.tagName);
    if (!mappedName) {
      for (const child of sourceNode.childNodes) appendSafePreviewNode(target, child);
      return;
    }
    if (sourceNode.tagName === "SPAN" && !sourceNode.classList.contains("tg-spoiler")) {
      for (const child of sourceNode.childNodes) appendSafePreviewNode(target, child);
      return;
    }

    const element = document.createElement(mappedName);
    if (sourceNode.tagName === "TG-SPOILER" || sourceNode.classList.contains("tg-spoiler")) {
      element.className = "broadcast-preview-spoiler";
      element.title = "Спойлер";
    }
    if (sourceNode.tagName === "A") {
      const rawHref = sourceNode.getAttribute("href") || "";
      try {
        const parsedHref = new URL(rawHref, window.location.origin);
        if (previewLinkProtocols.has(parsedHref.protocol)) {
          element.href = rawHref;
          element.rel = "noopener noreferrer";
        }
      } catch {
        // An invalid Telegram link stays visible as text without becoming clickable.
      }
    }
    for (const child of sourceNode.childNodes) appendSafePreviewNode(element, child);
    target.append(element);
  };

  const renderSafeTelegramPreview = (target, source) => {
    const parsed = new DOMParser().parseFromString(source, "text/html");
    const fragment = document.createDocumentFragment();
    for (const node of parsed.body.childNodes) appendSafePreviewNode(fragment, node);
    target.replaceChildren(fragment);
  };

  const reactionValues = () => reactionRows().map((row) => ({
    emoji: row.querySelector("[data-broadcast-reaction-emoji]").value.trim(),
    label: row.querySelector("[data-broadcast-reaction-label]").value.trim(),
  }));

  const compileBody = () => {
    const source = body.value.trim();
    if (!reactionsToggle.checked) return { source, rendered: source };
    const values = reactionValues();
    const rows = values.map(({ emoji, label }) => `${emoji} = ${label}`).join("\n");
    const footer = values.map(({ emoji, label }) => `${emoji} — ${escapeHtml(label)}`).join("\n");
    return {
      source: `${source}\n\n[reactions]\n${rows}\n[/reactions]`,
      rendered: `${source}\n\n<b>Реакции:</b>\n${footer}`,
    };
  };

  const formatChatCount = (count) => {
    const remainder100 = count % 100;
    const remainder10 = count % 10;
    if (remainder100 >= 11 && remainder100 <= 14) return `${count} чатов`;
    if (remainder10 === 1) return `${count} чат`;
    if (remainder10 >= 2 && remainder10 <= 4) return `${count} чата`;
    return `${count} чатов`;
  };

  const updatePreview = () => {
    const source = body.value.trim();
    const hasBody = Boolean(source);
    previewEmpty.hidden = hasBody;
    previewBody.hidden = !hasBody;
    if (hasBody) renderSafeTelegramPreview(previewBody, source);
    else previewBody.replaceChildren();

    previewMode.textContent = photoModeEnabled() ? "Фото с подписью" : "Текст";
    previewPhoto.hidden = !photoModeEnabled() || !previewUrl;
    if (previewUrl) previewPhoto.src = previewUrl;
    else previewPhoto.removeAttribute("src");

    previewReactions.replaceChildren();
    if (reactionsToggle.checked) {
      const title = document.createElement("strong");
      title.textContent = "Реакции:";
      previewReactions.append(title);
      for (const { emoji, label } of reactionValues()) {
        const row = document.createElement("span");
        row.textContent = `${emoji} — ${label}`;
        previewReactions.append(row);
      }
      previewReactions.hidden = false;
    } else {
      previewReactions.hidden = true;
    }
  };

  const hideFormError = () => {
    formError.hidden = true;
    formError.textContent = "";
  };

  const showFormError = (message) => {
    formError.textContent = message;
    formError.hidden = false;
    formError.focus({ preventScroll: true });
  };

  const updateCounter = () => {
    const limit = photoModeEnabled() ? 1024 : 3200;
    const remaining = limit - compileBody().rendered.length;
    counter.textContent = `Осталось: ${remaining} из ${limit}`;
    counter.classList.toggle("broadcast-counter-error", remaining < 0);
  };

  const updateSelectedCount = () => {
    const inputs = chatCheckboxes();
    const selected = inputs.filter((input) => input.checked).length;
    selectedCount.textContent = `Выбрано: ${selected} из ${inputs.length}`;
    previewAudience.textContent = `Выбрано чатов: ${selected}`;
  };

  const filterTargets = () => {
    const query = targetSearch.value.trim().toLocaleLowerCase("ru-RU");
    for (const item of targetItems()) {
      item.hidden = Boolean(query) && !item.textContent.toLocaleLowerCase("ru-RU").includes(query);
    }
  };

  const clearPhoto = () => {
    photoInput.value = "";
    photoName.textContent = "Файл не выбран";
    photoPicker.classList.remove("is-selected");
    photoInput.setCustomValidity("");
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = null;
  };

  const updatePhotoMode = () => {
    const enabled = photoModeEnabled();
    photoField.hidden = !enabled;
    photoInput.required = enabled;
    if (!enabled) clearPhoto();
    updateCounter();
    updatePreview();
  };

  const updateReactionControls = () => {
    const enabled = reactionsToggle.checked;
    reactionControls.hidden = !enabled;
    for (const row of reactionRows()) {
      row.querySelector("[data-broadcast-reaction-emoji]").required = enabled;
      row.querySelector("[data-broadcast-reaction-label]").required = enabled;
    }
    updateCounter();
    updatePreview();
  };

  const updateReactionButtons = () => {
    const rows = reactionRows();
    for (const row of rows) {
      row.querySelector("[data-broadcast-remove-reaction]").hidden = rows.length <= 2;
    }
    addReaction.hidden = rows.length >= 6;
  };

  const createReactionRow = () => {
    reactionSequence += 1;
    const emojiId = `broadcast-reaction-emoji-${reactionSequence}`;
    const labelId = `broadcast-reaction-label-${reactionSequence}`;
    const row = document.createElement("div");
    row.className = "broadcast-reaction-row";
    row.dataset.broadcastReactionRow = "";
    row.innerHTML = `
      <label for="${emojiId}">
        <span>Emoji</span>
        <input id="${emojiId}" class="admin-field-input" name="reaction_emoji" value="🤔" maxlength="32" required data-broadcast-reaction-emoji>
      </label>
      <label for="${labelId}">
        <span>Описание</span>
        <input id="${labelId}" class="admin-field-input" name="reaction_label" value="Есть вопросы" maxlength="64" required data-broadcast-reaction-label>
      </label>
      <button type="button" class="button ghost small" data-broadcast-remove-reaction>Удалить</button>
    `;
    reactionList.appendChild(row);
    updateReactionButtons();
    updateCounter();
    updatePreview();
    row.querySelector("[data-broadcast-reaction-emoji]").focus();
  };

  form.querySelectorAll("[data-broadcast-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const checked = button.dataset.broadcastToggle === "all";
      for (const input of chatCheckboxes()) input.checked = checked;
      hideFormError();
      updateSelectedCount();
    });
  });
  mediaModes.forEach((input) => input.addEventListener("change", updatePhotoMode));
  reactionsToggle.addEventListener("change", updateReactionControls);
  targetSearch.addEventListener("input", filterTargets);
  list?.addEventListener("change", () => {
    hideFormError();
    updateSelectedCount();
  });
  body.addEventListener("input", () => {
    body.setCustomValidity("");
    hideFormError();
    updateCounter();
    updatePreview();
  });
  reactionList.addEventListener("input", () => {
    updateCounter();
    updatePreview();
  });
  addReaction.addEventListener("click", () => {
    if (reactionRows().length < 6) createReactionRow();
  });
  reactionList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-broadcast-remove-reaction]");
    if (!button || reactionRows().length <= 2) return;
    button.closest("[data-broadcast-reaction-row]").remove();
    updateReactionButtons();
    updateCounter();
    updatePreview();
  });

  photoInput.addEventListener("change", () => {
    photoInput.setCustomValidity("");
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = null;
    photoPicker.classList.remove("is-selected");
    const file = photoInput.files && photoInput.files[0];
    if (!file) {
      photoName.textContent = "Файл не выбран";
      updatePreview();
      return;
    }
    photoName.textContent = file.name;
    if (!["image/jpeg", "image/png"].includes(file.type)) {
      photoInput.setCustomValidity("Выберите фотографию JPEG или PNG.");
      photoInput.reportValidity();
      updatePreview();
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      photoInput.setCustomValidity("Фотография должна быть не больше 10 МБ.");
      photoInput.reportValidity();
      updatePreview();
      return;
    }
    previewUrl = URL.createObjectURL(file);
    photoPicker.classList.add("is-selected");
    updatePreview();
  });

  const openConfirmation = () => {
    const selected = chatCheckboxes().filter((input) => input.checked).length;
    confirmCount.textContent = formatChatCount(selected);
    confirmPreview.replaceChildren(previewMessage.cloneNode(true));
    if (!confirmDialog.open) confirmDialog.showModal();
    confirmDialog.scrollTop = 0;
    confirmCancel.focus({ preventScroll: true });
  };

  confirmCancel.addEventListener("click", () => {
    confirmationGranted = false;
    confirmDialog.close("cancel");
    submitButton.focus();
  });
  confirmSubmit.addEventListener("click", () => {
    confirmationGranted = true;
    confirmDialog.close("confirm");
    form.requestSubmit(submitButton);
    if (form.dataset.submitting !== "true") confirmationGranted = false;
  });
  confirmDialog.addEventListener("cancel", () => {
    confirmationGranted = false;
  });
  confirmDialog.addEventListener("close", () => {
    if (confirmDialog.returnValue !== "confirm" && form.dataset.submitting !== "true") {
      submitButton.focus();
    }
  });

  form.addEventListener("submit", (event) => {
    hideFormError();
    body.setCustomValidity("");
    if (form.dataset.submitting === "true") {
      event.preventDefault();
      return;
    }
    const source = body.value.trim();
    if (reactionsToggle.checked && (source.includes("[reactions]") || source.includes("[/reactions]"))) {
      event.preventDefault();
      showFormError("Не добавляйте блок [reactions] вручную, когда включён конструктор.");
      body.focus();
      return;
    }
    const values = reactionValues();
    if (reactionsToggle.checked) {
      const emoji = values.map((item) => item.emoji);
      if (new Set(emoji).size !== emoji.length) {
        event.preventDefault();
        showFormError("Emoji в вариантах реакций не должны повторяться.");
        return;
      }
    }
    const compiled = compileBody();
    const limit = photoModeEnabled() ? 1024 : 3200;
    if (compiled.rendered.length > limit) {
      event.preventDefault();
      showFormError(`Итоговый текст длиннее ${limit} символов.`);
      body.focus();
      return;
    }
    if (chatCheckboxes().every((input) => !input.checked)) {
      event.preventDefault();
      showFormError("Выберите хотя бы один чат для рассылки.");
      return;
    }
    compiledBody.value = compiled.source;
    if (!confirmationGranted) {
      event.preventDefault();
      openConfirmation();
      return;
    }
    confirmationGranted = false;
    form.dataset.submitting = "true";
    submitButton.disabled = true;
    submitButton.textContent = "Отправка…";
  });

  updatePhotoMode();
  updateReactionControls();
  updateReactionButtons();
  updateSelectedCount();
  updatePreview();
};

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initBroadcastComposer, { once: true });
} else {
  initBroadcastComposer();
}
