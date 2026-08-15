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

if (backupForm instanceof HTMLFormElement) {
  backupForm.addEventListener("submit", () => {
    const submitButton = backupForm.querySelector("[data-admin-backup-submit]");
    if (submitButton instanceof HTMLButtonElement) {
      submitButton.disabled = true;
      submitButton.textContent = "Формируем backup…";
    }
  });
}
