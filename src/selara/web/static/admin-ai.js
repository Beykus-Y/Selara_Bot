// Owner AI settings in the server-rendered /app/admin.
// Talks to the same endpoints as the Mini App admin, mounted under /app/admin/api (admin web session).
// Everything is built with textContent/DOM nodes: server data is never put through innerHTML.

const API = "/app/admin/api";
const CAPABILITIES = [
  ["supports_tools", "инструменты"],
  ["supports_structured_output", "структурный вывод"],
  ["supports_vision", "изображения"],
];

class ApiError extends Error {}

async function request(method, path, body, { form = false } = {}) {
  const headers = { Accept: "application/json", "X-Selara-Admin": "1" };
  let payload;
  if (body !== undefined) {
    if (form) {
      headers["Content-Type"] = "application/x-www-form-urlencoded";
      payload = new URLSearchParams(body).toString();
    } else {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
  }
  const response = await fetch(API + path, { method, headers, body: payload, credentials: "same-origin" });
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (response.status === 401) {
    window.location.assign("/app/admin/login");
    throw new ApiError("Сессия истекла. Войдите снова.");
  }
  if (!response.ok) {
    const detail = data && (data.message || data.detail);
    const text = typeof detail === "string" ? detail : detail && detail.message;
    throw new ApiError(text || `Ошибка ${response.status}`);
  }
  return data;
}

const get = (path) => request("GET", path);

function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "value" || key === "checked" || key === "disabled" || key === "selected") node[key] = value;
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function field(label, control) {
  return h("label", { class: "admin-ai-field" }, h("span", { text: label }), control);
}

function check(label, checked, name) {
  const input = h("input", { type: "checkbox", name, checked: Boolean(checked) });
  return { input, node: h("label", { class: "admin-ai-check" }, input, h("span", { text: label })) };
}

function statusLine() {
  const node = h("div", { class: "admin-ai-status", role: "status" });
  return {
    node,
    ok(text) {
      node.className = "admin-ai-status is-ok";
      node.textContent = text;
    },
    error(text) {
      node.className = "admin-ai-status is-error";
      node.textContent = text;
    },
    clear() {
      node.className = "admin-ai-status";
      node.textContent = "";
    },
  };
}

async function submit(button, status, work, okText) {
  button.disabled = true;
  status.clear();
  try {
    const result = await work();
    status.ok(okText);
    return result;
  } catch (error) {
    status.error(error instanceof Error ? error.message : "Не удалось выполнить запрос.");
    return undefined;
  } finally {
    button.disabled = false;
  }
}

function table(headers, rows) {
  return h(
    "table",
    { class: "admin-ai-table" },
    h("thead", {}, h("tr", {}, headers.map((title) => h("th", { text: title })))),
    h("tbody", {}, rows.map((row) => h("tr", {}, row.map((cell) => h("td", {}, cell))))),
  );
}

const optionalNumber = (value) => {
  const text = String(value).trim();
  return text === "" ? null : Number(text);
};
const optionalText = (value) => {
  const text = String(value).trim();
  return text === "" ? null : text;
};

// --- sections ------------------------------------------------------------------------------------------

const sections = {};
let reloadAll = () => {};

function bodyOf(name) {
  const node = document.querySelector(`[data-ai-section="${name}"] [data-ai-body]`);
  return node instanceof HTMLElement ? node : null;
}

function mount(name, nodes) {
  const body = bodyOf(name);
  if (body) body.replaceChildren(...nodes.flat().filter(Boolean));
}

function showLoadError(name, error) {
  mount(name, [h("div", { class: "admin-ai-status is-error", text: error instanceof Error ? error.message : "Не удалось загрузить." })]);
}

sections.readiness = async () => {
  const data = await get("/ai/readiness");
  mount("readiness", [
    table(
      ["Проверка", "Статус", "Детали"],
      data.checks.map((item) => [item.label, item.status, item.detail || ""]),
    ),
  ]);
};

