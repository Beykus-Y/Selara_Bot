// Follows an admin-requested backup while it runs. admin-overview.js dispatches "backup-accepted" on the
// form once the server has started the job; this module owns the status line and polling.
const dialog = document.querySelector("[data-admin-backup-dialog]");
const openButton = document.querySelector("[data-admin-backup-open]");
const backupForm = document.querySelector("[data-admin-backup-form]");
const backupError = document.querySelector("[data-admin-backup-error]");
const backupStatusText = document.querySelector("[data-admin-backup-status]");
const backupSubmitDefaultText = "Запросить backup";
const backupStatusPollMs = 3000;
let backupStatusTimer = null;
// True while this page is following a job it started, so the submit button is ours to re-enable.
let watchActive = false;

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

function resetBackupSubmit(submitButton) {
  watchActive = false;
  if (submitButton instanceof HTMLButtonElement) {
    submitButton.disabled = false;
    submitButton.textContent = backupSubmitDefaultText;
  }
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

// Polling runs only while the dialog is open: closing it stops polling, and reopening it picks the job up
// again. The page is never reloaded here, so input elsewhere on the admin screen survives a finished backup.
function watchBackupJob(submitButton) {
  window.clearTimeout(backupStatusTimer);
  watchActive = true;
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

function refreshBackupStatus() {
  const submitButton = backupForm instanceof HTMLFormElement ? backupForm.querySelector("[data-admin-backup-submit]") : null;
  fetchBackupStatus().then((backup) => {
    if (backup && backup.status === "running") {
      watchBackupJob(submitButton);
      return;
    }
    setBackupStatusText(backup && backup.status !== "idle" ? describeBackupStatus(backup) : "");
    // Re-enable only a button this page disabled while watching a job that ended while the dialog was closed.
    if (watchActive) {
      resetBackupSubmit(submitButton);
    }
  });
}

if (openButton instanceof HTMLButtonElement) {
  openButton.addEventListener("click", refreshBackupStatus);
}
if (dialog instanceof HTMLDialogElement) {
  dialog.addEventListener("close", () => window.clearTimeout(backupStatusTimer));
}
if (backupForm instanceof HTMLFormElement) {
  backupForm.addEventListener("backup-accepted", (event) => watchBackupJob(event.detail));
}
