const loginForm = document.querySelector("[data-login-form]");

if (loginForm instanceof HTMLFormElement) {
  loginForm.addEventListener("submit", () => {
    const submitButton = loginForm.querySelector("[data-login-submit]");
    if (submitButton instanceof HTMLButtonElement) {
      submitButton.disabled = true;
      submitButton.textContent = "Проверяем…";
    }
  });
}
