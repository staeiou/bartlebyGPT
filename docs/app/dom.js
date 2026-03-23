const ELEMENT_IDS = {
  messages: "messages",
  resetChatButton: "reset-chat-button",
  statusPill: "status-pill",
  input: "message-input",
  sendButton: "send-button",
  inputCount: "input-count",
  inputHint: "input-hint",
  baseUrl: "base-url",
  modelName: "model-name",
  apiKey: "api-key",
  systemPrompt: "system-prompt",
  maxInputChars: "max-input-chars",
  maxNewTokens: "max-new-tokens",
  requestTimeout: "request-timeout",
  temperature: "temperature",
  topP: "top-p",
  powerProfile: "power-profile",
  wattsIdle: "watts-idle",
  wattsActive: "watts-active",
  gco2PerWh: "gco2-per-wh",
  costPerHr: "cost-per-hr",
  costPerKwh: "cost-per-kwh",
  costPerHrField: "cost-per-hr-field",
  costPerKwhField: "cost-per-kwh-field",
  powerDisplay: "power-display",
  powerWatts: "power-watts",
  wattsLiveDot: "watts-live-dot",
  powerCo2: "power-co2",
  powerCost: "power-cost",
  powerActiveCount: "power-active-count",
  activeCountHeader: "active-count-header",
  powerInfoBtn: "power-info-btn",
  advancedPanel: "advanced-panel",
  connectionSettings: "connection-settings",
  connectionToggle: "connection-toggle",
  mobileHallucination: "mobile-hallucination",
  batteryBadge: "battery-badge",
  batteryBadgeFill: "battery-badge-fill",
  batteryBadgePct: "battery-badge-pct",
  powerModalBackdrop: "power-modal-backdrop",
  powerModalClose: "power-modal-close",
  powerModalTitle: "power-modal-title",
  powerModalBody: "power-modal-body",
  colorWarmth: "color-warmth",
};

export function getElements(doc = document) {
  const elements = {};
  for (const [key, id] of Object.entries(ELEMENT_IDS)) {
    const node = doc.getElementById(id);
    if (!node) {
      throw new Error(`Missing required DOM node: #${id}`);
    }
    elements[key] = node;
  }
  return elements;
}
