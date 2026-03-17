import { POWER_PROFILES, defaults } from "./config.js?v=20260314e9";
import { getEffectiveBaseUrl } from "./settings.js?v=20260314e9";

export function createPowerController({ elements, state, getSettings }) {
  let viewportMetricsRaf = 0;
  const AUTO_PROFILE_ID = "auto-live";
  const ECO_TOTAL_INTERCEPT_WATTS = 5;
  const ECO_TOTAL_SLOPE = 1.5;
  const ECO_TOTAL_MIN_WATTS = 8;
  const ECO_TOTAL_MAX_WATTS = 21;
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
    if (appHostname === "api.bartlebygpt.org") {
      return "home-sd";
    }

    const effectiveBaseUrl = getEffectiveBaseUrl(settings.baseUrl);
    const hostname = parseHostnameFromBaseUrl(effectiveBaseUrl);
    if (hostname === "eco.bartlebygpt.org" || hostname === "apij.bartlebygpt.org") {
      return "eco-orin";
    }
    if (hostname === "api.bartlebygpt.org") {
      return "home-sd";
    }
    return "home-sd";
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
    const resolvedProfile = POWER_PROFILES[resolvedProfileId] || POWER_PROFILES["home-sd"] || POWER_PROFILES[defaults.powerProfile];
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
   //     html += `<hr><p><strong>Power:</strong> ${displayBaseWatts.toFixed(2)}W base + ${measuredServerWatts.toFixed(2)}W load = ${totalWatts.toFixed(2)}W</p>`;
        html += `<hr><p><strong>Power:</strong> ${totalWatts.toFixed(2)}W</p>`;

      }
      if (canRenderCostEquation) {
        const rateKwh = Number(costContext.rateKwh);
        const costPerHr = (totalWatts / 1000) * rateKwh;
        const safeLabel = escapeHtml(costContext.label || "TOU");
        html += `<p><strong>Cost:</strong> ${totalWatts.toFixed(2)}W x $${rateKwh.toFixed(5)}/kWh (${safeLabel}) = $${costPerHr.toFixed(4)}/hr</p>`;
      }
    }
    elements.powerModalBody.innerHTML = html;
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
    return profileId === "home-sd" || profileId === "eco-orin";
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
    const rounded = Math.round(watts * 10) / 10;
    return `${rounded.toFixed(1)} Watts`;
  }

  function estimateEcoBoardWattsFromMeasured(measuredLoadWatts) {
    if (!Number.isFinite(measuredLoadWatts)) return Number.NaN;
    const boardWatts = ECO_TOTAL_INTERCEPT_WATTS + (ECO_TOTAL_SLOPE * measuredLoadWatts);
    return Math.max(ECO_TOTAL_MIN_WATTS, Math.min(ECO_TOTAL_MAX_WATTS, boardWatts));
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
      if (Number.isFinite(measuredServerWatts)) {
        displayWatts = estimateEcoBoardWattsFromMeasured(measuredServerWatts);
        setPowerTelemetryMode("eco-linear", "");
      } else if (Number.isFinite(telemetryTotalWatts)) {
        displayWatts = Math.max(ECO_TOTAL_MIN_WATTS, Math.min(ECO_TOTAL_MAX_WATTS, telemetryTotalWatts));
        setPowerTelemetryMode("eco-total-clamped", "");
      } else if (Number.isFinite(baseSystemWatts) && Number.isFinite(measuredGpuWatts)) {
        displayWatts = Math.max(ECO_TOTAL_MIN_WATTS, Math.min(ECO_TOTAL_MAX_WATTS, baseSystemWatts + measuredGpuWatts));
        setPowerTelemetryMode("eco-derived-clamped", "");
      } else if (Number.isFinite(measuredGpuWatts)) {
        displayWatts = estimateEcoBoardWattsFromMeasured(measuredGpuWatts);
        setPowerTelemetryMode("eco-gpu-linear", "");
      } else {
        displayWatts = Math.max(ECO_TOTAL_MIN_WATTS, Math.min(ECO_TOTAL_MAX_WATTS, settings.wattsActive));
        setPowerTelemetryMode(
          "eco-fallback",
          "Telemetry payload is missing usable watt fields; falling back to local configured active watts.",
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
    elements.powerCo2.textContent = `${co2PerHr.toFixed(1)} gCO2/hr`;
    elements.powerCost.textContent = formatCostPerHr(displayCost);
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
    });
  }

  function toggleConnectionSettings(event) {
    event.preventDefault();
    event.stopPropagation();
    elements.connectionSettings.open = !elements.connectionSettings.open;
  }

  function openPowerModal() {
    elements.powerModalBackdrop.classList.add("is-open");
  }

  function closePowerModal() {
    elements.powerModalBackdrop.classList.remove("is-open");
  }

  return {
    syncPowerProfileUi,
    applyPowerProfileDefaults,
    updatePowerDisplay,
    setBusy,
    refreshPowerTelemetry,
    startPowerTelemetryPolling,
    initIdleDetection,
    updateMobileHallucination,
    scheduleViewportMetricsUpdate,
    toggleConnectionSettings,
    openPowerModal,
    closePowerModal,
  };
}
