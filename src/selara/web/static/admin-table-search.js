const searchInput = document.querySelector("[data-table-search-input]");
const emptyMessage = document.querySelector("[data-table-search-empty]");

if (searchInput instanceof HTMLInputElement) {
  searchInput.addEventListener("input", () => {
    const query = searchInput.value.trim().toLowerCase();
    let visibleCount = 0;

    for (const section of document.querySelectorAll("[data-table-search-section]")) {
      let sectionVisible = 0;
      for (const card of section.querySelectorAll("[data-table-search-card]")) {
        const matches = !query || (card.dataset.tableSearchText || "").includes(query);
        card.hidden = !matches;
        if (matches) sectionVisible += 1;
      }
      section.hidden = sectionVisible === 0;
      visibleCount += sectionVisible;

      // A collapsed section shouldn't hide a match the user just searched for.
      const details = section.querySelector("[data-table-search-details]");
      if (details instanceof HTMLDetailsElement && query && sectionVisible > 0) {
        details.open = true;
      }
    }

    if (emptyMessage instanceof HTMLElement) {
      emptyMessage.hidden = visibleCount !== 0;
    }
  });
}
