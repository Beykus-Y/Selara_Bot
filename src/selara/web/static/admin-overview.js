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
}

const backupError = document.querySelector("[data-admin-backup-error]");
const backupSubmitDefaultText = "Запросить backup";

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
        if (data && data.ok) {
          window.location.reload();
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
