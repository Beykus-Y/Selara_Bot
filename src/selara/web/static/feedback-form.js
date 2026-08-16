const feedbackForm = document.querySelector("[data-feedback-form]");

if (feedbackForm instanceof HTMLFormElement) {
  feedbackForm.addEventListener("submit", () => {
    const submitButton = feedbackForm.querySelector("[data-feedback-submit]");
    if (submitButton instanceof HTMLButtonElement) {
      submitButton.disabled = true;
      submitButton.textContent = "Отправляем…";
    }
  });
}
