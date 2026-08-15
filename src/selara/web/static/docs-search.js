const searchInput = document.querySelector("[data-docs-search-input]");
const emptyMessage = document.querySelector("[data-docs-search-empty]");

if (searchInput instanceof HTMLInputElement) {
  searchInput.addEventListener("input", () => {
    const query = searchInput.value.trim().toLowerCase();
    let visibleCount = 0;

    for (const section of document.querySelectorAll("[data-docs-search-section]")) {
      let sectionVisible = 0;
      for (const card of section.querySelectorAll("[data-docs-search-card]")) {
        const matches = !query || (card.dataset.docsSearchText || "").includes(query);
        card.hidden = !matches;
        if (matches) sectionVisible += 1;
      }
      section.hidden = sectionVisible === 0;
      visibleCount += sectionVisible;
    }

    if (emptyMessage instanceof HTMLElement) {
      emptyMessage.hidden = visibleCount !== 0;
    }
  });
}