sections.quota = async () => {
  const data = await get("/monetization/quota-mode");
  const status = statusLine();
  const form = h("form", { class: "admin-ai-row" });
  const modeRequests = h("input", { type: "radio", name: "quota_mode", value: "requests", checked: data.quota_mode === "requests" });
  const modeAil = h("input", { type: "radio", name: "quota_mode", value: "ail", checked: data.quota_mode === "ail" });
  const free = h("input", { type: "number", min: "1", max: String(data.max_daily_ail), value: data.free_daily_ail ?? "" });
  const paid = h("input", { type: "number", min: "1", max: String(data.max_daily_ail), value: data.paid_daily_ail ?? "" });
  const confirm = check("Подтверждаю включение AI Limits (лимиты запросов перестанут использоваться)", false, "confirm");
  const save = h("button", { type: "submit", class: "button primary", text: "Сохранить" });
  form.append(
    h("div", { class: "admin-ai-muted", text: `Сейчас: ${data.quota_mode === "ail" ? "AI Limits" : "запросы"}. Запросы: Free ${data.requests.free_daily} / Paid ${data.requests.paid_daily} в сутки.` }),
    h("label", { class: "admin-ai-check" }, modeRequests, h("span", { text: "Запросы (5/150 по умолчанию)" })),
    h("label", { class: "admin-ai-check" }, modeAil, h("span", { text: "AI Limits (расход по модели)" })),
    h("div", { class: "admin-ai-fields" }, field("Free, AIL в сутки", free), field("Paid, AIL в сутки", paid)),
    data.activation_problems.length
      ? h("div", { class: "admin-ai-muted", text: `Что мешает включить AI Limits: ${data.activation_problems.join(" ")}` })
      : null,
    confirm.node,
    h("div", { class: "admin-ai-actions" }, save, status.node),
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const ail = modeAil.checked;
    const result = await submit(save, status, () =>
      request("PUT", "/monetization/quota-mode", {
        quota_mode: ail ? "ail" : "requests",
        free_daily_ail: optionalNumber(free.value),
        paid_daily_ail: optionalNumber(paid.value),
        confirm: confirm.input.checked,
      }),
      "Сохранено.");
    if (result) reloadAll(["quota", "profiles", "personal"]);
  });
  mount("quota", [
    form,
    table(
      ["Профиль", "Коэффициент AIL", "Доступен"],
      data.profiles.map((item) => [item.display_name, item.ail_multiplier, item.available ? "да" : "нет"]),
    ),
    h("div", { class: "admin-ai-muted", text: `Применяется в течение ${data.applies_within_seconds} секунд.` }),
  ]);
};

sections.profiles = async () => {
  const [profiles, models] = await Promise.all([get("/ai/model-profiles"), get("/ai/models")]);
  const rows = profiles.items.map((profile) => {
    const status = statusLine();
    const name = h("input", { type: "text", value: profile.display_name });
    const model = h("select", {}, h("option", { value: "", text: "— не назначена (fallback LLM_MODEL) —" }),
      models.items.map((item) => h("option", {
        value: item.key,
        text: `${item.display_name} (${item.model_id})${item.enabled ? "" : " — выключена"}`,
        selected: item.key === profile.model_key,
      })));
    const multiplier = h("input", { type: "text", value: profile.ail_multiplier });
    const enabled = check("Включён", profile.enabled, "enabled");
    const save = h("button", { type: "submit", class: "button primary", text: "Сохранить" });
    const form = h("form", { class: "admin-ai-row" },
      h("strong", { text: profile.profile_key }),
      h("div", { class: "admin-ai-muted", text: `Сейчас отвечает: ${profile.effective.model_id}${profile.effective.is_fallback ? " (fallback)" : ""}` }),
      h("div", { class: "admin-ai-fields" }, field("Название", name), field("Модель", model), field("Коэффициент AIL", multiplier)),
      enabled.node,
      h("div", { class: "admin-ai-actions" }, save, status.node));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const result = await submit(save, status, () =>
        request("PUT", `/ai/model-profiles/${encodeURIComponent(profile.profile_key)}`, {
          display_name: name.value,
          model_key: optionalText(model.value),
          ail_multiplier: multiplier.value.trim(),
          enabled: enabled.input.checked,
          revision: profile.revision,
        }),
        "Сохранено.");
      if (result) reloadAll(["profiles", "routes", "quota"]);
    });
    return form;
  });
  mount("profiles", [h("div", { class: "admin-ai-muted", text: `Применяется в течение ${profiles.applies_within_seconds} секунд. ${profiles.fallback_note}` }), ...rows]);
};

