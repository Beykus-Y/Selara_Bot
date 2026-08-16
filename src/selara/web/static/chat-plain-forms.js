const plainForms = [
  ...document.querySelectorAll('form[action*="/aliases"], form[action*="/triggers"]'),
];

for (const form of plainForms) {
  if (!(form instanceof HTMLFormElement)) {
    continue;
  }

  form.addEventListener("submit", (event) => {
    const submitter = event.submitter;
    const confirmMessage = submitter?.getAttribute("data-confirm-delete");
    if (confirmMessage && !window.confirm(confirmMessage)) {
      event.preventDefault();
      return;
    }
    for (const button of form.querySelectorAll("button[type='submit']")) {
      button.disabled = true;
    }
  });
}
