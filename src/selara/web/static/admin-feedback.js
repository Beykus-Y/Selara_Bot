for (const form of document.querySelectorAll("[data-feedback-status-form]")) {
  form.addEventListener("submit", () => {
    const button = form.querySelector("button[type='submit']");
    if (button instanceof HTMLButtonElement) {
      button.disabled = true;
      button.textContent = "Обновляем…";
    }
  });
}