function modelForm(model, onSaved) {
  const created = model === null;
  const status = statusLine();
  const key = created ? h("input", { type: "text", name: "key", placeholder: "например deepseek-flash" }) : null;
  const name = h("input", { type: "text", value: model?.display_name ?? "" });
  const modelId = h("input", { type: "text", value: model?.model_id ?? "" });
  const prompt = h("input", { type: "text", value: model?.prompt_price_usd_per_million ?? "", placeholder: "$ за 1M токенов" });
  const completion = h("input", { type: "text", value: model?.completion_price_usd_per_million ?? "", placeholder: "$ за 1M токенов" });
  const aliases = h("textarea", { rows: "2", placeholder: "по одному на строку" });
  aliases.value = (model?.aliases ?? []).join("\n");
  const enabled = check("Включена", model ? model.enabled : true, "enabled");
  const caps = CAPABILITIES.map(([name_, label]) => ({ name: name_, ...check(label, model?.capabilities?.[name_], name_) }));
  const confirmDisable = created ? null : check("Подтверждаю выключение (профили останутся без модели)", false, "confirm_disable");
  const save = h("button", { type: "submit", class: "button primary", text: created ? "Добавить модель" : "Сохранить" });
  const form = h("form", { class: "admin-ai-row" },
    created ? null : h("strong", { text: `${model.key}${model.used_by_profiles.length ? ` · профили: ${model.used_by_profiles.join(", ")}` : ""}` }),
    h("div", { class: "admin-ai-fields" },
      key ? field("Ключ", key) : null, field("Название", name), field("model_id", modelId),
      field("Цена входа", prompt), field("Цена выхода", completion)),
    field("Алиасы", aliases),
    h("div", { class: "admin-ai-actions" }, enabled.node, caps.map((cap) => cap.node), confirmDisable?.node),
    h("div", { class: "admin-ai-actions" }, save, status.node));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      display_name: name.value,
      model_id: modelId.value.trim(),
      enabled: enabled.input.checked,
      prompt_price_usd_per_million: optionalText(prompt.value),
      completion_price_usd_per_million: optionalText(completion.value),
      aliases: aliases.value.split("\n").map((line) => line.trim()).filter(Boolean),
      capabilities: Object.fromEntries(caps.map((cap) => [cap.name, cap.input.checked])),
    };
    const result = await submit(save, status, () => created
      ? request("POST", "/ai/models", { ...payload, key: key.value.trim() })
      : request("PUT", `/ai/models/${encodeURIComponent(model.key)}`, {
        ...payload, revision: model.revision, confirm_disable: Boolean(confirmDisable?.input.checked),
      }),
    created ? "Модель добавлена." : "Сохранено.");
    if (result) onSaved();
  });
  return form;
}

sections.models = async () => {
  const data = await get("/ai/models");
  const onSaved = () => reloadAll(["models", "profiles", "routes", "quota"]);
  mount("models", [
    h("div", { class: "admin-ai-muted", text: `Цены нужны для расчёта стоимости. Применяется в течение ${data.applies_within_seconds} секунд.` }),
    ...data.items.map((model) => modelForm(model, onSaved)),
    h("details", {}, h("summary", { text: "Добавить модель" }), modelForm(null, onSaved)),
  ]);
};

