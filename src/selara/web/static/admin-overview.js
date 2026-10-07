const dialog = document.querySelector("[data-admin-backup-dialog]");
const openButton = document.querySelector("[data-admin-backup-open]");
const cancelButton = document.querySelector("[data-admin-backup-cancel]");
const backupForm = document.querySelector("[data-admin-backup-form]");

function restoreBackupTriggerFocus() {
  if (openButton instanceof HTMLElement && openButton.isConnected) {
    openButton.focus();
  }
}

if (dialog instanceof HTMLDialogElement && openButton instanceof HTMLButtonElement) {
  openButton.addEventListener("click", () => {
    if (!dialog.open) {
      dialog.showModal();
    }
    if (cancelButton instanceof HTMLButtonElement) {
      cancelButton.focus();
    }
    refreshBackupStatus();
  });

  if (cancelButton instanceof HTMLButtonElement) {
    cancelButton.addEventListener("click", () => dialog.close());
  }

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      dialog.close();
    }
  });
  dialog.addEventListener("close", restoreBackupTriggerFocus);
  dialog.addEventListener("close", () => window.clearTimeout(backupStatusTimer));
}

const backupError = document.querySelector("[data-admin-backup-error]");
const backupSubmitDefaultText = "Запросить backup";
const backupStatusText = document.querySelector("[data-admin-backup-status]");
const backupStatusPollMs = 3000;
let backupStatusTimer = null;

function setBackupStatusText(text) {
  if (backupStatusText instanceof HTMLElement) {
    backupStatusText.textContent = text;
    backupStatusText.hidden = !text;
  }
}

function formatBackupTime(value) {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}

function describeBackupStatus(backup) {
  const started = formatBackupTime(backup.started_at);
  const finished = formatBackupTime(backup.finished_at);
  if (backup.status === "running") {
    return started ? `Backup выполняется с ${started}.` : "Backup выполняется.";
  }
  if (backup.status === "completed") {
    return `Последний backup отправлен ${finished}.`;
  }
  const reason = backup.error ? ` ${backup.error}` : "";
  return `Последний backup не завершён (${finished || started}).${reason}`;
}

function fetchBackupStatus() {
  return fetch("/api/admin/backup-status", {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  })
    .then(async (response) => {
      const data = await response.json().catch(() => null);
      return data && data.ok && data.backup ? data.backup : null;
    })
    .catch(() => null);
}

function currentBackupSubmitButton() {
  return backupForm instanceof HTMLFormElement ? backupForm.querySelector("[data-admin-backup-submit]") : null;
}

function resetBackupSubmit(submitButton) {
  if (submitButton instanceof HTMLButtonElement) {
    submitButton.disabled = false;
    submitButton.textContent = backupSubmitDefaultText;
  }
}

function refreshBackupStatus() {
  fetchBackupStatus().then((backup) => {
    if (backup && backup.status === "running") {
      watchBackupJob(currentBackupSubmitButton());
      return;
    }
    setBackupStatusText(backup && backup.status !== "idle" ? describeBackupStatus(backup) : "");
    resetBackupSubmit(currentBackupSubmitButton());
  });
}

function showBackupFailure(message, submitButton) {
  setBackupStatusText("");
  if (backupError instanceof HTMLElement) {
    backupError.textContent = message;
    backupError.hidden = false;
    backupError.focus({ preventScroll: true });
  }
  resetBackupSubmit(submitButton);
}

// The server runs the dump in the background. The dialog follows the job only while it is open:
// closing it stops polling, and reopening it picks the job up again. The page is never reloaded here,
// so input elsewhere on the admin screen survives a finished backup.
function watchBackupJob(submitButton) {
  window.clearTimeout(backupStatusTimer);
  setBackupStatusText("Backup выполняется…");
  fetchBackupStatus().then((backup) => {
    if (!backup) {
      showBackupFailure("Не удалось получить статус backup. Обновите страницу позже.", submitButton);
      return;
    }
    if (backup.status === "running") {
      if (dialog instanceof HTMLDialogElement && dialog.open) {
        backupStatusTimer = window.setTimeout(() => watchBackupJob(submitButton), backupStatusPollMs);
      }
      return;
    }
    if (backup.status === "completed") {
      setBackupStatusText(describeBackupStatus(backup));
      resetBackupSubmit(submitButton);
      return;
    }
    showBackupFailure(backup.error || "Не удалось отправить backup. Проверьте логи и конфиг.", submitButton);
  });
}

if (backupForm instanceof HTMLFormElement) {
  backupForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (backupError instanceof HTMLElement) {
      backupError.hidden = true;
      backupError.textContent = "";
    }

    const submitButton = backupForm.querySelector("[data-admin-backup-submit]");
    if (submitButton instanceof HTMLButtonElement) {
      submitButton.disabled = true;
      submitButton.textContent = "Формируем backup…";
    }

    fetch("/api/admin/request-backup", {
      method: "POST",
      body: new FormData(backupForm),
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(async (response) => {
        const data = await response.json().catch(() => null);
        if (response.status === 202 || response.status === 409) {
          watchBackupJob(submitButton);
          return;
        }
        if (response.status === 401 && data && data.redirect) {
          window.location.href = data.redirect;
          return;
        }
        if (backupError instanceof HTMLElement) {
          backupError.textContent = (data && data.message) || "Не удалось отправить backup.";
          backupError.hidden = false;
          backupError.focus({ preventScroll: true });
        }
        if (submitButton instanceof HTMLButtonElement) {
          submitButton.disabled = false;
          submitButton.textContent = backupSubmitDefaultText;
        }
      })
      .catch(() => {
        if (backupError instanceof HTMLElement) {
          backupError.textContent = "Сеть недоступна. Проверьте соединение и попробуйте ещё раз.";
          backupError.hidden = false;
          backupError.focus({ preventScroll: true });
        }
        if (submitButton instanceof HTMLButtonElement) {
          submitButton.disabled = false;
          submitButton.textContent = backupSubmitDefaultText;
        }
      });
  });
}
