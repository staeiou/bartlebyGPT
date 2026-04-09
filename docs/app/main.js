import { createAnalytics } from "./analytics.js?v=20260409a1";
import { createChatApi } from "./chat-api.js?v=20260409a1";
import { createChatUi } from "./chat-ui.js?v=20260409a1";
import { getElements } from "./dom.js?v=20260409a1";
import { createPowerController } from "./power.js?v=20260409a1";
import { createSettingsController } from "./settings.js?v=20260409a1";
import { createState } from "./state.js?v=20260409a1";
import { createWelcomeController } from "./welcome.js?v=20260409a1";

const elements = getElements();
const state = createState();
const analytics = createAnalytics(state);
const settings = createSettingsController(elements);
const power = createPowerController({
  elements,
  state,
  getSettings: settings.getSettings,
});
const chatApi = createChatApi({
  state,
  gcCount: analytics.gcCount,
});

let chatUi;
const welcome = createWelcomeController({
  elements,
  state,
  updateInputCount() {
    if (chatUi) {
      chatUi.updateInputCount();
    }
  },
});

chatUi = createChatUi({
  elements,
  state,
  settings,
  analytics,
  power,
  welcome,
  chatApi,
});

elements.input.addEventListener("input", () => {
  chatUi.updateInputCount();
});

elements.input.addEventListener(
  "touchmove",
  (event) => {
    if (elements.input.scrollHeight > elements.input.clientHeight) {
      event.stopPropagation();
    }
  },
  { passive: true }
);

elements.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    void chatUi.handleSend();
  }
});

elements.input.addEventListener("touchend", () => {
  if (
    document.activeElement === elements.input &&
    !document.body.classList.contains("keyboard-open")
  ) {
    elements.input.blur();
    window.setTimeout(() => {
      elements.input.focus();
      power.scheduleViewportMetricsUpdate();
    }, 0);
  } else {
    power.scheduleViewportMetricsUpdate();
  }
});

elements.sendButton.addEventListener("click", () => {
  void chatUi.handleSend();
});

elements.resetChatButton.addEventListener("click", () => {
  chatUi.clearChat();
});

elements.advancedPanel.addEventListener("toggle", () => {
  power.updateMobileHallucination();
});

elements.connectionToggle.addEventListener("click", (event) => {
  power.toggleConnectionSettings(event);
});

document.addEventListener("focusin", () => {
  power.scheduleViewportMetricsUpdate();
});

document.addEventListener("focusout", () => {
  window.setTimeout(() => {
    power.scheduleViewportMetricsUpdate();
  }, 120);
});

window.addEventListener("orientationchange", () => {
  power.scheduleViewportMetricsUpdate();
});

if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", () => {
    power.scheduleViewportMetricsUpdate();
  });
}

window.addEventListener("resize", () => {
  welcome.requestWelcomeCardFit();
  power.scheduleViewportMetricsUpdate();
});

[
  elements.baseUrl,
  elements.modelName,
  elements.apiKey,
  elements.systemPrompt,
  elements.maxInputChars,
  elements.maxNewTokens,
  elements.requestTimeout,
  elements.temperature,
  elements.topP,
  elements.wattsIdle,
  elements.wattsActive,
  elements.gco2PerWh,
  elements.costPerHr,
  elements.costPerKwh,
  elements.colorWarmth,
].forEach((element) => {
  element.addEventListener("change", () => {
    settings.saveSettings();
  });
  element.addEventListener("blur", () => {
    settings.saveSettings();
  });
});

elements.colorWarmth.addEventListener("input", () => {
  document.documentElement.style.setProperty("--color-warmth", elements.colorWarmth.value);
  settings.saveSettings();
});

elements.powerProfile.addEventListener("change", () => {
  power.applyPowerProfileDefaults(elements.powerProfile.value);
  settings.saveSettings();
});

[elements.baseUrl, elements.apiKey].forEach((element) => {
  element.addEventListener("change", () => {
    void power.refreshPowerTelemetry();
  });
  element.addEventListener("blur", () => {
    void power.refreshPowerTelemetry();
  });
});

[
  elements.wattsIdle,
  elements.wattsActive,
  elements.gco2PerWh,
  elements.costPerHr,
  elements.costPerKwh,
].forEach((element) => {
  element.addEventListener("input", () => {
    power.updatePowerDisplay(state.busy);
  });
});

elements.powerInfoBtn.addEventListener("click", () => {
  power.openPowerModal();
});

elements.batteryBadge.addEventListener("click", () => {
  power.openPowerModal();
});

elements.powerDisplay.addEventListener("click", (event) => {
  if (event.target !== elements.powerInfoBtn) {
    power.openPowerModal();
  }
});

elements.powerModalClose.addEventListener("click", () => {
  power.closePowerModal();
});

elements.powerModalBackdrop.addEventListener("click", (event) => {
  if (event.target === elements.powerModalBackdrop) {
    power.closePowerModal();
  }
});

state.turnCount = analytics.loadTurnCount();
settings.applySettings(settings.loadSettings());
document.documentElement.style.setProperty("--color-warmth", elements.colorWarmth.value);
power.scheduleViewportMetricsUpdate();
power.syncPowerProfileUi();
chatUi.updateInputCount();
power.updateMobileHallucination();
power.initIdleDetection();
power.startPowerTelemetryPolling();
power.startPowerHistoryPolling();
power.updatePowerDisplay(false);
analytics.goatStart();
welcome.initCardRotation();
welcome.renderWelcome();