sections.routes = async () => {
  const data = await get("/ai/feature-routes");
  const rows = data.items.map((item) => {
    const status = statusLine();
    const select = h("select", {}, h("option", { value: "", text: "— по умолчанию (LLM_MODEL) —" }),
      data.profiles.map((profile) => h("option", { value: profile.profile_key, text: profile.display_name, selected: profile.profile_key === item.profile_key })));
    const save = h("button", { type: "submit", class: "button primary", text: "Сохранить" });
    const form = h("form", { class: "admin-ai-row" },
      h("strong", { text: item.title }),
      h("div", { class: "admin-ai-muted", text: `Сейчас: ${item.effective_model_id}${item.is_fallback ? " (fallback)" : ""}` }),
      h("div", { class: "admin-ai-fields" }, field("Профиль", select)),
      h("div", { class: "admin-ai-actions" }, save, status.node));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const result = await submit(save, status, () =>
        request("PUT", `/ai/feature-routes/${encodeURIComponent(item.route_key)}`, { profile_key: optionalText(select.value) }),
        "Сохранено.");
      if (result) reloadAll(["routes"]);
    });
    return form;
  });
  mount("routes", [h("div", { class: "admin-ai-muted", text: data.fallback_note }), ...rows]);
};

const PERSONAL_FIELDS = [
  ["price_stars", "Цена, Stars"],
  ["duration_days", "Срок, дней"],
  ["free_daily_limit", "Free, запросов в сутки"],
  ["paid_daily_limit", "Paid, запросов в сутки"],
  ["memory_free_limit", "Память Free, фактов"],
  ["memory_paid_limit", "Память Paid, фактов"],
  ["memory_extract_every", "Авто-память: каждые N сообщений"],
];

sections.personal = async () => {
  const data = await get("/monetization/personal-config");
  const status = statusLine();
  const inputs = PERSONAL_FIELDS.map(([name, label]) => ({
    name,
    input: h("input", {
      type: "number",
      value: data.override?.[name] ?? "",
      placeholder: String(data.env[name] ?? ""),
    }),
    label,
  }));
  const auto = h("select", {},
    h("option", { value: "", text: `как в .env (${data.env.memory_auto_extract ? "вкл" : "выкл"})`, selected: data.override?.memory_auto_extract == null }),
    h("option", { value: "true", text: "включено", selected: data.override?.memory_auto_extract === true }),
    h("option", { value: "false", text: "выключено", selected: data.override?.memory_auto_extract === false }));
  const save = h("button", { type: "submit", class: "button primary", text: "Сохранить" });
  const form = h("form", { class: "admin-ai-row" },
    h("div", { class: "admin-ai-muted", text: "Пустое поле означает значение из .env (оно показано серым). Купленные подписки сохраняют свой дневной лимит." }),
    h("div", { class: "admin-ai-fields" }, inputs.map((item) => field(item.label, item.input)), field("Авто-память", auto)),
    h("div", { class: "admin-ai-actions" }, save, status.node));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = Object.fromEntries(inputs.map((item) => [item.name, optionalNumber(item.input.value)]));
    payload.memory_auto_extract = auto.value === "" ? null : auto.value === "true";
    const result = await submit(save, status, () => request("PUT", "/monetization/personal-config", payload), "Сохранено.");
    if (result) reloadAll(["personal", "quota"]);
  });
  mount("personal", [form]);
};

sections.report = async () => {
  const period = h("select", {}, [7, 30, 90].map((days) => h("option", { value: String(days), text: `${days} дн.`, selected: days === 30 })));
  const output = h("div", { class: "admin-ai-body" });
  const load = async () => {
    try {
      const data = await get(`/ai/breakdown?period_days=${encodeURIComponent(period.value)}`);
      output.replaceChildren(
        h("strong", { text: "Группы по расходу" }),
        data.chats.length
          ? table(
            ["Чат", "Запросы ?/??", "Кличка", "Вызовов", "Стоимость, $"],
            data.chats.map((row) => [`${row.title || "—"} (${row.chat_id})`, row.question_calls, row.nickname_calls, row.provider_calls, row.known_cost_usd]),
          )
          : h("div", { class: "admin-ai-muted", text: "За период расходов нет." }),
        h("strong", { text: "Профили" }),
        table(["Профиль", "Вызовов", "Стоимость, $"], data.profiles.map((row) => [row.model_profile ?? row.profile_key ?? "—", row.provider_calls ?? "", row.known_cost_usd])),
        h("div", { class: "admin-ai-muted", text: `Потрачено AIL пользователями: ${data.ail_consumed}.` }),
      );
    } catch (error) {
      output.replaceChildren(h("div", { class: "admin-ai-status is-error", text: error instanceof Error ? error.message : "Не удалось загрузить." }));
    }
  };
  period.addEventListener("change", load);
  mount("report", [field("Период", period), output]);
  await load();
};

