(() => {
  const dataScript = document.getElementById("economy-page-data");
  if (!dataScript) {
    return;
  }

  const { chat_id: chatId, trade_points: tradePoints } = JSON.parse(dataScript.textContent);

  const applyUrl = `/api/chat/${chatId}/economy/apply`;
  const marketCreateUrl = `/api/chat/${chatId}/economy/market/create`;
  const marketBuyUrl = `/api/chat/${chatId}/economy/market/buy`;
  const marketCancelUrl = `/api/chat/${chatId}/economy/market/cancel`;
  const toastRegion = document.getElementById("toast-region");
  let selectedItem = null;

  const showToast = (message, tone = "ok") => {
    if (!toastRegion || !message) return;
    const toast = document.createElement("div");
    toast.className = "toast";
    toast.dataset.tone = tone;
    toast.textContent = message;
    toastRegion.appendChild(toast);
    window.setTimeout(() => toast.remove(), 2600);
  };

  const postForm = async (url, payload) => {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Accept": "application/json", "X-Requested-With": "fetch" },
      credentials: "same-origin",
      body: payload,
    });
    const data = await response.json().catch(() => null);
    return { ok: Boolean(response.ok && data && data.ok), data };
  };

  // Guards a button (or every submit button in a form) against a second
  // click firing a duplicate request while the first one is still in flight.
  const withInFlightGuard = (buttons, action) => {
    let pending = false;
    return async (...args) => {
      if (pending) return;
      pending = true;
      for (const button of buttons) button.disabled = true;
      try {
        await action(...args);
      } finally {
        pending = false;
        for (const button of buttons) button.disabled = false;
      }
    };
  };

  const applyItem = async ({ itemCode, targetType, plotNo }) => {
    const payload = new URLSearchParams();
    payload.set("item_code", itemCode);
    payload.set("target_type", targetType);
    if (plotNo) payload.set("plot_no", String(plotNo));
    const result = await postForm(applyUrl, payload);
    showToast(result.data?.message || "Не удалось применить предмет.", result.ok ? "ok" : "error");
    if (result.ok) window.location.reload();
  };

  document.querySelectorAll("[data-item-code]").forEach((card) => {
    card.addEventListener("dragstart", (event) => {
      event.dataTransfer?.setData("text/item-code", card.dataset.itemCode || "");
      event.dataTransfer?.setData("text/target", card.dataset.target || "");
    });
    card.addEventListener("click", () => {
      document.querySelectorAll("[data-item-code].is-selected").forEach((node) => node.classList.remove("is-selected"));
      card.classList.add("is-selected");
      selectedItem = { itemCode: card.dataset.itemCode, target: card.dataset.target };
    });
  });

  document.querySelectorAll("[data-drop-target]").forEach((target) => {
    const guardedApply = withInFlightGuard([target], applyItem);
    target.addEventListener("dragover", (event) => event.preventDefault());
    target.addEventListener("drop", async (event) => {
      event.preventDefault();
      const itemCode = event.dataTransfer?.getData("text/item-code");
      const expectedTarget = event.dataTransfer?.getData("text/target");
      if (!itemCode || !expectedTarget || expectedTarget !== target.dataset.targetType) return;
      await guardedApply({
        itemCode,
        targetType: target.dataset.targetType,
        plotNo: target.dataset.plotNo,
      });
    });
    target.addEventListener("click", async () => {
      if (!selectedItem || selectedItem.target !== target.dataset.targetType) return;
      await guardedApply({
        itemCode: selectedItem.itemCode,
        targetType: target.dataset.targetType,
        plotNo: target.dataset.plotNo,
      });
    });
  });

  const marketCreateForm = document.querySelector("[data-market-create-form]");
  if (marketCreateForm instanceof HTMLFormElement) {
    const submitButtons = [...marketCreateForm.querySelectorAll("button[type='submit']")];
    const guardedCreate = withInFlightGuard(submitButtons, async (event) => {
      const payload = new URLSearchParams(new FormData(event.currentTarget));
      const result = await postForm(marketCreateUrl, payload);
      showToast(result.data?.message || "Не удалось выставить лот.", result.ok ? "ok" : "error");
      if (result.ok) window.location.reload();
    });
    marketCreateForm.addEventListener("submit", (event) => {
      event.preventDefault();
      guardedCreate(event);
    });
  }

  document.querySelectorAll("[data-market-buy]").forEach((button) => {
    button.addEventListener(
      "click",
      withInFlightGuard([button], async () => {
        const payload = new URLSearchParams();
        payload.set("listing_id", button.dataset.marketBuy || "");
        payload.set("quantity", "1");
        const result = await postForm(marketBuyUrl, payload);
        showToast(result.data?.message || "Не удалось купить лот.", result.ok ? "ok" : "error");
        if (result.ok) window.location.reload();
      }),
    );
  });

  document.querySelectorAll("[data-market-cancel]").forEach((button) => {
    button.addEventListener(
      "click",
      withInFlightGuard([button], async () => {
        const payload = new URLSearchParams();
        payload.set("listing_id", button.dataset.marketCancel || "");
        const result = await postForm(marketCancelUrl, payload);
        showToast(result.data?.message || "Не удалось снять лот.", result.ok ? "ok" : "error");
        if (result.ok) window.location.reload();
      }),
    );
  });

  let activeFilter = "all";
  let activeSort = null;
  const renderMarket = () => {
    const rows = [...document.querySelectorAll("[data-market-row]")];
    rows.sort((a, b) => {
      if (!activeSort) return 0;
      const left = Number(a.dataset.unitPrice || 0);
      const right = Number(b.dataset.unitPrice || 0);
      return activeSort === "asc" ? left - right : right - left;
    });
    const grid = document.getElementById("market-grid");
    rows.forEach((row) => {
      const visible = activeFilter === "all" || row.dataset.filterGroup === activeFilter || row.dataset.filterGroup === "all";
      row.hidden = !visible;
      grid?.appendChild(row);
    });
  };

  document.querySelectorAll("[data-market-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      activeFilter = button.dataset.marketFilter || "all";
      renderMarket();
    });
  });
  document.querySelectorAll("[data-market-sort]").forEach((button) => {
    button.addEventListener("click", () => {
      activeSort = button.dataset.marketSort || null;
      renderMarket();
    });
  });
  renderMarket();

  const chartCanvas = document.getElementById("trade-chart");
  const chartSelect = document.getElementById("trade-item-select");
  const tradeKeys = Object.keys(tradePoints);
  for (const key of tradeKeys) {
    const option = document.createElement("option");
    option.value = key;
    option.textContent = key;
    chartSelect?.appendChild(option);
  }

  const drawChart = (itemCode) => {
    if (!(chartCanvas instanceof HTMLCanvasElement)) return;
    const ctx = chartCanvas.getContext("2d");
    if (!ctx) return;
    const points = tradePoints[itemCode] || [];
    ctx.clearRect(0, 0, chartCanvas.width, chartCanvas.height);
    ctx.fillStyle = "rgba(7, 13, 24, 0.84)";
    ctx.fillRect(0, 0, chartCanvas.width, chartCanvas.height);
    if (!points.length) {
      ctx.fillStyle = "#9bb5c7";
      ctx.fillText("Нет сделок за 7 дней", 16, 24);
      return;
    }
    const prices = points.map((point) => point.unit_price);
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    const span = Math.max(1, max - min);
    ctx.strokeStyle = "#58d8ff";
    ctx.lineWidth = 2;
    ctx.beginPath();
    points.forEach((point, index) => {
      const x = 18 + ((chartCanvas.width - 36) / Math.max(1, points.length - 1)) * index;
      const y = chartCanvas.height - 18 - ((point.unit_price - min) / span) * (chartCanvas.height - 40);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  };

  if (tradeKeys.length) {
    chartSelect.value = tradeKeys[0];
    drawChart(tradeKeys[0]);
    chartSelect?.addEventListener("change", () => drawChart(chartSelect.value));
  } else {
    drawChart("");
  }
})();
