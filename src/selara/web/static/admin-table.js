const deleteDialog = document.getElementById("delete-dialog");
const deleteForm = document.getElementById("delete-form");
const deleteHiddenFields = document.getElementById("delete-hidden-fields");
const deleteTarget = deleteDialog?.querySelector("[data-delete-target]");
const deleteCancelButton = deleteDialog?.querySelector("[data-delete-cancel]");
let deleteTrigger = null;

function restoreDeleteTriggerFocus() {
  if (deleteTrigger instanceof HTMLElement && deleteTrigger.isConnected) {
    deleteTrigger.focus();
  }
  deleteTrigger = null;
}

if (deleteDialog instanceof HTMLDialogElement && deleteForm instanceof HTMLFormElement) {
  document.querySelectorAll("[data-delete-url]").forEach((button) => {
    button.addEventListener("click", () => {
      const url = button.getAttribute("data-delete-url") || "";
      const [action, query] = url.split("?");
      const params = new URLSearchParams(query || "");

      if (deleteHiddenFields) {
        deleteHiddenFields.replaceChildren();
        for (const [key, value] of params.entries()) {
          const input = document.createElement("input");
          input.type = "hidden";
          input.name = key;
          input.value = value;
          deleteHiddenFields.appendChild(input);
        }
      }

      deleteForm.action = action;
      if (deleteTarget) {
        deleteTarget.textContent = button.getAttribute("data-delete-label") || "";
      }

      deleteTrigger = button;
      deleteDialog.showModal();
      if (deleteCancelButton instanceof HTMLButtonElement) {
        deleteCancelButton.focus();
      }
    });
  });

  if (deleteCancelButton instanceof HTMLButtonElement) {
    deleteCancelButton.addEventListener("click", () => deleteDialog.close());
  }

  deleteDialog.addEventListener("click", (event) => {
    if (event.target === deleteDialog) {
      deleteDialog.close();
    }
  });

  deleteDialog.addEventListener("close", restoreDeleteTriggerFocus);
}

const editForm = document.querySelector("[data-admin-edit-form]");
if (editForm instanceof HTMLFormElement) {
  editForm.addEventListener("submit", () => {
    const submitButton = editForm.querySelector("[data-admin-edit-submit]");
    if (submitButton instanceof HTMLButtonElement) {
      submitButton.disabled = true;
      submitButton.textContent = "Сохраняем…";
    }
  });
}
