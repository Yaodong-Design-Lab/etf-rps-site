(function () {
  const data = window.ETF_RPS_DAILY;
  if (!data) return;

  let showAll = false;
  let sortKey = "rps20";

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));
  const rpsLabels = { rps3: "R3", rps5: "R5", rps10: "R10", rps20: "R20", rps50: "R50", rps120: "R120", rps250: "R250" };
  const resonanceKeys = ["rps3", "rps5", "rps10", "rps20", "rps50", "rps120", "rps250"];

  const fmt = (value) => value === null || value === undefined || Number.isNaN(Number(value)) ? "-" : Number(value).toFixed(1);
  const signed = (value) => {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
    return `${Number(value) > 0 ? "+" : ""}${Number(value).toFixed(1)}%`;
  };
  const tone = (value) => Number(value) >= 0 ? "up" : "down";
  const rpsTone = (value) => {
    const number = Number(value);
    if (number >= 98) return "rps-hot";
    if (number >= 95) return "rps-warm";
    if (number >= 90) return "rps-soft";
    return "";
  };
  const resonanceCount = (item) => resonanceKeys.filter((key) => Number(item[key]) >= 90).length;
  const resonanceClass = (item) => {
    const count = resonanceCount(item);
    if (count >= 5) return "res-hot";
    if (count >= 3) return "res-warm";
    if (count >= 2) return "res-soft";
    return "";
  };

  function renderSummary() {
    $("#subtitle").textContent = `交易日 ${data.date}`;
    $("#strongestDirection").textContent = data.summary.strongestDirection;
    $("#topEtf").textContent = data.summary.topEtf;
    $("#watchDirections").textContent = data.summary.watchDirections.join(" / ") || "-";
    $("#riskTip").textContent = data.summary.riskTip;
  }

  function sortedEtfs() {
    const sorted = data.all.slice().sort((a, b) => Number(b[sortKey] ?? -1) - Number(a[sortKey] ?? -1));
    if (showAll) return sorted;
    return sorted.filter((item) => Number(item[sortKey]) >= 90);
  }

  function cell(value, extraClass = "") {
    return `<td class="${extraClass}" data-value="${fmt(value)}">${fmt(value)}</td>`;
  }

  function renderEtfTable() {
    const label = rpsLabels[sortKey] || sortKey.toUpperCase();
    const list = sortedEtfs();
    $("#toggleAll").classList.toggle("show-all", showAll);
    $("#rankingHint").textContent = showAll ? "全部ETF" : `${label} ≥ 90 强度榜`;
    $("#activeRpsHead").textContent = label;
    $("#rpsGroupHead").textContent = `${label} 强度`;
    $("#etfTableBody").innerHTML = list.map((item, index) => `
      <tr class="${resonanceClass(item)}">
        <td class="rank-cell">${index + 1}</td>
        <td class="code-cell">${item.code}</td>
        <td class="name-cell"><span class="res-dot"></span>${item.shortName}</td>
        <td class="theme-cell">${item.theme}</td>
        ${cell(item[sortKey], rpsTone(item[sortKey]))}
        <td class="${tone(item.ret1)}">${signed(item.ret1)}</td>
        <td class="${tone(item.ret3)}">${signed(item.ret3)}</td>
        <td class="${tone(item.ret5)}">${signed(item.ret5)}</td>
        <td class="${tone(item.ret20)}">${signed(item.ret20)}</td>
      </tr>
    `).join("");
  }

  function renderHistory() {
    const prefix = location.pathname.includes("/reports/") ? "../" : "";
    $("#historyList").innerHTML = data.history.map((item) => `
      <a class="history-item" href="${prefix}${item.url}">
        <strong>${item.title}</strong>
        <span>查看</span>
      </a>
    `).join("");
  }

  $("#toggleAll").addEventListener("click", () => {
    showAll = !showAll;
    renderEtfTable();
  });

  $$(".rps-sort").forEach((button) => {
    button.addEventListener("click", () => {
      sortKey = button.dataset.key;
      $$(".rps-sort").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      renderEtfTable();
    });
  });

  renderSummary();
  renderEtfTable();
  renderHistory();
})();
