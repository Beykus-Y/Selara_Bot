(() => {
  const forms = [...document.querySelectorAll(".setting-form, .setting-reset")];
  const settingCards = [...document.querySelectorAll("[data-setting-card]")];
  const docsLinks = [...document.querySelectorAll("[data-docs-link]")];
  if (!forms.length && !docsLinks.length) {
    return;
  }

  const toastRegion = document.getElementById("toast-region");
  const docsDialog = document.getElementById("docs-guard-dialog");
  const docsChangeList = docsDialog?.querySelector("[data-docs-change-list]");
  const saveAndGoButton = docsDialog?.querySelector("[data-docs-save-and-go]");
  const discardAndGoButton = docsDialog?.querySelector("[data-docs-discard-and-go]");
  const cancelDocsButton = docsDialog?.querySelector("[data-docs-cancel]");
  let pendingDocsHref = "";
  let allowUnload = false;

  const showToast = (message, tone = "ok") => {
    if (!toastRegion || !message) {
      return;
    }
    const toast = document.createElement("div");
    toast.className = "toast";
    toast.dataset.tone = tone;
    toast.textContent = message;
    toastRegion.appendChild(toast);
    window.setTimeout(() => {
      toast.classList.add("is-hiding");
    }, 2400);
    window.setTimeout(() => {
      toast.remove();
    }, 2800);
  };

  const getEditableField = (card) => card?.querySelector(".setting-form [name='value']");

  const bindToggleControl = (card) => {
    const toggle = card?.querySelector("[data-setting-toggle]");
    const hidden = getEditableField(card);
    const label = card?.querySelector("[data-toggle-label]");
    if (!(toggle instanceof HTMLInputElement) || !(hidden instanceof HTMLInputElement)) {
      return;
    }
    const sync = () => {
      hidden.value = toggle.checked ? "true" : "false";
      if (label) {
        label.textContent = toggle.checked ? "Включено" : "Выключено";
      }
      syncDirtyState(card);
    };
    toggle.addEventListener("change", sync);
    sync();
  };

  const normalizeValue = (field, value) => {
    const normalized = String(value ?? "");
    if (field instanceof HTMLSelectElement) {
      return normalized.toLowerCase();
    }
    return normalized;
  };

  const usesSavedValueDataset = (card) => card.dataset.settingSavedValue !== undefined;

  const getSavedValue = (card) => {
    const field = getEditableField(card);
    if (!field) {
      return "";
    }
    if (usesSavedValueDataset(card)) {
      // Hidden <input> fields (used for toggle-kind settings) keep .value and
      // .defaultValue in sync in real browsers, unlike text/textarea inputs,
      // so dirty-state can't rely on defaultValue there — same as selects.
      return card.dataset.settingSavedValue || normalizeValue(field, field.value);
    }
    return field.defaultValue;
  };

  const getCurrentValue = (card) => {
    const field = getEditableField(card);
    if (!field) {
      return "";
    }
    return normalizeValue(field, field.value);
  };

  const summarizeValue = (value) => {
    const compact = String(value ?? "").replace(/\s+/g, " ").trim();
    if (!compact) {
      return "пусто";
    }
    if (compact.length <= 84) {
      return compact;
    }
    return `${compact.slice(0, 81)}...`;
  };

  const isDirty = (card) => {
    const field = getEditableField(card);
    if (!field) {
      return false;
    }
    return getCurrentValue(card) !== getSavedValue(card);
  };

  const syncDirtyState = (card) => {
    if (!card) {
      return;
    }
    card.classList.toggle("is-dirty", isDirty(card));
  };

  const collectDirtyCards = () => settingCards.filter((card) => isDirty(card));

  const renderDirtyChanges = (cards) => {
    if (!docsChangeList) {
      return;
    }
    docsChangeList.innerHTML = "";

    for (const card of cards) {
      const item = document.createElement("div");
      item.className = "docs-guard-item";

      const title = document.createElement("strong");
      title.textContent = card.dataset.settingTitle || card.dataset.settingKey || "Настройка";

      const meta = document.createElement("p");
      meta.textContent = `${card.dataset.settingKey || "key"}: ${summarizeValue(getSavedValue(card))} -> ${summarizeValue(getCurrentValue(card))}`;

      item.appendChild(title);
      item.appendChild(meta);
      docsChangeList.appendChild(item);
    }
  };

  const setDialogPending = (pending) => {
    if (!docsDialog) {
      return;
    }
    docsDialog.classList.toggle("is-saving", pending);
    for (const button of docsDialog.querySelectorAll("button")) {
      button.disabled = pending;
    }
  };

  const setPending = (card, pending) => {
    if (!card) {
      return;
    }
    card.classList.toggle("is-saving", pending);
    for (const button of card.querySelectorAll("button")) {
      button.disabled = pending;
    }
  };

  const applySettingState = (card, setting) => {
    if (!card || !setting) {
      return;
    }

    const currentDisplay = setting.current_value_display ?? setting.current_value;
    const defaultDisplay = setting.default_value_display ?? setting.default_value;

    const currentValue = card.querySelector("[data-setting-current]");
    if (currentValue) {
      currentValue.textContent = currentDisplay;
    }

    const defaultValue = card.querySelector("[data-setting-default]");
    if (defaultValue) {
      defaultValue.textContent = defaultDisplay;
    }

    const status = card.querySelector(".setting-status");
    if (status) {
      status.textContent = currentDisplay;
    }

    const input = getEditableField(card);
    if (!input) {
      return;
    }

    if (input instanceof HTMLSelectElement) {
      input.value = String(setting.current_value).toLowerCase();
      card.dataset.settingSavedValue = String(setting.current_value).toLowerCase();
    } else {
      input.value = setting.current_value;
      input.defaultValue = setting.current_value;
      if (usesSavedValueDataset(card)) {
        card.dataset.settingSavedValue = String(setting.current_value).toLowerCase();
      }
    }

    const toggle = card.querySelector("[data-setting-toggle]");
    if (toggle instanceof HTMLInputElement) {
      const enabled = String(setting.current_value).toLowerCase() === "true";
      toggle.checked = enabled;
    }

    syncDirtyState(card);
  };

  const sendSettingRequest = async ({ card, actionUrl, payload }) => {
    if (!actionUrl) {
      return { ok: false, data: null };
    }
    setPending(card, true);

    try {
      const response = await fetch(actionUrl, {
        method: "POST",
        headers: {
          "Accept": "application/json",
          "X-Requested-With": "fetch"
        },
        credentials: "same-origin",
        body: payload
      });
      const data = await response.json().catch(() => null);
      return {
        ok: Boolean(response.ok && data && data.ok),
        data,
      };
    } catch {
      return { ok: false, data: null };
    } finally {
      setPending(card, false);
    }
  };

  const saveDirtyCards = async () => {
    const dirtyCards = collectDirtyCards();
    for (const card of dirtyCards) {
      const form = card.querySelector(".setting-form");
      const field = getEditableField(card);
      if (!(form instanceof HTMLFormElement) || !field) {
        continue;
      }

      const payload = new URLSearchParams();
      payload.set("key", card.dataset.settingKey || "");
      payload.set("value", field.value);

      const result = await sendSettingRequest({
        card,
        actionUrl: form.action,
        payload,
      });
      if (!result.ok || !result.data) {
        showToast(result.data?.message || "Не удалось сохранить часть настроек.", "error");
        if (result.data?.redirect) {
          allowUnload = true;
          window.setTimeout(() => {
            window.location.assign(result.data.redirect);
          }, 250);
        }
        return false;
      }

      applySettingState(card, result.data.setting);
    }
    return true;
  };

  const openDocsDialog = (href) => {
    if (!docsDialog || typeof docsDialog.showModal !== "function") {
      const summary = collectDirtyCards()
        .map((card) => `${card.dataset.settingTitle || card.dataset.settingKey}: ${summarizeValue(getSavedValue(card))} -> ${summarizeValue(getCurrentValue(card))}`)
        .join("\n");
      const saveFirst = window.confirm(
        `Есть несохранённые настройки:\n\n${summary}\n\nНажмите OK, чтобы сохранить их и перейти в документацию. Нажмите Cancel, чтобы выбрать переход без сохранения или остаться.`
      );
      if (saveFirst) {
        saveDirtyCards().then((saved) => {
          if (!saved) {
            return;
          }
          allowUnload = true;
          window.location.assign(href);
        });
        return;
      }
      const discardChanges = window.confirm(
        `Открыть документацию без сохранения?\n\nИзменения будут потеряны:\n\n${summary}`
      );
      if (discardChanges) {
        allowUnload = true;
        window.location.assign(href);
      }
      return;
    }

    pendingDocsHref = href;
    renderDirtyChanges(collectDirtyCards());
    docsDialog.showModal();
  };

  for (const card of settingCards) {
    bindToggleControl(card);
    const field = getEditableField(card);
    if (!field) {
      continue;
    }
    field.addEventListener("input", () => syncDirtyState(card));
    field.addEventListener("change", () => syncDirtyState(card));
    syncDirtyState(card);
  }

  for (const form of forms) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();

      const card = form.closest("[data-setting-card]");
      const payload = new URLSearchParams(new FormData(form));
      const result = await sendSettingRequest({
        card,
        actionUrl: form.action,
        payload,
      });

      if (!result.ok || !result.data) {
        showToast(result.data?.message || "Не удалось сохранить настройку.", "error");
        if (result.data?.redirect) {
          allowUnload = true;
          window.setTimeout(() => {
            window.location.assign(result.data.redirect);
          }, 250);
        }
        return;
      }

      applySettingState(card, result.data.setting);
      showToast(result.data.message || "Сохранено.", "ok");
    });
  }

  for (const link of docsLinks) {
    link.addEventListener("click", (event) => {
      const href = link.getAttribute("href");
      if (!href) {
        return;
      }
      const dirtyCards = collectDirtyCards();
      if (!dirtyCards.length) {
        allowUnload = true;
        return;
      }
      event.preventDefault();
      openDocsDialog(href);
    });
  }

  if (saveAndGoButton) {
    saveAndGoButton.addEventListener("click", async () => {
      if (!pendingDocsHref) {
        return;
      }
      setDialogPending(true);
      const saved = await saveDirtyCards();
      setDialogPending(false);
      if (!saved) {
        return;
      }
      allowUnload = true;
      docsDialog?.close();
      window.location.assign(pendingDocsHref);
    });
  }

  if (discardAndGoButton) {
    discardAndGoButton.addEventListener("click", () => {
      if (!pendingDocsHref) {
        return;
      }
      allowUnload = true;
      docsDialog?.close();
      window.location.assign(pendingDocsHref);
    });
  }

  if (cancelDocsButton) {
    cancelDocsButton.addEventListener("click", () => {
      docsDialog?.close();
    });
  }

  docsDialog?.addEventListener("close", () => {
    pendingDocsHref = "";
    setDialogPending(false);
  });

  window.addEventListener("beforeunload", (event) => {
    if (allowUnload || !collectDirtyCards().length) {
      return;
    }
    event.preventDefault();
    event.returnValue = "";
  });
})();
