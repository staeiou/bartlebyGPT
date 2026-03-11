import { POWER_PROFILES, defaults } from "./config.js";
import { getEffectiveBaseUrl } from "./settings.js";

export function createPowerController({ elements, state, getSettings }) {
  let viewportMetricsRaf = 0;

  function getPowerProfileId(settings) {
    const candidate = String(settings.powerProfile || "").trim();
    if (candidate && POWER_PROFILES[candidate]) return candidate;
    return defaults.powerProfile;
  }

  function getPowerProfile(settings) {
    const profileId = getPowerProfileId(settings);
    return POWER_PROFILES[profileId] || POWER_PROFILES[defaults.powerProfile];
  }

  function syncPowerProfileUi() {
    const settings = getSettings();
    const profileId = getPowerProfileId(settings);
    const profile = POWER_PROFILES[profileId];
    if (!profile) return;

    if (elements.powerProfile.value !== profileId) {
      elements.powerProfile.value = profileId;
    }

    const usePerKwh = profile.costMode === "per-kwh";
    elements.costPerHrField.hidden = usePerKwh;
    elements.costPerKwhField.hidden = !usePerKwh;
    elements.powerModalBody.innerHTML = profile.modalHtml || "";
  }

  function applyPowerProfileDefaults(profileId) {
    const profile = POWER_PROFILES[profileId];
    if (!profile) return;
    const profileDefaults = profile.defaults || {};

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

  function computeCostPerHr(watts, settings, profile) {
    if (profile.costMode === "per-kwh") {
      return Number.isFinite(watts) && Number.isFinite(settings.costPerKwh)
        ? (watts / 1000) * settings.costPerKwh
        : Number.NaN;
    }
    return settings.costPerHr;
  }

  function renderFallbackPowerDisplay(active) {
    const settings = getSettings();
    const profile = getPowerProfile(settings);
    const watts = active ? settings.wattsActive : settings.wattsIdle;
    const co2PerHr = watts * settings.gco2PerWh;
    const displayCost = computeCostPerHr(watts, settings, profile);
    elements.powerWatts.textContent = `${Math.round(watts)} Watts`;
    elements.powerCo2.textContent = `${co2PerHr.toFixed(1)} gCO2/hr`;
    elements.powerCost.textContent = Number.isFinite(displayCost) ? `$${displayCost.toFixed(2)}/hr` : "--/hr";
    const activeCount = active ? 1 : 0;
    const activeCountText = formatActiveCountText(activeCount);
    elements.powerActiveCount.textContent = activeCountText;
    elements.activeCountHeader.textContent = activeCountText;
    elements.powerDisplay.classList.toggle("is-active", Boolean(active));
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
    const profileId = getPowerProfileId(settings);
    const profile = getPowerProfile(settings);
    const telemetryTotalWatts = Number.parseFloat(String(payload.estimated_total_watts));
    const running = Number.parseFloat(String(payload.requests_running));
    const baseSystemWatts = Number.parseFloat(String(payload.base_system_watts));
    const measuredGpuWatts = Number.parseFloat(String(payload.measured_gpu_watts));
    const activeCount = Number.isFinite(running) ? Math.max(0, Math.round(running)) : 0;

    let displayWatts = Number.NaN;
    if (profileId === "home-sd") {
      if (Number.isFinite(measuredGpuWatts)) {
        displayWatts = settings.wattsIdle + measuredGpuWatts;
        setPowerTelemetryMode("home-derived", "");
      } else {
        displayWatts = settings.wattsActive;
        setPowerTelemetryMode(
          "home-fallback",
          "Telemetry payload is missing measured_gpu_watts; falling back to local configured active watts.",
          payload
        );
      }
    } else {
      const derivedBaseSystemWatts = Number.isFinite(baseSystemWatts) ? baseSystemWatts : 300;
      const legacyWatts = Number.isFinite(measuredGpuWatts)
        ? (derivedBaseSystemWatts + measuredGpuWatts) * (profile.overheadMultiplier || 1.35)
        : Number.NaN;
      displayWatts = Number.isFinite(telemetryTotalWatts)
        ? telemetryTotalWatts
        : (Number.isFinite(legacyWatts) ? legacyWatts : settings.wattsActive);

      if (Number.isFinite(telemetryTotalWatts)) {
        setPowerTelemetryMode("native", "");
      } else if (Number.isFinite(legacyWatts)) {
        setPowerTelemetryMode(
          "legacy-derived",
          "Telemetry payload is missing estimated_total_watts; deriving display watts from older payload fields.",
          payload
        );
      } else {
        setPowerTelemetryMode(
          "legacy-fallback",
          "Telemetry payload is missing usable watt fields; falling back to local configured active watts.",
          payload
        );
      }
    }

    const co2PerHr = displayWatts * settings.gco2PerWh;
    const displayCost = computeCostPerHr(displayWatts, settings, profile);
    elements.powerWatts.textContent = `${Math.round(displayWatts)} Watts`;
    elements.powerCo2.textContent = `${co2PerHr.toFixed(1)} gCO2/hr`;
    elements.powerCost.textContent = Number.isFinite(displayCost) ? `$${displayCost.toFixed(2)}/hr` : "--/hr";
    const activeCountText = formatActiveCountText(activeCount);
    elements.powerActiveCount.textContent = activeCountText;
    elements.activeCountHeader.textContent = activeCountText;
    elements.powerDisplay.classList.toggle("is-active", activeCount > 0);
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
