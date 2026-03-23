import { POWER_PROFILES, defaults } from "./config.js?v=20260320a1";
import { getEffectiveBaseUrl } from "./settings.js?v=20260320a1";

export function createPowerController({ elements, state, getSettings }) {
  let viewportMetricsRaf = 0;
  let echartsLoadPromise = null;
  let historyModalCacheKey = "";
  let historyModalCacheHtml = "";
  let historyModalSectionKey = "";
  let modalStructureKey = "";
  let historyChartResizeRaf = 0;
  const historyChartInstances = {
    "24h": null,
    "7d": null,
  };
  const historyChartOptionKeys = {
    "24h": "",
    "7d": "",
  };
  const AUTO_PROFILE_ID = "auto-live";
  const wattsBuffer = []; // {ts, watts} — unique BLE readings, kept 30s
  let wattsBufferLastTs = null;

  function wattsBufferPush(watts, readingTs) {
    if (readingTs == null || readingTs === wattsBufferLastTs) return;
    wattsBufferLastTs = readingTs;
    const now = Date.now() / 1000;
    wattsBuffer.push({ ts: now, watts });
    // prune older than 30s
    while (wattsBuffer.length > 0 && now - wattsBuffer[0].ts > 30) wattsBuffer.shift();
  }

  function wattsAvg(windowSecs) {
    const cutoff = Date.now() / 1000 - windowSecs;
    const vals = wattsBuffer.filter(p => p.ts >= cutoff).map(p => p.watts);
    if (!vals.length) return null;
    return vals.reduce((a, b) => a + b, 0) / vals.length;
  }
  const POWER_HISTORY_REFRESH_MS = 10 * 60 * 1000;
  const SDGE_DR2_ALL_IN_RATES = {
    summer: {
      onPeak: 0.70103,
      offPeak: 0.42936,
    },
    winter: {
      onPeak: 0.62200,
      offPeak: 0.48485,
    },
  };

  function getSelectedPowerProfileId(settings) {
    const candidate = String(settings.powerProfile || "").trim();
    if (candidate && POWER_PROFILES[candidate]) return candidate;
    return defaults.powerProfile;
  }

  function parseHostnameFromBaseUrl(baseUrl) {
    try {
      return new URL(baseUrl).hostname.toLowerCase();
    } catch (_err) {
      return "";
    }
  }

  function getAppHostname() {
    if (typeof window !== "undefined" && window.location && window.location.hostname) {
      return String(window.location.hostname).toLowerCase();
    }
    return "";
  }

  function resolveAutoProfileId(settings, payload) {
    const telemetryProfileId = String(payload && payload.deployment_profile ? payload.deployment_profile : "").trim();
    if (telemetryProfileId && POWER_PROFILES[telemetryProfileId]) {
      return telemetryProfileId;
    }

    const appHostname = getAppHostname();
    if (appHostname === "eco.bartlebygpt.org" || appHostname === "apij.bartlebygpt.org") {
      return "eco-orin";
    }
    if (appHostname === "pi.bartlebygpt.org") {
      return "pi-rpi4";
    }
    if (appHostname === "api.bartlebygpt.org") {
      return "eco-orin";
    }

    const effectiveBaseUrl = getEffectiveBaseUrl(settings.baseUrl);
    const hostname = parseHostnameFromBaseUrl(effectiveBaseUrl);
    if (hostname === "eco.bartlebygpt.org" || hostname === "apij.bartlebygpt.org") {
      return "eco-orin";
    }
    if (hostname === "pi.bartlebygpt.org") {
      return "pi-rpi4";
    }
    if (hostname === "api.bartlebygpt.org") {
      return "eco-orin";
    }
    return "eco-orin";
  }

  function resolvePowerProfile(settings, payload) {
    const selectedProfileId = getSelectedPowerProfileId(settings);
    if (selectedProfileId !== AUTO_PROFILE_ID) {
      const selectedProfile = POWER_PROFILES[selectedProfileId] || POWER_PROFILES[defaults.powerProfile];
      return {
        selectedProfileId,
        resolvedProfileId: selectedProfileId,
        profile: selectedProfile,
        isAuto: false,
      };
    }

    const resolvedProfileId = resolveAutoProfileId(settings, payload);
    const resolvedProfile = POWER_PROFILES[resolvedProfileId] || POWER_PROFILES["eco-orin"] || POWER_PROFILES[defaults.powerProfile];
    return {
      selectedProfileId,
      resolvedProfileId,
      profile: resolvedProfile,
      isAuto: true,
    };
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function toFiniteNumber(value) {
    if (value === null || value === undefined) return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function isSolixProfile(resolution) {
    return Boolean(
      resolution &&
      (resolution.resolvedProfileId === "pi-rpi4" || resolution.resolvedProfileId === "eco-orin")
    );
  }

  function normalizeHistoryPoints(windowPayload) {
    const source = windowPayload && Array.isArray(windowPayload.points) ? windowPayload.points : [];
    return source
      .map((point) => ({
        ts: toFiniteNumber(point && point.ts),
        loadW: toFiniteNumber(point && point.load_w),
        chargeW: toFiniteNumber(point && point.charge_w),
        socPct: toFiniteNumber(point && point.soc_pct),
        requestsCount: toFiniteNumber(point && point.requests_count),
      }))
      .filter((point) => point.ts !== null)
      .sort((a, b) => a.ts - b.ts);
  }

  function formatHistoryGeneratedAt(isoTimestamp) {
    if (!isoTimestamp) return "";
    const parsed = new Date(isoTimestamp);
    if (Number.isNaN(parsed.getTime())) return "";
    return parsed.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function renderHistoryCard(title, historyWindow, mode) {
    const points = normalizeHistoryPoints(historyWindow);
    const hasPoints = points.length > 0;
    return `<article class="power-history-card">
<h4>${escapeHtml(title)}</h4>
${hasPoints ? `<div class="power-history-chart" data-history-window="${escapeHtml(mode)}" aria-label="${escapeHtml(title)} chart"></div>` : `<div class="power-history-empty">No data points yet.</div>`}
</article>`;
  }

  function disposeHistoryCharts() {
    for (const key of ["24h", "7d"]) {
      const chart = historyChartInstances[key];
      if (chart) {
        chart.dispose();
        historyChartInstances[key] = null;
      }
      historyChartOptionKeys[key] = "";
    }
  }

  function formatXAxisLabelForMode(value, mode) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    if (mode === "7d") {
      return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    }
    return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }

  function historyPointsToSeries(points) {
    return {
      load: points.map((point) => [point.ts * 1000, point.loadW]),
      charge: points.map((point) => [point.ts * 1000, point.chargeW]),
      soc: points.map((point) => [point.ts * 1000, point.socPct]),
      requests: points.map((point) => [point.ts * 1000, point.requestsCount]),
    };
  }

  function buildHistoryChartOption(points, mode) {
    const series = historyPointsToSeries(points);
    const is24h = mode === "24h";
    const lineWidth = 1.2;
    return {
      animation: false,
      backgroundColor: "transparent",
      toolbox: {
        left: 16,
        top: 4,
        feature: {
          dataZoom: { yAxisIndex: "none", title: { zoom: "Zoom", back: "Reset" } },
        },
      },
      dataZoom: [{ type: "inside" }],
      legend: {
        top: 6,
        right: 16,
        itemWidth: 32,
        itemHeight: 4,
        textStyle: {
          fontFamily: "EB Garamond, Georgia, serif",
          fontSize: 18,
          color: "rgba(36,32,26,0.84)",
        },
      },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
        valueFormatter(value) {
          if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
          return String(Math.round(Number(value)));
        },
      },
      grid: {
        top: 58,
        left: 104,
        right: 104,
        bottom: 76,
      },
      xAxis: {
        type: "time",
        axisLine: { lineStyle: { color: "rgba(24,20,16,0.42)", width: 1.2 } },
        axisTick: { show: true },
        axisLabel: {
          fontFamily: "EB Garamond, Georgia, serif",
          fontSize: 14,
          fontWeight: 700,
          color: "rgba(36,32,26,0.74)",
          formatter(value) {
            return formatXAxisLabelForMode(value, mode);
          },
        },
        splitLine: { show: true, lineStyle: { color: "rgba(40,36,30,0.08)" } },
      },
      yAxis: [
        {
          type: "value",
          name: "Watts",
          nameLocation: "middle",
          nameGap: 72,
          min: 0,
          axisLine: { show: true, lineStyle: { color: "rgba(24,20,16,0.42)", width: 1.2 } },
          axisTick: { show: true },
          axisLabel: {
            formatter: "{value}W",
            fontFamily: "EB Garamond, Georgia, serif",
            fontSize: 21,
            fontWeight: 700,
            color: "rgba(36,32,26,0.74)",
          },
          nameTextStyle: {
            fontFamily: "EB Garamond, Georgia, serif",
            fontSize: 21,
            fontWeight: 700,
            color: "rgba(36,32,26,0.68)",
          },
          splitLine: { show: true, lineStyle: { color: "rgba(40,36,30,0.14)" } },
        },
        {
          type: "value",
          name: "State of Charge",
          nameLocation: "middle",
          nameGap: 72,
          min: 0,
          max: 100,
          axisLine: { show: true, lineStyle: { color: "rgba(24,20,16,0.42)", width: 1.2 } },
          axisTick: { show: true },
          axisLabel: {
            formatter: "{value}%",
            fontFamily: "EB Garamond, Georgia, serif",
            fontSize: 21,
            fontWeight: 700,
            color: "rgba(36,32,26,0.74)",
          },
          nameTextStyle: {
            fontFamily: "EB Garamond, Georgia, serif",
            fontSize: 21,
            fontWeight: 700,
            color: "rgba(36,32,26,0.68)",
          },
          splitLine: { show: false },
        },
        {
          type: "value",
          show: false,
          position: "right",
          offset: 0,
          min: 0,
        },
      ],
      series: [
        {
          name: "Load",
          type: "line",
          yAxisIndex: 0,
          showSymbol: false,
          connectNulls: true,
          lineStyle: { width: lineWidth, color: "#8f2f2f" },
          itemStyle: { color: "#8f2f2f" },
          data: series.load,
        },
        {
          name: "Charge",
          type: "line",
          yAxisIndex: 0,
          showSymbol: false,
          connectNulls: true,
          lineStyle: { width: lineWidth, color: "#246f38" },
          itemStyle: { color: "#246f38" },
          data: series.charge,
        },
        {
          name: "State of Charge",
          type: "line",
          yAxisIndex: 1,
          showSymbol: false,
          connectNulls: true,
          lineStyle: { width: lineWidth, color: "#8d7722" },
          areaStyle: { color: "rgba(214,183,72,0.5)" },
          itemStyle: { color: "#8d7722" },
          data: series.soc,
        },
        {
          name: "Requests",
          type: "bar",
          yAxisIndex: 2,
          itemStyle: { color: "rgba(74,111,165,0.55)" },
          data: series.requests,
        },
      ],
    };
  }

  function ensureHistoryChartsRendered(resolution) {
    if (!isSolixProfile(resolution)) return;
    if (!elements.powerModalBackdrop.classList.contains("is-open")) return;
    if (!state.powerHistory || !state.powerHistory.history_24h || !state.powerHistory.history_7d) return;

    const echartsLib = (typeof window !== "undefined" && window.echarts) ? window.echarts : null;
    if (!echartsLib || typeof echartsLib.init !== "function") return;

    const windows = {
      "24h": normalizeHistoryPoints(state.powerHistory.history_24h),
      "7d": normalizeHistoryPoints(state.powerHistory.history_7d),
    };

    for (const key of ["24h", "7d"]) {
      const node = elements.powerModalBody.querySelector(`.power-history-chart[data-history-window="${key}"]`);
      if (!node) continue;

      let chart = historyChartInstances[key];
      const points = windows[key];
      const firstTs = points.length ? points[0].ts : "";
      const lastTs = points.length ? points[points.length - 1].ts : "";
      const optionKey = [
        state.powerHistory && state.powerHistory.generated_at_iso ? state.powerHistory.generated_at_iso : "",
        points.length,
        firstTs,
        lastTs,
      ].join("|");

      if (!chart || chart.getDom() !== node) {
        if (chart) chart.dispose();
        chart = echartsLib.init(node, null, { renderer: "canvas" });
        historyChartInstances[key] = chart;
        historyChartOptionKeys[key] = "";
      }

      if (historyChartOptionKeys[key] !== optionKey) {
        chart.setOption(buildHistoryChartOption(points, key), true);
        historyChartOptionKeys[key] = optionKey;
        chart.resize();
      }
    }
  }

  function scheduleHistoryChartResize() {
    if (historyChartResizeRaf) return;
    historyChartResizeRaf = window.requestAnimationFrame(() => {
      historyChartResizeRaf = 0;
      for (const key of ["24h", "7d"]) {
        const chart = historyChartInstances[key];
        if (chart) chart.resize();
      }
    });
  }

  function buildPowerHistoryModalSection(resolution) {
    if (!isSolixProfile(resolution)) {
      return "";
    }

    const history = state.powerHistory;
    const hasHistory = Boolean(history && history.history_24h && history.history_7d);
    const cacheKey = [
      hasHistory ? "1" : "0",
      state.powerHistoryAvailable ? "1" : "0",
      state.powerHistoryInFlight ? "1" : "0",
      history && history.generated_at_iso ? history.generated_at_iso : "",
      history && Number.isFinite(Number(history.rows_considered)) ? Number(history.rows_considered) : "",
      history && history.history_24h && Array.isArray(history.history_24h.points) ? history.history_24h.points.length : 0,
      history && history.history_7d && Array.isArray(history.history_7d.points) ? history.history_7d.points.length : 0,
    ].join("|");
    historyModalSectionKey = cacheKey;
    if (cacheKey === historyModalCacheKey) {
      return historyModalCacheHtml;
    }

    const generatedAt = hasHistory ? formatHistoryGeneratedAt(history.generated_at_iso) : "";
    const rows = hasHistory && Number.isFinite(Number(history.rows_considered))
      ? Number(history.rows_considered)
      : null;
    let statusLine = "";
    if (generatedAt) {
      statusLine = `Last refresh: ${generatedAt}${rows !== null ? ` · ${rows} source rows` : ""}`;
    } else if (state.powerHistoryInFlight) {
      statusLine = "Loading history from local Solix logs...";
    } else if (!state.powerHistoryAvailable) {
      statusLine = "History unavailable on this server yet.";
    }

    const charts = hasHistory
      ? `${renderHistoryCard("Last 24 Hours", history.history_24h, "24h")}
${renderHistoryCard("Last 7 Days", history.history_7d, "7d")}`
      : `<div class="power-history-empty">Historical charts will appear once telemetry history is available.</div>`;

    const html = `<hr>
<section class="power-history-section">
<h3>Battery, Load &amp; Usage History</h3>
${statusLine ? `<p class="power-history-status">${escapeHtml(statusLine)}</p>` : ""}
<p class="power-history-legend">
<span><i class="power-history-key is-load"></i>Load</span>
<span><i class="power-history-key is-charge"></i>Charge</span>
<span><i class="power-history-key is-soc"></i>State of Charge</span>
<span><i class="power-history-key is-concurrent"></i>Requests</span>
</p>
<div class="power-history-grid">
${charts}
</div>
</section>`;
    historyModalCacheKey = cacheKey;
    historyModalCacheHtml = html;
    return html;
  }

  function buildDebugSection(payload) {
    if (!payload) return "";
    const fmt = (v, unit = "") => (v === null || v === undefined || !Number.isFinite(Number(v))) ? "—" : `${Math.round(Number(v))}${unit}`;
    const isWallTotal = payload.power_measurement_kind === "wall-total";
    const total = fmt(payload.estimated_total_watts, "W");
    const raw = isWallTotal
      ? `${fmt(payload.estimated_total_watts, "W")} (wall meter)`
      : fmt(payload.measured_server_watts ?? payload.measured_gpu_watts, "W");
    const base = isWallTotal ? "n/a" : fmt(payload.base_system_watts, "W");
    const clampMin = Number(payload.clamp_min_watts);
    const clampMax = Number(payload.clamp_max_watts);
    const clampStr = isWallTotal ? "bypassed (live)" : (clampMin > 0 || clampMax > 0)
      ? `[${clampMin > 0 ? Math.round(clampMin) : "—"}, ${clampMax > 0 ? Math.round(clampMax) : "—"}]W`
      : "off";
    const live = payload.watts_is_live ? "yes" : "no";
    const kind = escapeHtml(String(payload.power_measurement_kind || "—"));
    const backend = escapeHtml(String(payload.power_backend || "—"));
    const mode = escapeHtml(String(state.powerTelemetryMode || "—"));
    const ageSec = payload.power_reading_ts ? (Date.now() / 1000 - Number(payload.power_reading_ts)) : null;
    const age = ageSec !== null ? `${ageSec.toFixed(3)}s ago` : "—";
    const fmtAvg = (secs) => {
      const v = wattsAvg(secs);
      const cutoff = Date.now() / 1000 - secs;
      const n = wattsBuffer.filter(p => p.ts >= cutoff).length;
      return v !== null ? `${v.toFixed(2)}W (n=${n})` : "—";
    };
    const concurrentRunning = Number.isFinite(Number(payload.requests_running)) ? Math.round(Number(payload.requests_running)) : 0;
    const concurrentWaiting = Number.isFinite(Number(payload.requests_waiting)) ? Math.round(Number(payload.requests_waiting)) : 0;
    return `<hr>
<div class="power-debug">
<table class="power-debug-table">
<tr><td>backend</td><td>${backend}</td><td>kind</td><td>${kind}</td></tr>
<tr><td>live</td><td>${live}</td><td>mode</td><td>${mode}</td></tr>
<tr><td>raw measured</td><td class="power-debug-raw">${raw}</td><td>base overhead</td><td>${base}</td></tr>
<tr><td>display total</td><td class="power-debug-total">${total}</td><td>clamp</td><td>${clampStr}</td></tr>
<tr><td>concurrent</td><td class="power-debug-concurrent">${concurrentRunning} running</td><td>waiting</td><td class="power-debug-waiting">${concurrentWaiting}</td></tr>
<tr><td>last reading</td><td class="power-debug-age" data-reading-ts="${payload.power_reading_ts || ""}">${age}</td><td></td><td></td></tr>
<tr><td>avg 5s</td><td class="power-debug-avg" data-secs="5">${fmtAvg(5)}</td><td>avg 10s</td><td class="power-debug-avg" data-secs="10">${fmtAvg(10)}</td></tr>
<tr><td>avg 15s</td><td class="power-debug-avg" data-secs="15">${fmtAvg(15)}</td><td>avg 30s</td><td class="power-debug-avg" data-secs="30">${fmtAvg(30)}</td></tr>
</table>
</div>`;
  }

  function updatePowerModalBody(resolution, payload, costContext) {
    const profile = resolution && resolution.profile ? resolution.profile : null;
    let html = profile && profile.modalHtml ? profile.modalHtml : "";

    if (payload) {
      const baseSystemWatts = Number.parseFloat(String(payload.base_system_watts));
      const totalWatts = Number.parseFloat(String(payload.estimated_total_watts));
      const measuredServerWatts = Number.parseFloat(String(payload.measured_server_watts ?? payload.measured_gpu_watts));

      const canRenderPowerEquation =
        Number.isFinite(baseSystemWatts) &&
        Number.isFinite(measuredServerWatts) &&
        Number.isFinite(totalWatts);

      const canRenderCostEquation =
        canRenderPowerEquation &&
        Boolean(costContext && costContext.dynamicTou) &&
        Number.isFinite(Number(costContext.rateKwh));

      if (canRenderPowerEquation) {
        const displayBaseWatts = totalWatts - measuredServerWatts;
        html += `<hr><p><strong>Power:</strong> ${Math.round(displayBaseWatts)}W base + ${Math.round(measuredServerWatts)}W load = ${Math.round(totalWatts)}W</p>`;
      }

      if (isSolixProfile(resolution)) {
        const solarW = Number.parseFloat(String(payload.solix_solar_input_w));
        const soc = Number.parseFloat(String(payload.solix_soc_pct));
        const hasSolar = Number.isFinite(solarW);
        const hasSoc = Number.isFinite(soc);
        const hasDraw = Number.isFinite(totalWatts);
        if (hasSolar && hasSoc && hasDraw) {
          const CAPACITY_WH = 288;
          const netW = totalWatts - solarW;
          if (Math.abs(netW) < 0.5) {
            html += `<p><strong>Battery:</strong> maintaining charge at ${soc.toFixed(0)}%</p>`;
          } else if (netW > 0) {
            const storedWh = (soc / 100) * CAPACITY_WH;
            const hrs = storedWh / netW;
            const hh = Math.floor(hrs);
            const mm = Math.round((hrs - hh) * 60);
            html += `<p><strong>Battery:</strong> ${soc.toFixed(0)}% — draws ${Math.round(netW)}W net (${Math.round(totalWatts)}W − ${Math.round(solarW)}W solar) → ~${hh}h ${mm}m remaining</p>`;
          } else {
            const emptyWh = ((100 - soc) / 100) * CAPACITY_WH;
            const hrs = emptyWh / (-netW);
            const hh = Math.floor(hrs);
            const mm = Math.round((hrs - hh) * 60);
            html += `<p><strong>Battery:</strong> ${soc.toFixed(0)}% — surplus ${Math.round(-netW)}W (${Math.round(solarW)}W solar − ${Math.round(totalWatts)}W draw) → ~${hh}h ${mm}m to full</p>`;
          }
        }
      }

      if (canRenderCostEquation) {
        const rateKwh = Number(costContext.rateKwh);
        const costPerHr = (totalWatts / 1000) * rateKwh;
        const safeLabel = escapeHtml(costContext.label || "TOU");
        html += `<p><strong>Cost:</strong> ${Math.round(totalWatts)}W x $${rateKwh.toFixed(5)}/kWh (${safeLabel}) = $${costPerHr.toFixed(4)}/hr</p>`;
      }
    }
    html += buildPowerHistoryModalSection(resolution);
    html += buildDebugSection(payload);
    const structureKey = `${resolution && resolution.resolvedProfileId ? resolution.resolvedProfileId : ""}|${historyModalSectionKey}`;
    const modalOpen = elements.powerModalBackdrop.classList.contains("is-open");
    const hasHistoryChartNode = Boolean(elements.powerModalBody.querySelector(".power-history-chart"));
    const shouldPreserveDom = modalOpen && hasHistoryChartNode && structureKey === modalStructureKey;

    if (!shouldPreserveDom && elements.powerModalBody.innerHTML !== html) {
      disposeHistoryCharts();
      elements.powerModalBody.innerHTML = html;
      modalStructureKey = structureKey;
    }
    if (!shouldPreserveDom && elements.powerModalBody.innerHTML === html) {
      modalStructureKey = structureKey;
    }
    const ageCell = elements.powerModalBody.querySelector(".power-debug-age");
    if (ageCell) {
      if (payload && payload.power_reading_ts) {
        ageCell.dataset.readingTs = payload.power_reading_ts;
      }
      const rts = Number(ageCell.dataset.readingTs);
      if (rts) ageCell.textContent = `${(Date.now() / 1000 - rts).toFixed(3)}s ago`;
    }
    for (const cell of elements.powerModalBody.querySelectorAll(".power-debug-avg")) {
      const secs = Number(cell.dataset.secs);
      if (!secs) continue;
      const v = wattsAvg(secs);
      const n = wattsBuffer.filter(p => p.ts >= Date.now() / 1000 - secs).length;
      cell.textContent = v !== null ? `${v.toFixed(2)}W (n=${n})` : "—";
    }
    if (payload) {
      const isWallTotal = payload.power_measurement_kind === "wall-total";
      const fmt1 = (v) => (v === null || v === undefined || !Number.isFinite(Number(v))) ? "—" : `${Math.round(Number(v))}W`;
      const rawCell = elements.powerModalBody.querySelector(".power-debug-raw");
      if (rawCell) rawCell.textContent = isWallTotal ? `${fmt1(payload.estimated_total_watts)} (wall meter)` : fmt1(payload.measured_server_watts ?? payload.measured_gpu_watts);
      const totalCell = elements.powerModalBody.querySelector(".power-debug-total");
      if (totalCell) totalCell.textContent = fmt1(payload.estimated_total_watts);
      const concurrentCell = elements.powerModalBody.querySelector(".power-debug-concurrent");
      if (concurrentCell) {
        const r = Number.isFinite(Number(payload.requests_running)) ? Math.round(Number(payload.requests_running)) : 0;
        concurrentCell.textContent = `${r} running`;
      }
      const waitingCell = elements.powerModalBody.querySelector(".power-debug-waiting");
      if (waitingCell) {
        const w = Number.isFinite(Number(payload.requests_waiting)) ? Math.round(Number(payload.requests_waiting)) : 0;
        waitingCell.textContent = String(w);
      }
    }
    ensureHistoryChartsRendered(resolution);
  }

  function syncPowerProfileUi() {
    const settings = getSettings();
    const resolution = resolvePowerProfile(settings, state.powerTelemetry);
    if (!resolution.profile) return;

    if (elements.powerProfile.value !== resolution.selectedProfileId) {
      elements.powerProfile.value = resolution.selectedProfileId;
    }

    const usePerKwh = resolution.profile.costMode === "per-kwh";
    elements.costPerHrField.hidden = usePerKwh;
    elements.costPerKwhField.hidden = !usePerKwh;
    updatePowerModalBody(resolution, state.powerTelemetry, null);
  }

  function applyPowerProfileDefaults(profileId) {
    const settings = getSettings();
    const targetResolution = profileId === AUTO_PROFILE_ID
      ? resolvePowerProfile({ ...settings, powerProfile: AUTO_PROFILE_ID }, state.powerTelemetry)
      : resolvePowerProfile({ ...settings, powerProfile: profileId }, state.powerTelemetry);

    const targetProfile = targetResolution.profile;
    if (!targetProfile) return;
    const profileDefaults = targetProfile.defaults || {};

    if (Number.isFinite(profileDefaults.wattsIdle)) {
      elements.wattsIdle.value = String(profileDefaults.wattsIdle);
    }
    if (Number.isFinite(profileDefaults.wattsActive)) {
      elements.wattsActive.value = String(profileDefaults.wattsActive);
    }
    if (Number.isFinite(profileDefaults.gco2PerWh)) {
      elements.gco2PerWh.value = String(profileDefaults.gco2PerWh);
    }
    if (Number.isFinite(profileDefaults.costPerHr)) {
      elements.costPerHr.value = String(profileDefaults.costPerHr);
    }
    if (Number.isFinite(profileDefaults.costPerKwh)) {
      elements.costPerKwh.value = String(profileDefaults.costPerKwh);
    }

    syncPowerProfileUi();
    updatePowerDisplay(state.busy);
  }

  function pluralize(count, singular, plural) {
    return count === 1 ? singular : plural;
  }

  function formatActiveCountText(activeCount) {
    return `generating ${activeCount} ${pluralize(activeCount, "reply", "replies")}`;
  }

  function getPacificDateParts(now) {
    try {
      const formatter = new Intl.DateTimeFormat("en-US", {
        timeZone: "America/Los_Angeles",
        month: "numeric",
        hour: "numeric",
        hourCycle: "h23",
      });
      const parts = formatter.formatToParts(now);
      const month = Number.parseInt(parts.find((p) => p.type === "month")?.value || "", 10);
      const hour = Number.parseInt(parts.find((p) => p.type === "hour")?.value || "", 10);
      if (Number.isFinite(month) && Number.isFinite(hour)) {
        return { month, hour };
      }
    } catch (_err) {
      // Fall back to local clock if timezone conversion fails.
    }
    return { month: now.getMonth() + 1, hour: now.getHours() };
  }

  function resolveSdgeTouRate(now) {
    const { month, hour } = getPacificDateParts(now);
    const season = month >= 6 && month <= 10 ? "summer" : "winter";
    const isOnPeak = hour >= 16 && hour < 21;
    const periodKey = isOnPeak ? "onPeak" : "offPeak";
    return {
      rateKwh: SDGE_DR2_ALL_IN_RATES[season][periodKey],
      label: `${season === "summer" ? "Summer" : "Winter"} ${isOnPeak ? "On-Peak" : "Off-Peak"}`,
    };
  }

  function isSdTouProfile(profileId) {
    return profileId === "home-sd" || profileId === "eco-orin" || profileId === "pi-rpi4";
  }

  function computeCostPerHr(watts, settings, profile, profileId) {
    if (isSdTouProfile(profileId)) {
      const tou = resolveSdgeTouRate(new Date());
      return {
        value: Number.isFinite(watts) ? (watts / 1000) * tou.rateKwh : Number.NaN,
        context: {
          dynamicTou: true,
          label: tou.label,
          rateKwh: tou.rateKwh,
        },
      };
    }

    if (profile.costMode === "per-kwh") {
      return {
        value: Number.isFinite(watts) && Number.isFinite(settings.costPerKwh)
          ? (watts / 1000) * settings.costPerKwh
          : Number.NaN,
        context: null,
      };
    }
    return {
      value: settings.costPerHr,
      context: null,
    };
  }

  function formatCostPerHr(costPerHr) {
    if (!Number.isFinite(costPerHr)) return "--/hr";
    const absCost = Math.abs(costPerHr);
    const decimals = absCost < 0.05 ? 3 : 2;
    return `$${costPerHr.toFixed(decimals)}/hr`;
  }

  function formatWattsDisplay(watts) {
    if (!Number.isFinite(watts)) return "-- Watts";
    return `${Math.round(watts)} Watts`;
  }

  function renderFallbackPowerDisplay(active) {
    const settings = getSettings();
    const resolution = resolvePowerProfile(settings, state.powerTelemetry);
    const profileId = resolution.resolvedProfileId;
    const profile = resolution.profile;
    const watts = active ? settings.wattsActive : settings.wattsIdle;
    const co2PerHr = watts * settings.gco2PerWh;
    const costResult = computeCostPerHr(watts, settings, profile, profileId);
    const displayCost = costResult.value;
    const costContext = costResult.context;
    if (costContext && costContext.dynamicTou) {
      elements.costPerKwh.value = Number(costContext.rateKwh).toFixed(5);
    }
    elements.powerWatts.textContent = formatWattsDisplay(watts);
    elements.wattsLiveDot.hidden = true;
    elements.powerCo2.textContent = `${co2PerHr.toFixed(1)} gCO2/hr`;
    elements.powerCost.textContent = formatCostPerHr(displayCost);
    const activeCount = active ? 1 : 0;
    const activeCountText = formatActiveCountText(activeCount);
    elements.powerActiveCount.textContent = activeCountText;
    elements.activeCountHeader.textContent = activeCountText;
    elements.powerDisplay.classList.toggle("is-active", Boolean(active));
    updatePowerModalBody(resolution, state.powerTelemetry, costContext);
  }

  function setPowerTelemetryMode(mode, detail, payload) {
    if (state.powerTelemetryMode === mode) return;
    state.powerTelemetryMode = mode;
    if (detail) {
      console.warn(`[power] ${detail}`, payload || "");
    }
  }

  function renderTelemetryPowerDisplay(payload) {
    if (Number.isFinite(Number(payload.estimated_total_watts))) {
      wattsBufferPush(Number(payload.estimated_total_watts), payload.solix_reading_ts ?? payload.power_reading_ts);
    }
    const settings = getSettings();
    const resolution = resolvePowerProfile(settings, payload);
    const profileId = resolution.resolvedProfileId;
    const profile = resolution.profile;
    const telemetryTotalWatts = Number.parseFloat(String(payload.estimated_total_watts));
    const running = Number.parseFloat(String(payload.requests_running));
    const baseSystemWatts = Number.parseFloat(String(payload.base_system_watts));
    const measuredServerWatts = Number.parseFloat(String(payload.measured_server_watts ?? payload.measured_gpu_watts));
    const measuredGpuWatts = Number.parseFloat(String(payload.measured_gpu_watts));
    const activeCount = Number.isFinite(running) ? Math.max(0, Math.round(running)) : 0;

    let displayWatts = Number.NaN;
    if (profileId === "home-sd") {
      if (Number.isFinite(measuredGpuWatts)) {
        displayWatts = settings.wattsIdle + measuredGpuWatts;
        setPowerTelemetryMode("home-derived", "");
      } else if (Number.isFinite(telemetryTotalWatts)) {
        displayWatts = telemetryTotalWatts;
        setPowerTelemetryMode("home-total-fallback", "");
      } else {
        displayWatts = settings.wattsActive;
        setPowerTelemetryMode(
          "home-fallback",
          "Telemetry payload is missing measured_gpu_watts; falling back to local configured active watts.",
          payload
        );
      }
    } else if (profileId === "eco-orin") {
      if (Number.isFinite(telemetryTotalWatts)) {
        displayWatts = telemetryTotalWatts;
        setPowerTelemetryMode("eco-total", "");
      } else {
        displayWatts = settings.wattsActive;
        setPowerTelemetryMode(
          "eco-fallback",
          "Telemetry payload is missing estimated_total_watts; falling back to local configured active watts.",
          payload
        );
      }
    } else {
      const derivedBaseSystemWatts = Number.isFinite(baseSystemWatts) ? baseSystemWatts : 300;
      const legacyWatts = Number.isFinite(measuredGpuWatts)
        ? (derivedBaseSystemWatts + measuredGpuWatts) * (profile.overheadMultiplier || 1.35)
        : Number.NaN;
      if (Number.isFinite(telemetryTotalWatts)) {
        displayWatts = telemetryTotalWatts;
        setPowerTelemetryMode("native-total", "");
      } else if (Number.isFinite(legacyWatts)) {
        displayWatts = legacyWatts;
        setPowerTelemetryMode(
          "legacy-derived",
          "Telemetry payload is missing estimated_total_watts; deriving display watts from older payload fields.",
          payload
        );
      } else {
        displayWatts = settings.wattsActive;
        setPowerTelemetryMode(
          "legacy-fallback",
          "Telemetry payload is missing usable watt fields; falling back to local configured active watts.",
          payload
        );
      }
    }

    const co2PerHr = displayWatts * settings.gco2PerWh;
    const costResult = computeCostPerHr(displayWatts, settings, profile, profileId);
    const displayCost = costResult.value;
    const costContext = costResult.context;
    if (costContext && costContext.dynamicTou) {
      elements.costPerKwh.value = Number(costContext.rateKwh).toFixed(5);
    }
    elements.powerWatts.textContent = formatWattsDisplay(displayWatts);
    elements.wattsLiveDot.hidden = !payload.watts_is_live;
    if (profileId === "pi-rpi4" || profileId === "eco-orin") {
      const solarW = payload.solix_solar_input_w;
      const soc = payload.solix_soc_pct;
      elements.powerCo2.textContent = Number.isFinite(Number(solarW)) && solarW !== null ? `Solar: ${solarW}W in` : "Solar: -- W";
      elements.powerCost.textContent = Number.isFinite(Number(soc)) && soc !== null ? `${soc}% battery` : "--% battery";
      const hasSoc = Number.isFinite(Number(soc)) && soc !== null;
      const socNum = hasSoc ? Number(soc) : 0;
      document.documentElement.style.setProperty(
        "--solix-battery-fill",
        hasSoc ? `${soc}%` : "100%"
      );
      if (hasSoc) {
        const fillH = (socNum / 100) * 46;
        elements.batteryBadgeFill.setAttribute("y", (53 - fillH).toFixed(1));
        elements.batteryBadgeFill.setAttribute("height", fillH.toFixed(1));
      }
      elements.batteryBadgePct.textContent = hasSoc ? `${soc}%` : "";
      elements.batteryBadge.hidden = !hasSoc;
    } else {
      document.documentElement.style.setProperty("--solix-battery-fill", "100%");
      elements.batteryBadge.hidden = true;
      elements.powerCo2.textContent = `${co2PerHr.toFixed(1)} gCO2/hr`;
      elements.powerCost.textContent = formatCostPerHr(displayCost);
    }
    const activeCountText = formatActiveCountText(activeCount);
    elements.powerActiveCount.textContent = activeCountText;
    elements.activeCountHeader.textContent = activeCountText;
    elements.powerDisplay.classList.toggle("is-active", activeCount > 0);
    let modalPayload = payload;
    if (Number.isFinite(displayWatts)) {
      if (profileId === "eco-orin") {
        modalPayload = { ...payload, estimated_total_watts: displayWatts };
      } else if (profileId === "home-sd") {
        const homeMeasuredWatts = Number.isFinite(measuredGpuWatts) ? measuredGpuWatts : measuredServerWatts;
        if (Number.isFinite(homeMeasuredWatts)) {
          const homeBaseWatts = Math.max(0, displayWatts - homeMeasuredWatts);
          modalPayload = {
            ...payload,
            base_system_watts: homeBaseWatts,
            measured_server_watts: homeMeasuredWatts,
            estimated_total_watts: displayWatts,
          };
        }
      }
    }
    updatePowerModalBody(resolution, modalPayload, costContext);
  }

  function updatePowerDisplay(active) {
    if (state.powerTelemetryAvailable && state.powerTelemetry) {
      renderTelemetryPowerDisplay(state.powerTelemetry);
      return;
    }
    renderFallbackPowerDisplay(active);
  }

  function setBusy(next, label) {
    state.busy = Boolean(next);
    elements.sendButton.disabled = state.busy;
    const text = label || (state.busy ? "Busy" : "Idle");
    elements.statusPill.textContent = text;
    const showStatus = text && text.toLowerCase() !== "idle";
    elements.statusPill.classList.toggle("is-visible", showStatus);
    updatePowerDisplay(state.busy);
  }

  function authHeaders(apiKey) {
    const headers = {};
    if (apiKey) headers.Authorization = `Bearer ${apiKey}`;
    return headers;
  }

  async function refreshPowerHistory({ force = false } = {}) {
    if (state.powerHistoryInFlight) return;
    state.powerHistoryInFlight = true;

    try {
      const settings = getSettings();
      const baseUrl = getEffectiveBaseUrl(settings.baseUrl);
      const query = force ? "?refresh=1" : "";
      const response = await fetch(`${baseUrl}/telemetry/history${query}`, {
        method: "GET",
        headers: authHeaders(settings.apiKey),
        cache: "no-store",
      });

      if (!response.ok) {
        throw new Error(`History error: ${response.status}`);
      }

      const payload = await response.json();
      const hasHistory = Boolean(payload && payload.history_24h && payload.history_7d);
      if (!hasHistory) {
        throw new Error("History payload missing required windows");
      }

      state.powerHistory = payload;
      state.powerHistoryAvailable = true;
      state.powerHistoryLastFetchedAt = Date.now();
      updatePowerDisplay(state.busy);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err || "unknown error");
      console.warn(`[power] Telemetry history fetch failed. ${message}`);
      state.powerHistoryAvailable = false;
      updatePowerDisplay(state.busy);
    } finally {
      state.powerHistoryInFlight = false;
    }
  }

  async function refreshPowerTelemetry() {
    if (state.powerTelemetryInFlight) return;
    state.powerTelemetryInFlight = true;

    try {
      const settings = getSettings();
      const baseUrl = getEffectiveBaseUrl(settings.baseUrl);
      const response = await fetch(`${baseUrl}/telemetry/power`, {
        method: "GET",
        headers: authHeaders(settings.apiKey),
        cache: "no-store",
      });

      if (!response.ok) {
        throw new Error(`Telemetry error: ${response.status}`);
      }

      const payload = await response.json();
      state.powerTelemetry = payload;
      state.powerTelemetryAvailable = true;
      updatePowerDisplay(state.busy);
    } catch (err) {
      if (state.powerTelemetryMode !== "fetch-error") {
        const message = err instanceof Error ? err.message : String(err || "unknown error");
        console.warn(`[power] Telemetry fetch failed; using local fallback. ${message}`);
      }
      state.powerTelemetryMode = "fetch-error";
      state.powerTelemetryAvailable = false;
      updatePowerDisplay(state.busy);
    } finally {
      state.powerTelemetryInFlight = false;
    }
  }

  function startPowerTelemetryPolling() {
    if (state.powerTelemetryTimer) {
      window.clearInterval(state.powerTelemetryTimer);
    }

    void refreshPowerTelemetry();
    state.powerTelemetryTimer = window.setInterval(() => {
      if (!state.idle) {
        void refreshPowerTelemetry();
      }
    }, 1000);
  }

  function startPowerHistoryPolling() {
    if (state.powerHistoryTimer) {
      window.clearInterval(state.powerHistoryTimer);
    }

    void refreshPowerHistory();
    state.powerHistoryTimer = window.setInterval(() => {
      if (!state.idle || elements.powerModalBackdrop.classList.contains("is-open")) {
        void refreshPowerHistory();
      }
    }, POWER_HISTORY_REFRESH_MS);
  }

  function initIdleDetection() {
    const IDLE_MS = 5 * 60 * 1000;
    let idleTimeout = null;

    function onActive() {
      if (state.idle) {
        state.idle = false;
        void refreshPowerTelemetry();
      }
      window.clearTimeout(idleTimeout);
      idleTimeout = window.setTimeout(() => {
        state.idle = true;
      }, IDLE_MS);
    }

    function onVisibilityChange() {
      if (document.hidden) {
        window.clearTimeout(idleTimeout);
        state.idle = true;
      } else {
        onActive();
      }
    }

    const activityEvents = [
      "mousemove",
      "mousedown",
      "keydown",
      "touchstart",
      "scroll",
      "pointerdown",
    ];

    activityEvents.forEach((eventName) => {
      window.addEventListener(eventName, onActive, { passive: true });
    });
    document.addEventListener("visibilitychange", onVisibilityChange);

    idleTimeout = window.setTimeout(() => {
      state.idle = true;
    }, IDLE_MS);
  }

  function updateMobileHallucination() {
    const keyboardOpen = document.body.classList.contains("keyboard-open");
    elements.mobileHallucination.classList.toggle(
      "is-hidden",
      elements.advancedPanel.open || keyboardOpen
    );
  }

  function updateViewportMetrics() {
    const vv = window.visualViewport;
    if (!vv) {
      document.documentElement.style.setProperty("--app-height", "100dvh");
      document.documentElement.style.setProperty("--keyboard-offset", "0px");
      document.body.classList.remove("keyboard-open");
      updateMobileHallucination();
      return;
    }

    const safeOffsetTop = Math.max(0, vv.offsetTop);
    const viewportHeight = Math.round(vv.height + safeOffsetTop);
    document.documentElement.style.setProperty("--app-height", `${viewportHeight}px`);

    const keyboardOffset = Math.max(0, Math.round(window.innerHeight - vv.height));
    const active = document.activeElement;
    const isTextFocused = Boolean(
      active &&
      (
        active.matches(
          "textarea, input[type='text'], input[type='search'], input[type='email'], input[type='url'], input[type='tel'], input[type='number'], input[type='password']"
        ) ||
        active.isContentEditable
      )
    );

    const keyboardOpen = keyboardOffset > 120 || (isTextFocused && keyboardOffset > 24);
    document.documentElement.style.setProperty(
      "--keyboard-offset",
      keyboardOpen ? `${keyboardOffset}px` : "0px"
    );
    document.body.classList.toggle("keyboard-open", keyboardOpen);
    updateMobileHallucination();
  }

  function scheduleViewportMetricsUpdate() {
    if (viewportMetricsRaf) return;
    viewportMetricsRaf = window.requestAnimationFrame(() => {
      viewportMetricsRaf = 0;
      updateViewportMetrics();
      if (elements.powerModalBackdrop.classList.contains("is-open")) {
        scheduleHistoryChartResize();
      }
    });
  }

  function toggleConnectionSettings(event) {
    event.preventDefault();
    event.stopPropagation();
    elements.connectionSettings.open = !elements.connectionSettings.open;
  }

  function loadEcharts() {
    if (typeof window !== "undefined" && window.echarts) return Promise.resolve();
    if (echartsLoadPromise) return echartsLoadPromise;
    echartsLoadPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js";
      script.onload = resolve;
      script.onerror = reject;
      document.head.appendChild(script);
    });
    return echartsLoadPromise;
  }

  function openPowerModal() {
    elements.powerModalBackdrop.classList.add("is-open");
    const stale =
      !state.powerHistoryLastFetchedAt ||
      (Date.now() - state.powerHistoryLastFetchedAt) > POWER_HISTORY_REFRESH_MS;
    if (stale) {
      void refreshPowerHistory();
    }
    updatePowerDisplay(state.busy);
    scheduleHistoryChartResize();
    void loadEcharts().then(() => {
      if (elements.powerModalBackdrop.classList.contains("is-open")) {
        updatePowerDisplay(state.busy);
      }
    });
  }

  function closePowerModal() {
    elements.powerModalBackdrop.classList.remove("is-open");
    disposeHistoryCharts();
  }

  return {
    syncPowerProfileUi,
    applyPowerProfileDefaults,
    updatePowerDisplay,
    setBusy,
    refreshPowerTelemetry,
    refreshPowerHistory,
    startPowerTelemetryPolling,
    startPowerHistoryPolling,
    initIdleDetection,
    updateMobileHallucination,
    scheduleViewportMetricsUpdate,
    toggleConnectionSettings,
    openPowerModal,
    closePowerModal,
  };
}