function idempotencyKey() {
  return globalThis.crypto && typeof globalThis.crypto.randomUUID === "function"
    ? globalThis.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

sections.grants = async () => {
  const [grants, personal] = await Promise.all([get("/monetization/grants?limit=10"), get("/monetization/personal-entitlements")]);
  const status = statusLine();
  const scope = h("select", {}, h("option", { value: "user", text: "Selara Personal (пользователь)" }), h("option", { value: "chat", text: "Selara AI (группа)" }));
  const target = h("input", { type: "text", placeholder: "id пользователя или группы (у группы с минусом)" });
  const days = h("input", { type: "number", min: "1", max: "365", value: "30" });
  const reason = h("input", { type: "text", maxlength: "300", placeholder: "причина (обязательно)" });
  const notify = check("Уведомить получателя", true, "notify");
  let key = idempotencyKey();
  const buttons = {
    grant: h("button", { type: "button", class: "button primary", text: "Выдать / продлить" }),
    shorten: h("button", { type: "button", class: "button ghost", text: "Убрать дни" }),
    revoke: h("button", { type: "button", class: "button ghost", text: "Отключить полностью" }),
  };
  const run = (name) => async () => {
    const targetId = Number(target.value.trim());
    if (!Number.isSafeInteger(targetId) || targetId === 0) {
      status.error("Укажите числовой id.");
      return;
    }
    const common = { scope: scope.value, target_id: targetId, reason: reason.value.trim(), idempotency_key: key, notify: notify.input.checked };
    const dayCount = Number(days.value);
    let work;
    if (name === "grant") work = () => request("POST", "/monetization/grants", { ...common, days: dayCount });
    else if (name === "shorten") work = () => request("POST", "/monetization/grants/revoke", { ...common, mode: "shorten", days: dayCount });
    else {
      if (!window.confirm("Отключить подписку полностью? Оплаченные Stars не возвращаются.")) return;
      work = () => request("POST", "/monetization/grants/revoke", { ...common, mode: "cancel_all" });
    }
    const result = await submit(buttons[name], status, work, "Готово.");
    if (result) {
      key = idempotencyKey();
      status.ok(`Готово: ${result.action}, статус ${result.status}${result.valid_until ? ` до ${result.valid_until}` : ""}${result.notified === false ? "; получатель не уведомлён" : ""}.`);
      reloadAll(["grants"]);
    }
  };
  buttons.grant.addEventListener("click", run("grant"));
  buttons.shorten.addEventListener("click", run("shorten"));
  buttons.revoke.addEventListener("click", run("revoke"));
  const form = h("div", { class: "admin-ai-row" },
    h("div", { class: "admin-ai-fields" }, field("Что", scope), field("Кому (id)", target), field("Дней", days), field("Причина", reason)),
    notify.node,
    h("div", { class: "admin-ai-actions" }, buttons.grant, buttons.shorten, buttons.revoke, status.node));
  mount("grants", [
    form,
    h("strong", { text: "Последние операции" }),
    grants.items.length
      ? table(
        ["#", "Когда", "Действие", "Кому", "Дней", "Причина"],
        grants.items.map((row) => [row.id, row.created_at, row.action, `${row.scope === "chat" ? "чат" : "польз."} ${row.target_id}${row.target_title ? ` (${row.target_title})` : ""}`, row.delta_days, row.reason]),
      )
      : h("div", { class: "admin-ai-muted", text: "Пока ничего не выдавали." }),
    h("strong", { text: "Активные Selara Personal" }),
    personal.items.length
      ? table(
        ["Пользователь", "Действует до", "Выдана админом"],
        personal.items.map((row) => [`${row.name || row.username || ""} ${row.user_id}`.trim(), row.valid_until ?? "", row.granted_by_admin ? "да" : "нет"]),
      )
      : h("div", { class: "admin-ai-muted", text: "Активных подписок нет." }),
  ]);
};

// Selara in a group: the same payload and actions as the chat page in the Mini App, owner needs no chat rights.
sections.chat = async () => {
  const status = statusLine();
  const chatId = h("input", { type: "text", placeholder: "id группы (отрицательное число)" });
  const open = h("button", { type: "button", class: "button primary", text: "Открыть" });
  const output = h("div", { class: "admin-ai-body" });
  let current = null;

  const act = async (action, value, button) => {
    const result = await submit(button, status, async () => {
      const data = await request("POST", `/chats/${encodeURIComponent(current)}/selara`, { action, value: value ?? "" }, { form: true });
      render(data);
      return data;
    }, "Сохранено.");
    return result;
  };

  const render = (data) => {
    const name = h("input", { type: "text", placeholder: "новая кличка", maxlength: "40" });
    const addButton = h("button", { type: "button", class: "button primary", text: "Добавить кличку" });
    addButton.addEventListener("click", () => act("add_name", name.value, addButton));
    const custom = h("textarea", { rows: "2", maxlength: "500", placeholder: "свой характер, до 500 символов" });
    const customButton = h("button", { type: "button", class: "button ghost", text: "Сохранить свой характер" });
    customButton.addEventListener("click", () => act("set_custom", custom.value, customButton));
    const preset = h("select", {}, h("option", { value: "", text: "— выбрать пресет —" }),
      data.character.presets.map((item) => h("option", { value: item.key, text: item.title, selected: item.key === data.character.preset })));
    preset.addEventListener("change", () => preset.value && act("set_preset", preset.value, customButton));
    const switchRow = (action, label, on) => {
      const box = check(label, on, action);
      box.input.addEventListener("change", () => act(action, box.input.checked ? "1" : "0", addButton));
      return box.node;
    };
    const nameRows = data.names.map((item) => {
      const primary = h("button", { type: "button", class: "button ghost", text: "Основная", disabled: item.is_primary });
      primary.addEventListener("click", () => act("set_primary", item.norm, primary));
      const remove = h("button", { type: "button", class: "button ghost", text: "Убрать" });
      remove.addEventListener("click", () => act("remove_name", item.norm, remove));
      return h("div", { class: "admin-ai-actions" },
        h("span", { text: `${item.display}${item.is_primary ? " (основная)" : ""}${item.active ? "" : " — не работает без Selara AI"}` }), primary, remove);
    });
    const reset = h("button", { type: "button", class: "button ghost", text: "Забыть разговор с участниками" });
    reset.addEventListener("click", () => window.confirm("Забыть разговор с участниками?") && act("reset_history", "", reset));
    output.replaceChildren(
      h("div", { class: "admin-ai-muted", text: `${data.chat_title || "Группа"} · Selara AI ${data.paid ? "есть" : "нет"} · кличек: ${data.names.length} из ${data.name_limit} · лимит ответов: ${data.limits.daily} в сутки, ${data.limits.per_actor} на человека` }),
      ...nameRows,
      h("div", { class: "admin-ai-actions" }, name, addButton),
      h("div", { class: "admin-ai-fields" }, field("Характер", preset), field("Свой характер", custom)),
      customButton,
      switchRow("member_mode", "Отвечать участникам по кличке", data.member_mode),
      switchRow("history", "Разрешить читать недавние сообщения чата", data.history),
      switchRow("actions", "Разрешить безобидные действия (обнять и т.п.)", data.actions),
      reset,
    );
  };

  open.addEventListener("click", async () => {
    const value = chatId.value.trim();
    if (!/^-?\d{1,20}$/.test(value)) {
      status.error("Укажите числовой id группы.");
      return;
    }
    current = value;
    await submit(open, status, async () => render(await get(`/chats/${encodeURIComponent(value)}/selara`)), "Загружено.");
  });
  mount("chat", [h("div", { class: "admin-ai-actions" }, field("Группа", chatId), open), status.node, output]);
};

// --- boot ----------------------------------------------------------------------------------------------

async function load(name) {
  try {
    await sections[name]();
  } catch (error) {
    showLoadError(name, error);
  }
}

reloadAll = (names) => Promise.all((names ?? Object.keys(sections)).map(load));

if (document.querySelector("[data-admin-ai]")) {
  void reloadAll();
}
