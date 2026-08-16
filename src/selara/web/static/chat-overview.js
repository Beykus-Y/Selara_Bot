(() => {
  const root = document.querySelector("[data-chat-overview-root]");
  if (!root) {
    return;
  }

  const chatId = root.dataset.chatId;
  const activityChart = document.querySelector("[data-chat-activity-chart]");
  const heroCard = document.querySelector("[data-hero-of-day]");
  const richestCard = document.querySelector("[data-richest-of-day]");
  const status = root.querySelector("[data-lb-status]");
  const tableBody = root.querySelector("[data-lb-body]");
  const pager = root.querySelector("[data-lb-pager]");
  const searchForm = root.querySelector("[data-lb-search-form]");
  const searchInput = root.querySelector("[data-lb-search]");
  const findMeButton = root.querySelector("[data-lb-find-me]");
  const modeButtons = [...root.querySelectorAll("[data-lb-mode]")];
  let liveSource = null;
  let refreshTimer = 0;
  let searchTimer = 0;
  const state = {
    mode: "mix",
    page: 1,
    query: "",
  };

  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (symbol) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;",
  }[symbol] || symbol));

  const fetchJson = async (url) => {
    const response = await fetch(url, {
      headers: {
        "Accept": "application/json",
        "X-Requested-With": "fetch",
      },
      credentials: "same-origin",
    });
    const data = await response.json().catch(() => null);
    if (!response.ok || !data?.ok) {
      if (data?.redirect) {
        window.location.assign(data.redirect);
      }
      throw new Error(data?.message || "Не удалось загрузить данные.");
    }
    return data;
  };

  const renderActivityChart = (series) => {
    if (!activityChart) {
      return;
    }
    if (!Array.isArray(series) || !series.length) {
      activityChart.innerHTML = '<p class="empty-text">Пока нет данных по активности.</p>';
      return;
    }
    const maxValue = Math.max(...series.map((item) => Number(item.messages) || 0), 1);
    activityChart.innerHTML = series.map((item) => {
      const value = Number(item.messages) || 0;
      const height = Math.max(10, Math.round((value / maxValue) * 100));
      return `
        <div class="activity-bar">
          <span class="activity-bar-fill" style="height:${height}%"></span>
          <strong>${value}</strong>
          <small>${escapeHtml(item.label)}</small>
        </div>
      `;
    }).join("");
  };

  const renderHeroCard = (target, payload, emptyText, formatter) => {
    if (!target) {
      return;
    }
    if (!payload) {
      target.innerHTML = `<p class="empty-text">${escapeHtml(emptyText)}</p>`;
      return;
    }
    target.innerHTML = formatter(payload);
  };

  const renderTableRows = (rows) => {
    if (!tableBody) {
      return;
    }
    if (!Array.isArray(rows) || !rows.length) {
      tableBody.innerHTML = '<tr><td colspan="7" class="empty-text">Ничего не найдено.</td></tr>';
      return;
    }
    tableBody.innerHTML = rows.map((row) => `
      <tr class="${row.is_me ? "leaderboard-row-me" : ""}">
        <td>${row.position}</td>
        <td>
          <strong>${escapeHtml(row.name)}</strong>
          ${row.is_me ? '<span class="leaderboard-me-badge">это вы</span>' : ""}
        </td>
        <td class="lb-mobile-hide">${escapeHtml(row.username || "—")}</td>
        <td>${row.activity}</td>
        <td>${row.karma}</td>
        <td>${Number(row.hybrid_score).toFixed(3)}</td>
        <td class="lb-mobile-hide">${escapeHtml(row.last_seen_at)}</td>
      </tr>
    `).join("");
  };

  const renderPager = (payload) => {
    if (!pager) {
      return;
    }
    const page = Number(payload.page) || 1;
    const totalPages = Number(payload.total_pages) || 1;
    pager.innerHTML = `
      <button type="button" class="button ghost small" data-page-target="${Math.max(1, page - 1)}" ${page <= 1 ? "disabled" : ""}>Назад</button>
      <span class="helper-text">Страница ${page} / ${totalPages}</span>
      <button type="button" class="button ghost small" data-page-target="${Math.min(totalPages, page + 1)}" ${page >= totalPages ? "disabled" : ""}>Вперёд</button>
    `;
    for (const button of pager.querySelectorAll("[data-page-target]")) {
      button.addEventListener("click", () => {
        state.page = Number(button.dataset.pageTarget || "1");
        loadLeaderboard();
      });
    }
  };

  const renderLeaderboard = (payload) => {
    renderTableRows(payload.rows);
    renderPager(payload);
    if (status) {
      const base = `Записей: ${payload.total_rows}. Ваш ранг: ${payload.my_rank ?? "нет"}.`;
      status.textContent = payload.truncated ? `${base} Показаны только первые 500 участников.` : base;
    }
  };

  const loadOverview = async () => {
    const data = await fetchJson(`/api/chat/${chatId}/overview`);
    renderActivityChart(data.daily_activity);
    renderHeroCard(
      heroCard,
      data.hero_of_day,
      "За последние 24 часа ещё не набралось активности.",
      (payload) => `
        <strong>${escapeHtml(payload.label)}</strong>
        <p>${payload.messages} сообщений за сутки</p>
        <p class="helper-text">Карма: ${payload.karma}</p>
      `,
    );
    renderHeroCard(
      richestCard,
      data.richest_of_day,
      "Экономика пока не выдала лидера.",
      (payload) => `
        <strong>${escapeHtml(payload.label)}</strong>
        <p>${payload.balance} монет на счету</p>
        <p class="helper-text">Срез по активному экономическому контуру чата</p>
      `,
    );
  };

  const syncModeButtons = () => {
    for (const button of modeButtons) {
      button.classList.toggle("is-active", button.dataset.lbMode === state.mode);
    }
  };

  const loadLeaderboard = async ({ findMe = false } = {}) => {
    syncModeButtons();
    if (status) {
      status.textContent = "Обновляем лидерборд...";
    }
    const params = new URLSearchParams({
      mode: state.mode,
      page: String(state.page),
    });
    if (state.query) {
      params.set("q", state.query);
    }
    if (findMe) {
      params.set("find_me", "1");
    }
    const data = await fetchJson(`/api/chat/${chatId}/leaderboard?${params.toString()}`);
    state.page = Number(data.page) || 1;
    renderLeaderboard(data);
  };

  const scheduleRefresh = () => {
    window.clearTimeout(refreshTimer);
    refreshTimer = window.setTimeout(() => {
      loadOverview().catch(() => null);
      loadLeaderboard().catch(() => null);
    }, 180);
  };

  for (const button of modeButtons) {
    button.addEventListener("click", () => {
      state.mode = button.dataset.lbMode || "mix";
      state.page = 1;
      loadLeaderboard().catch(() => null);
    });
  }

  searchForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    state.query = (searchInput?.value || "").trim();
    state.page = 1;
    loadLeaderboard().catch(() => null);
  });

  searchInput?.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      state.query = (searchInput.value || "").trim();
      state.page = 1;
      loadLeaderboard().catch(() => null);
    }, 260);
  });

  findMeButton?.addEventListener("click", () => {
    state.query = (searchInput?.value || "").trim();
    loadLeaderboard({ findMe: true }).catch(() => null);
  });

  if ("EventSource" in window) {
    liveSource = new EventSource(`/api/live/stream?scope=chat&chat_id=${chatId}`);
    for (const eventName of ["chat_activity", "new_vote", "chat_refresh"]) {
      liveSource.addEventListener(eventName, scheduleRefresh);
    }
  }

  window.addEventListener("beforeunload", () => {
    liveSource?.close();
  });

  loadOverview().catch(() => null);
  loadLeaderboard().catch(() => null);
})();
