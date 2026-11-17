"use strict";

const form = document.querySelector("#terms-form");
const controls = form.elements;
const confirmButton = document.querySelector("#confirm");
const previewButton = document.querySelector("#preview");
const previewPanel = document.querySelector("#preview-panel");
const resultPanel = document.querySelector("#results");
const csrf = document.querySelector('meta[name="csrf-token"]').content;
const numberFormat = new Intl.NumberFormat("zh-Hans", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const money = (value) => numberFormat.format(value);
const stockPrice = new Intl.NumberFormat("zh-Hans", {minimumFractionDigits: 2, maximumFractionDigits: 6});
const price = (value) => stockPrice.format(value);
const percent = (value) => `${(value * 100).toFixed(2)}%`;
const defaultPreview = previewPanel.firstElementChild.cloneNode(true);
let previewRecord = null;
let revision = 0;

function element(tag, content, className) {
  const node = document.createElement(tag);
  if (content !== undefined) node.textContent = content;
  if (className) node.className = className;
  return node;
}

function status(id, message, error = false) {
  const node = document.getElementById(id);
  node.textContent = message;
  node.classList.toggle("error", error);
}

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf},
    body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  if (response.status === 401) window.location.assign("/login");
  if (!response.ok) {
    const message = (await response.json().catch(() => null))?.error;
    throw new Error(message || "服务暂未返回结果，请稍后重试。");
  }
  return response.json();
}

function resetPreview() {
  revision += 1;
  previewRecord = null;
  confirmButton.disabled = true;
  previewPanel.replaceChildren(defaultPreview.cloneNode(true));
  resultPanel.hidden = true;
  status("form-status", "");
  status("confirm-status", "");
}

function updateKnockout() {
  const monthly = controls.knockout.value === "monthly";
  document.getElementById("ko-fields").hidden = !monthly;
  controls.ko_unit.disabled = !monthly;
  controls.ko_value.disabled = !monthly;
  controls.ko_value.required = monthly;
}

function readForm() {
  const monthly = controls.knockout.value === "monthly";
  return {
    instrument_id: controls.instrument_id.value,
    notional: controls.notional.value,
    tenor_months: controls.tenor_months.value,
    lookback_months: controls.lookback_months.value,
    strike_unit: controls.strike_unit.value,
    strike_value: controls.strike_value.value,
    knockout: controls.knockout.value,
    ko_unit: monthly ? controls.ko_unit.value : null,
    ko_value: monthly ? controls.ko_value.value : null,
  };
}

function setDraft(draft) {
  for (const name of Object.keys(readForm())) {
    const fallback = ["tenor_months", "lookback_months"].includes(name) ? "3" : "";
    controls[name].value = draft[name] ?? fallback;
  }
  updateKnockout();
  resetPreview();
}

function showPreview(record) {
  const {terms, market, instrument} = record;
  const priceKind = market.price_kind === "realtime" ? "盘中最新成交价" : "参考收盘价";
  const values = [
    ["挂钩标的", `${instrument.name} · ${instrument.ticker}`],
    [priceKind, `${price(terms.reference_price)} ${terms.currency}`],
    ["接货价", `${price(terms.strike)}（${terms.strike_pct.toFixed(2)}%）`],
    ["敲出价", terms.ko === null ? "无敲出" : `${price(terms.ko)}（${terms.ko_pct.toFixed(2)}%）`],
    ["面值", `${money(terms.notional)} ${terms.currency}`],
    ["票据期限", `${terms.tenor_months} 个月`],
    ["行情时点（交易所当地）", (market.price_at || market.as_of).replace("T", " ")],
  ];
  const pairs = element("dl", undefined, "kv");
  for (const [label, value] of values) {
    const pair = element("div");
    pair.append(element("dt", label), element("dd", value));
    pairs.append(pair);
  }
  const dates = terms.events.map(event => event.observation).join(" · ");
  previewPanel.replaceChildren(
    pairs,
    element("p", `观察日 ${dates}`, "dates"),
    element("p", "到期股价低于接货价时按约定交割。敲出或到期时结算应计票息。", "muted"),
    element("p", market.source, "badge"),
  );
}

