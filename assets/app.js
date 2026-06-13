(function () {
  const data = window.ETF_RPS_DAILY;
  if (!data) return;

  let showAll = false;
  let sortKey = "rps20";
  let historyPage = 1;
  const historyPageSize = 8;

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));
  const rpsLabels = { rps3: "RPS 3", rps5: "RPS 5", rps10: "RPS 10", rps20: "RPS 20", rps50: "RPS 50", rps120: "RPS 120", rps250: "RPS 250" };
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
    if (!$("#focusBasis")) {
      $(".conclusion-list").insertAdjacentHTML("beforeend", '<div class="focus-basis" id="focusBasis"></div>');
    }
    $("#focusBasis").textContent = "最强方向口径：取当日 RPS 20 排名前 12 只 ETF，按主题出现数量优先、平均 RPS 20 次之排序；观察方向取其后的前 3 个主题。";
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
    if (!$("#rankingExplain")) {
      $("#rankingHint").insertAdjacentHTML("afterend", '<p class="ranking-explain" id="rankingExplain"></p>');
    }
    $("#rankingExplain").textContent = showAll
      ? "显示当前 ETF 池全部标的，可用上方 RPS 周期切换排序。"
      : `${label} 代表近 ${label.replace("RPS ", "")} 个交易日相对价格强度；90 分以上约等于强度排名进入前 10%。`;
    $("#activeRpsHead").textContent = label;
    $("#rpsGroupHead").textContent = `${label} 强度`;
    $("#etfTableBody").innerHTML = list.map((item, index) => `
      <tr class="${resonanceClass(item)}">
        <td class="rank-cell">${index + 1}</td>
        <td class="code-cell">${item.code}</td>
        <td class="name-cell">${item.shortName}</td>
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
    const totalPages = Math.max(1, Math.ceil(data.history.length / historyPageSize));
    historyPage = Math.min(Math.max(1, historyPage), totalPages);
    const start = (historyPage - 1) * historyPageSize;
    const items = data.history.slice(start, start + historyPageSize);
    $("#historyList").innerHTML = items.map((item) => `
      <a class="history-item" href="${prefix}${item.url}">
        <strong>${item.title}</strong>
        <span>查看</span>
      </a>
    `).join("");
    if (!$("#historyPager")) {
      $("#historyList").insertAdjacentHTML("afterend", `
        <div class="history-pager" id="historyPager">
          <button type="button" id="historyPrev">上一页</button>
          <span id="historyPageText"></span>
          <button type="button" id="historyNext">下一页</button>
        </div>
      `);
      $("#historyPrev").addEventListener("click", () => {
        historyPage -= 1;
        renderHistory();
      });
      $("#historyNext").addEventListener("click", () => {
        historyPage += 1;
        renderHistory();
      });
    }
    $("#historyPageText").textContent = `${historyPage} / ${totalPages}`;
    $("#historyPrev").disabled = historyPage <= 1;
    $("#historyNext").disabled = historyPage >= totalPages;
    $("#historyPager").hidden = data.history.length <= historyPageSize;
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
