for (const form of document.querySelectorAll("[data-feedback-status-form]")) {
  const errorTarget = form.querySelector("[data-feedback-item-error]");
  const apiUrl = form.dataset.feedbackStatusApi;

  form.addEventListener("submit", (event) => {
    if (!apiUrl) {
      return;
    }
    event.preventDefault();
    if (errorTarget) {
      errorTarget.hidden = true;
      errorTarget.textContent = "";
    }

    const button = form.querySelector("button[type='submit']");
    const defaultText = button instanceof HTMLButtonElement ? button.textContent : "";
    if (button instanceof HTMLButtonElement) {
      button.disabled = true;
      button.textContent = "Обновляем…";
    }

    fetch(apiUrl, {
      method: "POST",
      body: new FormData(form),
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
        if (errorTarget) {
          errorTarget.textContent = (data && data.message) || "Не удалось обновить статус заявки.";
          errorTarget.hidden = false;
          errorTarget.focus({ preventScroll: true });
        }
        if (button instanceof HTMLButtonElement) {
          button.disabled = false;
          button.textContent = defaultText;
        }
      })
      .catch(() => {
        if (errorTarget) {
          errorTarget.textContent = "Сеть недоступна. Проверьте соединение и попробуйте ещё раз.";
          errorTarget.hidden = false;
          errorTarget.focus({ preventScroll: true });
        }
        if (button instanceof HTMLButtonElement) {
          button.disabled = false;
          button.textContent = defaultText;
        }
      });
  });
}
