function fallbackCopy(text) {
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  try {
    document.execCommand("copy");
  } finally {
    textarea.remove();
  }
}

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  fallbackCopy(text);
}

function showFeedback(button) {
  const original = button.dataset.originalLabel || button.textContent;
  button.dataset.originalLabel = original;
  button.textContent = "Скопировано";
  button.classList.add("is-copied");
  window.clearTimeout(button._copyResetTimer);
  button._copyResetTimer = window.setTimeout(() => {
    button.textContent = original;
    button.classList.remove("is-copied");
  }, 1500);
}

document.addEventListener("click", (event) => {
  const button = event.target.closest(".docs-clip-button");
  if (!button) {
    return;
  }
  const text = button.dataset.copyText || "";
  copyText(text)
    .then(() => showFeedback(button))
    .catch(() => {
      button.textContent = "Не скопировалось";
      window.setTimeout(() => {
        button.textContent = button.dataset.originalLabel || "Копировать";
      }, 1500);
    });
});