function chart(record) {
  const {rows} = record.market;
  const window = record.market.lookback_months || 3;
  const {strike, ko, currency} = record.terms;
  const prices = rows.map(row => row.close);
  const levels = ko === null ? [strike] : [strike, ko];
  const min = Math.min(...prices, ...levels);
  const span = Math.max(Math.max(...prices, ...levels) - min, min * .01);
  const width = 900;
  const height = 250;
  const y = (value) => 210 - (value - min) / span * 174;
  const x = (index) => 70 + index / (rows.length - 1) * 740;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `最近 ${window} 个月收盘价走势及接货、敲出价`);

  function draw(tag, attributes, content) {
    const node = document.createElementNS(svg.namespaceURI, tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
    if (content) node.textContent = content;
    svg.append(node);
  }
  for (let i = 0; i <= 4; i++) {
    const tick = min + i * span / 4;
    draw("line", {x1:70, x2:810, y1:y(tick), y2:y(tick), stroke:"#dce8f0"});
    draw("text", {x:62, y:y(tick) + 4, "text-anchor":"end"}, price(tick));
  }
  for (const [index, level] of levels.entries()) {
    const color = index === 0 ? "#99753b" : "#5f718e";
    draw("line", {x1:70, x2:810, y1:y(level), y2:y(level), stroke:color, "stroke-dasharray":"6 5"});
    draw("text", {x:817, y:y(level) + 4}, index === 0 ? "接货价" : "敲出价");
  }
  draw("polyline", {
    fill:"none", stroke:"#13638b", "stroke-width":2.5,
    points:prices.map((price, index) => `${x(index)},${y(price)}`).join(" "),
  });
  draw("text", {x:70, y:20}, currency);
  draw("text", {x:70, y:235}, rows[0].date);
  draw("text", {x:810, y:235, "text-anchor":"end"}, rows.at(-1).date);
  const container = element("div", undefined, "chart");
  container.append(element("h3", `近 ${window} 个月收盘价`), svg);
  return container;
}

function table(headings, rows) {
  const tableNode = element("table");
  const header = element("tr");
  for (const title of headings) header.append(element("th", title));
  const head = element("thead");
  head.append(header);
  const body = element("tbody");
  for (const row of rows) {
    const tr = element("tr");
    for (const cell of row) tr.append(element("td", cell));
    body.append(tr);
  }
  tableNode.append(head, body);
  const wrapper = element("div", undefined, "table-wrap");
  wrapper.append(tableNode);
  return wrapper;
}

function showResult(record) {
  const {terms, market, result, issuer_quotes: quotes} = record;
  const title = element("div", undefined, "result-heading");
  const description = element("div");
  description.append(element("h2", "03 · 模型估值与结算情景"),
    element("p", `${record.instrument.name} · ${market.as_of} · ${market.source}`, "muted"),
    element("small", `估值编号 ${record.id}`, "muted"));
  const download = element("a", "下载估值 PDF", "button secondary");
  download.href = `/api/valuations/${record.id}/report`;
  title.append(description, download);
  const metrics = element("div", undefined, "metrics");
  for (const [label, value] of [
    ["估算年化票息", percent(result.annual_coupon)],
    [terms.ko === null ? `${terms.tenor_months} 个月全期票息` : "未提前敲出时的全期票息", percent(result.full_term_coupon)],
    ["参数敏感区间（年化）", result.range.map(percent).join(" – ")],
  ]) {
    const metric = element("div", undefined, "metric");
    metric.append(element("span", label), element("strong", value));
    metrics.append(metric);
  }
  const method = element("details");
  method.append(element("summary", "查看计算依据"));
  method.append(element("p", `${result.method} · ${result.model} · ${terms.version}。${result.range_method}。`));
  method.append(element("p", `历史波动率 ${percent(market.volatility)}，样本 ${market.volatility_observations} 笔日收益，截至 ${market.history_end || market.as_of}。连续利率 ${percent(market.rate)}，股息率 ${percent(market.dividend_yield)}。${market.assumption_source}。`));
  if (result.standard_error !== null) {
    method.append(element("p", `模拟 ${result.paths.toLocaleString()} 条路径，种子 ${result.seed}，年化票息标准误 ${(result.standard_error * 10000).toFixed(2)} 个基点。`));
  }
  resultPanel.replaceChildren(title, metrics, chart(record),
    element("h3", `结算示例 · ${terms.currency}`),
    table(["情景", "计息天数", "付息日", "票息", "本金或股票等值", "净损益"],
      result.scenarios.map(row => [row.label, row.days, row.payment, money(row.coupon), money(row.principal_value), money(row.net)])),
    element("p", `接货股数等值 ${money(result.shares_equivalent)} 股。实际股数与零碎股结算以条款为准。`, "muted"),
    method,
  );
  if (quotes.length) {
    resultPanel.append(element("h3", "发行商实际报价"),
      table(["发行商", "票息", "口径", "有效期"], quotes.map(quote => [
        quote.issuer, percent(quote.coupon), quote.basis === "annual" ? "年化" : "全期",
        new Date(quote.expires_at * 1000).toLocaleString("zh-Hans", {timeZone:"Asia/Shanghai"}) + "（北京时间）",
      ])));
  }
  if (resultPanel.dataset.ai === "yes") {
    const explain = element("button", "AI 解释结算", "quiet");
    const explanation = element("p", "", "status");
    explanation.setAttribute("role", "status");
    explain.addEventListener("click", async () => {
      explain.disabled = true;
      explanation.textContent = "正在解释结算…";
      try {
        const reply = await api(`/api/valuations/${record.id}/explain`, {});
        explanation.textContent = reply.explanation;
      } catch (error) {
        explanation.textContent = error.message;
      } finally {
        explain.disabled = false;
      }
    });
    resultPanel.append(explain, explanation);
  }
  resultPanel.hidden = false;
}

