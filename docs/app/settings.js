import {
  DEFAULT_BASE_URL,
  SETTINGS_KEY,
  POWER_PROFILES,
  defaults,
} from "./config.js";

export function coercePositiveInt(value, fallback) {
  const parsed = Number.parseInt(String(value), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export function coerceFloat(value, fallback) {
  const parsed = Number.parseFloat(String(value));
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function getEffectiveBaseUrl(baseUrl) {
  const candidate = String(baseUrl || "").trim().replace(/\/+$/, "");
  if (!candidate) {
    return DEFAULT_BASE_URL;
  }

  try {
    const parsed = new URL(candidate);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      throw new Error("Unsupported protocol");
    }
    return `${parsed.origin}${parsed.pathname}`.replace(/\/+$/, "");
  } catch (_err) {
    return DEFAULT_BASE_URL;
  }
}

export function createSettingsController(elements) {
  function loadSettings() {
    try {
      const raw = localStorage.getItem(SETTINGS_KEY);
      if (!raw) return { ...defaults };

      const parsed = JSON.parse(raw);
      const merged = { ...defaults, ...parsed };
      let needsSave = false;

      if (!merged.powerProfile || !POWER_PROFILES[merged.powerProfile]) {
        merged.powerProfile = defaults.powerProfile;
        needsSave = true;
      }

      if (!parsed || typeof parsed.powerProfile !== "string") {
        const profile = POWER_PROFILES[merged.powerProfile];
        const profileDefaults = profile && profile.defaults ? profile.defaults : null;
        if (profileDefaults) {
          if (profileDefaults.wattsIdle !== undefined) merged.wattsIdle = profileDefaults.wattsIdle;
          if (profileDefaults.wattsActive !== undefined) merged.wattsActive = profileDefaults.wattsActive;
          if (profileDefaults.gco2PerWh !== undefined) merged.gco2PerWh = profileDefaults.gco2PerWh;
          if (profileDefaults.costPerHr !== undefined) merged.costPerHr = profileDefaults.costPerHr;
          if (profileDefaults.costPerKwh !== undefined) merged.costPerKwh = profileDefaults.costPerKwh;
          needsSave = true;
        }
      }

      if (needsSave) {
        try {
          localStorage.setItem(SETTINGS_KEY, JSON.stringify(merged));
        } catch (_err) {}
      }

      return merged;
    } catch (_err) {
      return { ...defaults };
    }
  }

  function getSettings() {
    return {
      baseUrl: elements.baseUrl.value.trim().replace(/\/+$/, ""),
      modelName: elements.modelName.value.trim(),
      apiKey: elements.apiKey.value.trim(),
      systemPrompt: elements.systemPrompt.value,
      maxInputChars: coercePositiveInt(elements.maxInputChars.value, defaults.maxInputChars),
      maxNewTokens: coercePositiveInt(elements.maxNewTokens.value, defaults.maxNewTokens),
      requestTimeout: coercePositiveInt(elements.requestTimeout.value, defaults.requestTimeout),
      temperature: coerceFloat(elements.temperature.value, defaults.temperature),
      topP: coerceFloat(elements.topP.value, defaults.topP),
      powerProfile: elements.powerProfile.value,
      wattsIdle: coerceFloat(elements.wattsIdle.value, defaults.wattsIdle),
      wattsActive: coerceFloat(elements.wattsActive.value, defaults.wattsActive),
      gco2PerWh: coerceFloat(elements.gco2PerWh.value, defaults.gco2PerWh),
      costPerHr: coerceFloat(elements.costPerHr.value, defaults.costPerHr),
      costPerKwh: coerceFloat(elements.costPerKwh.value, defaults.costPerKwh),
    };
  }

  function saveSettings() {
    const settings = getSettings();
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  }

  function applySettings(settings) {
    const visibleBaseUrl = String(settings.baseUrl || "").trim();
    elements.baseUrl.value = visibleBaseUrl === DEFAULT_BASE_URL ? "" : visibleBaseUrl;
    elements.modelName.value = settings.modelName;
    elements.apiKey.value = settings.apiKey;
    elements.systemPrompt.value = settings.systemPrompt;
    elements.maxInputChars.value = settings.maxInputChars;
    elements.maxNewTokens.value = settings.maxNewTokens;
    elements.requestTimeout.value = settings.requestTimeout;
    elements.temperature.value = settings.temperature;
    elements.topP.value = settings.topP;
    elements.powerProfile.value = settings.powerProfile;
    elements.wattsIdle.value = settings.wattsIdle;
    elements.wattsActive.value = settings.wattsActive;
    elements.gco2PerWh.value = settings.gco2PerWh;
    elements.costPerHr.value = settings.costPerHr;
    elements.costPerKwh.value = settings.costPerKwh;
  }

  return {
    loadSettings,
    getSettings,
    saveSettings,
    applySettings,
  };
}
