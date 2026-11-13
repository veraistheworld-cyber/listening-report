const $ = (id) => document.getElementById(id);

const PLATFORM_LABEL = {
  netease: "网易云音乐",
  qq: "QQ 音乐",
  qishui: "汽水音乐 · beta",
  text: "手动粘贴",
};

let lastResult = null;

function isMostlyUrl(text) {
  const t = text.trim();
  if (!t) return false;
  if (/^https?:\/\//i.test(t)) return true;
  if (/https?:\/\//i.test(t) && t.split("\n").length <= 3) return true;
  return false;
}

function setBusy(busy, message) {
  $("btn-fetch").disabled = busy;
  const status = $("status");
  status.textContent = message || "";
  status.classList.toggle("busy", Boolean(busy));
}

function showError(msg) {
  const el = $("error");
  if (!msg) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.textContent = msg;
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatPercent(share) {
  const n = Number(share);
  if (!Number.isFinite(n)) return "—";
  const pct = n * 100;
  if (Math.abs(pct - Math.round(pct)) < 0.05) return `${Math.round(pct)}%`;
  return `${pct.toFixed(1)}%`;
}

function formatChineseDuration(ms) {
  if (ms == null || !Number.isFinite(Number(ms)) || Number(ms) < 0) return "—";
  const totalSec = Math.round(Number(ms) / 1000);
  const hours = Math.floor(totalSec / 3600);
  const minutes = Math.floor((totalSec % 3600) / 60);
  const seconds = totalSec % 60;
  const parts = [];
  if (hours) parts.push(`${hours} 小时`);
  if (minutes) parts.push(`${minutes} 分`);
  if (!hours && (seconds || !parts.length)) parts.push(`${seconds} 秒`);
  return parts.join(" ");
}

function formatClockDuration(ms) {
  if (ms == null || !Number.isFinite(Number(ms)) || Number(ms) < 0) return "—";
  const totalSec = Math.round(Number(ms) / 1000);
  const hours = Math.floor(totalSec / 3600);
  const minutes = Math.floor((totalSec % 3600) / 60);
  const seconds = totalSec % 60;
  if (hours) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function formatArtists(artists) {
  return Array.isArray(artists) ? artists.join(" / ") : artists || "";
}

function clearReport() {
  const report = $("report");
  if (!report) return;
  report.hidden = true;
  report.innerHTML = "";
}

function renderResult(data) {
  lastResult = data;
  clearReport();
  const box = $("result");
  box.hidden = false;
  const platform = PLATFORM_LABEL[data.platform] || data.platform || "未知来源";
  $("result-meta").textContent = platform;
  $("result-title").textContent = data.title || "未命名歌单";
  const n = Number(data.trackCount ?? (data.tracks || []).length);
  $("result-count").textContent = `共 ${n} 首，请核对歌名与歌手`;
  const warn = $("result-warning");
  if (data.warning) {
    warn.hidden = false;
    warn.textContent = data.warning;
  } else {
    warn.hidden = true;
    warn.textContent = "";
  }
  $("btn-analyze").disabled = n <= 0;

  const body = $("track-body");
  body.innerHTML = "";
  (data.tracks || []).forEach((t, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="col-idx">${i + 1}</td>
      <td>${escapeHtml(t.name)}</td>
      <td>${escapeHtml(formatArtists(t.artists))}</td>
      <td class="col-album">${escapeHtml(t.album || "")}</td>
    `;
    body.appendChild(tr);
  });
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}

function rankRows(items, countKey = "count") {
  if (!items || !items.length) return `<p class="report-empty">暂无数据</p>`;
  const max = Math.max(...items.map((item) => Number(item[countKey]) || 0), 1);
  return items
    .map((item, i) => {
      const width = Math.max(2, ((Number(item[countKey]) || 0) / max) * 100);
      return `<div class="rank-row">
        <span class="rank-idx">${i + 1}</span>
        <div class="rank-main">
          <div class="rank-line">
            <span class="rank-name">${escapeHtml(item.name || item.label)}</span>
            <span class="rank-meta">${item.count} 首 · ${formatPercent(item.share)}</span>
          </div>
          <div class="bar" aria-hidden="true"><span style="width:${width}%"></span></div>
        </div>
      </div>`;
    })
    .join("");
}

function songLine(item) {
  if (!item) return "—";
  const artists = formatArtists(item.artists);
  const title = `《${item.name || "未知曲目"}》`;
  return `${escapeHtml(title)}${artists ? ` ${escapeHtml(artists)}` : ""} · ${formatClockDuration(item.durationMs)}`;
}

function renderReport(data) {
  const report = $("report");
  const stats = data.stats || {};
  const duration = stats.duration || null;
  const eras = stats.eras || null;
  const personality = data.personality || { locked: true, teaser: "基于上面的统计摘要生成一张短人格卡。本期先占位，不调用模型。" };

  const tiles = [
    { num: stats.trackCount ?? 0, lbl: "首曲目" },
    { num: stats.uniqueArtists ?? 0, lbl: "位歌手" },
  ];
  if (duration) {
    tiles.push({ num: formatChineseDuration(duration.totalMs), lbl: `总时长（${duration.covered} 首）` });
    tiles.push({ num: formatChineseDuration(duration.avgMs), lbl: "平均时长" });
  }

  const facts = [
    `<div class="fact"><span class="fact-lbl">前三位集中度</span><span class="fact-val">${formatPercent(stats.top3Share)} 的曲目出现过常听的三位歌手</span></div>`,
    `<div class="fact"><span class="fact-lbl">合作率</span><span class="fact-val">${formatPercent(stats.collabRate)} 的曲目有两位及以上歌手</span></div>`,
    `<div class="fact"><span class="fact-lbl">独立专辑</span><span class="fact-val">${stats.uniqueAlbums ?? 0} 张</span></div>`,
  ];
  if (duration) {
    facts.push(`<div class="fact"><span class="fact-lbl">最长</span><span class="fact-val">${songLine(duration.longest)}</span></div>`);
    facts.push(`<div class="fact"><span class="fact-lbl">最短</span><span class="fact-val">${songLine(duration.shortest)}</span></div>`);
  }

  function coverageSection(title, block, note) {
    if (!block || !Array.isArray(block.buckets) || !block.buckets.length) return "";
    return `<section class="report-section">
      <h4>${title}</h4>
      <p class="report-note">${note}</p>
      ${rankRows(block.buckets)}
    </section>`;
  }

  const genresHtml = coverageSection(
    "风格",
    stats.genres,
    `基于已识别风格的 ${stats.genres && stats.genres.covered != null ? stats.genres.covered : "—"} / ${stats.trackCount ?? 0} 首`
  );
  const languagesHtml = coverageSection(
    "语种",
    stats.languages,
    `基于已识别语种的 ${stats.languages && stats.languages.covered != null ? stats.languages.covered : "—"} / ${stats.trackCount ?? 0} 首`
  );

  let erasHtml = "";
  if (eras && Array.isArray(eras.buckets) && eras.buckets.length) {
    const eraItems = eras.buckets.map((b) => ({ ...b, name: b.label }));
    erasHtml = `<section class="report-section">
      <h4>年代分布</h4>
      <p class="report-note">基于有发行年份的 ${eras.covered} 首</p>
      ${rankRows(eraItems)}
    </section>`;
  }

  let albumsHtml = "";
  if (stats.topAlbums && stats.topAlbums.length) {
    albumsHtml = `<section class="report-section">
      <h4>常出现的专辑</h4>
      ${rankRows(stats.topAlbums)}
    </section>`;
  }

  const notes = Array.isArray(data.notes) ? data.notes.filter(Boolean) : [];
  const notesHtml = notes.length
    ? `<ul class="report-notes">${notes.map((n) => `<li>${escapeHtml(n)}</li>`).join("")}</ul>`
    : "";

  report.innerHTML = `
    <header class="report-head">
      <p class="meta">听歌报告</p>
      <h3>${escapeHtml(data.title || "未命名歌单")}</h3>
    </header>
    <div class="stat-grid">${tiles
      .map((t) => `<div class="stat-tile"><div class="num">${escapeHtml(t.num)}</div><div class="lbl">${escapeHtml(t.lbl)}</div></div>`)
      .join("")}</div>
    <section class="report-section">
      <h4>常听的人</h4>
      ${rankRows(stats.topArtists || [])}
    </section>
    ${genresHtml}
    ${languagesHtml}
    ${erasHtml}
    <section class="report-section">
      <h4>时长与集中度</h4>
      <div class="facts">${facts.join("")}</div>
    </section>
    ${albumsHtml}
    <section class="personality" aria-label="人格解读（付费）">
      <p class="personality-kicker">付费</p>
      <h4>人格解读（付费）</h4>
      <p>${escapeHtml(personality.teaser || "基于上面的统计摘要生成一张短人格卡。本期先占位，不调用模型。")}</p>
    </section>
    ${notesHtml}
  `;
  report.hidden = false;
  report.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function fetchPlaylist() {
  const source = $("source").value.trim();
  showError("");
  $("result").hidden = true;
  clearReport();
  lastResult = null;
  if (!source) {
    showError("请先粘贴歌单链接，或按「歌名 - 歌手」每行一首写曲目。");
    return;
  }
  const payload = isMostlyUrl(source) ? { url: source } : { text: source };
  setBusy(true, "正在拉取完整曲目…");
  try {
    const res = await fetch("/api/fetch-playlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showError(data.error || "获取失败，请稍后重试。");
      return;
    }
    renderResult(data);
  } catch (err) {
    showError("网络异常，无法连上本地服务。");
  } finally {
    setBusy(false, "");
  }
}

async function startAnalyze() {
  if (!lastResult) return;
  showError("");
  const btn = $("btn-analyze");
  const report = $("report");
  btn.disabled = true;
  report.hidden = false;
  report.innerHTML = `<p class="report-status">正在生成报告…</p>`;
  try {
    const res = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(lastResult),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      report.hidden = true;
      report.innerHTML = "";
      showError(data.error || "分析失败，请稍后重试。");
      return;
    }
    renderReport(data);
  } catch (_) {
    report.hidden = true;
    report.innerHTML = "";
    showError("网络异常，无法连上本地服务。");
  } finally {
    btn.disabled = false;
  }
}

$("btn-fetch").addEventListener("click", fetchPlaylist);
$("btn-analyze").addEventListener("click", startAnalyze);
$("source").addEventListener("keydown", (ev) => {
  if ((ev.metaKey || ev.ctrlKey) && ev.key === "Enter") {
    ev.preventDefault();
    fetchPlaylist();
  }
});