async function refreshHistory() {
  try {
    const rows = await api("/api/valuations");
    const list = document.querySelector("#history");
    list.replaceChildren();
    if (!rows.length) list.append(element("p", "完成一次估值后，记录会显示在这里。", "muted"));
    for (const row of rows) {
      const button = element("button", undefined, "quiet");
      button.append(element("span", `${row.terms.instrument_id} · ${row.terms.issue_date}`),
        element("strong", percent(row.annual_coupon)));
      button.addEventListener("click", async () => {
        try {
          const saved = await api(`/api/valuations/${row.id}`);
          resetPreview();
          showPreview(saved);
          showResult(saved);
          resultPanel.scrollIntoView({block:"start"});
        } catch (error) {
          status("history-status", error.message, true);
        }
      });
      list.append(button);
    }
  } catch (error) {
    status("history-status", error.message, true);
  }
}

form.addEventListener("input", () => {
  updateKnockout();
  resetPreview();
});
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  resetPreview();
  const current = revision;
  previewButton.disabled = true;
  status("form-status", "正在获取参考价、历史行情和观察日程…");
  try {
    const record = await api("/api/terms/preview", readForm());
    if (current === revision) {
      previewRecord = record;
      showPreview(record);
      confirmButton.disabled = false;
      status("form-status", "行情快照已冻结，请核对右侧条款。");
    }
  } catch (error) {
    if (current === revision) status("form-status", error.message, true);
  } finally {
    previewButton.disabled = false;
  }
});

confirmButton.addEventListener("click", async () => {
  const record = previewRecord;
  if (!record) return;
  confirmButton.disabled = true;
  status("confirm-status", "正在计算模型票息…");
  try {
    const completed = await api("/api/valuations", {preview_id: record.id});
    if (record === previewRecord) {
      showResult(completed);
      status("confirm-status", "估值已保存。");
      resultPanel.scrollIntoView({block:"start"});
    }
    await refreshHistory();
  } catch (error) {
    if (record === previewRecord) {
      status("confirm-status", error.message, true);
      confirmButton.disabled = false;
    }
  }
});

document.getElementById("parse").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const current = revision;
  button.disabled = true;
  status("ai-status", "正在读取描述…");
  try {
    const draft = await api("/api/terms/parse", {message: document.getElementById("prompt").value});
    if (draft.unsupported.length) {
      status("ai-status", `请调整以下条款：${draft.unsupported.join("、")}`, true);
    } else if (current === revision) {
      setDraft(draft);
      status("ai-status", "草稿已填入表单，请核对并补齐空白项。");
    } else {
      status("ai-status", "表单已修改，请重新生成草稿。");
    }
  } catch (error) {
    status("ai-status", error.message, true);
  } finally {
    button.disabled = false;
  }
});

for (const button of document.querySelectorAll("[data-example]")) {
  button.addEventListener("click", () => {
    const mode = button.dataset.example;
    setDraft({instrument_id:"XHKG:00700", notional:100000, tenor_months:3, lookback_months:3,
      strike_unit:"percent", strike_value:85, knockout:mode, ko_unit:"percent", ko_value:105});
  });
}

updateKnockout();
refreshHistory();
