import {
  DEFAULT_BASE_URL,
  ECO_BASE_URL,
  SETTINGS_KEY,
  POWER_PROFILES,
  defaults,
} from "./config.js?v=20260320a1";

export function coercePositiveInt(value, fallback) {
  const parsed = Number.parseInt(String(value), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export function coerceFloat(value, fallback) {
  const parsed = Number.parseFloat(String(value));
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function getDeploymentDefaultBaseUrl() {
  const hostname =
    typeof window !== "undefined" && window.location && window.location.hostname
      ? String(window.location.hostname).toLowerCase()
      : "";
  const origin =
    typeof window !== "undefined" && window.location && window.location.origin
      ? String(window.location.origin).replace(/\/+$/, "")
      : "";

  if (hostname === "eco.bartlebygpt.org") {
    return ECO_BASE_URL;
  }
  if (hostname === "pi.bartlebygpt.org" && origin) {
    return origin;
  }
  return DEFAULT_BASE_URL;
}

function getDeploymentScope() {
  const hostname =
    typeof window !== "undefined" && window.location && window.location.hostname
      ? String(window.location.hostname).toLowerCase()
      : "";
  if (hostname === "pi.bartlebygpt.org") {
    return "pi";
  }
  if (hostname === "eco.bartlebygpt.org" || hostname === "apij.bartlebygpt.org") {
    return "eco";
  }
  if (hostname === "api.bartlebygpt.org") {
    return "api";
  }
  return "default";
}

function getScopedSettingsKey() {
  return `${SETTINGS_KEY}:${getDeploymentScope()}`;
}

function normalizeStoredBaseUrl(baseUrl) {
  const scope = getDeploymentScope();
  const candidate = String(baseUrl || "").trim();
  if (!candidate) return "";

  try {
    const parsed = new URL(candidate);
    const hostname = parsed.hostname.toLowerCase();
    if (scope === "eco" && hostname === "apij.bartlebygpt.org") {
      return "";
    }
  } catch (_err) {
    return "";
  }

  return candidate.replace(/\/+$/, "");
}

function getDeploymentDefaults() {
  const deploymentBaseUrl = getDeploymentDefaultBaseUrl();
  const deploymentScope = getDeploymentScope();
  let resolvedAutoProfileId = "home-sd";
  if (deploymentScope === "api" || deploymentBaseUrl === ECO_BASE_URL) {
    resolvedAutoProfileId = "eco-orin";
  } else if (deploymentScope === "pi") {
    resolvedAutoProfileId = "pi-rpi4";
  }
  const resolvedAutoProfile = POWER_PROFILES[resolvedAutoProfileId] || {};
  const resolvedDefaults = resolvedAutoProfile.defaults || {};
  return {
    ...defaults,
    baseUrl: "",
    powerProfile: "auto-live",
    wattsIdle: Number.isFinite(resolvedDefaults.wattsIdle) ? resolvedDefaults.wattsIdle : defaults.wattsIdle,
    wattsActive: Number.isFinite(resolvedDefaults.wattsActive) ? resolvedDefaults.wattsActive : defaults.wattsActive,
    gco2PerWh: Number.isFinite(resolvedDefaults.gco2PerWh) ? resolvedDefaults.gco2PerWh : defaults.gco2PerWh,
    costPerKwh: Number.isFinite(resolvedDefaults.costPerKwh) ? resolvedDefaults.costPerKwh : defaults.costPerKwh,
  };
}

function copySharedLegacySettings(parsed, deploymentDefaults) {
  const migrated = { ...deploymentDefaults };
  const sharedKeys = [
    "modelName",
    "apiKey",
    "systemPrompt",
    "maxInputChars",
    "maxNewTokens",
    "requestTimeout",
    "temperature",
    "topP",
  ];
  sharedKeys.forEach((key) => {
    if (Object.prototype.hasOwnProperty.call(parsed, key)) {
      migrated[key] = parsed[key];
    }
  });
  return migrated;
}

export function getEffectiveBaseUrl(baseUrl) {
  const candidate = String(baseUrl || "").trim().replace(/\/+$/, "");
  if (!candidate) {
    return getDeploymentDefaultBaseUrl();
  }

  let parsed;
  try {
    parsed = new URL(candidate);
  } catch (_err) {
    throw new Error("Invalid base URL. Use a full http:// or https:// URL.");
  }

  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error("Invalid base URL. Use an http:// or https:// URL.");
  }
  return `${parsed.origin}${parsed.pathname}`.replace(/\/+$/, "");
}

export function createSettingsController(elements) {
  function loadSettings() {
    try {
      const deploymentDefaults = getDeploymentDefaults();
      const scopedKey = getScopedSettingsKey();
      let raw = localStorage.getItem(scopedKey);
      let fromLegacy = false;
      if (!raw) {
        const legacyRaw = localStorage.getItem(SETTINGS_KEY);
        if (legacyRaw) {
          raw = legacyRaw;
          fromLegacy = true;
        }
      }
      if (!raw) return deploymentDefaults;

      const parsed = JSON.parse(raw);
      const merged = fromLegacy
        ? copySharedLegacySettings(parsed, deploymentDefaults)
        : { ...deploymentDefaults, ...parsed };
      let needsSave = false;

      if (!merged.powerProfile || !POWER_PROFILES[merged.powerProfile]) {
        merged.powerProfile = deploymentDefaults.powerProfile;
        needsSave = true;
      }

      const normalizedBaseUrl = normalizeStoredBaseUrl(merged.baseUrl);
      if (normalizedBaseUrl !== String(merged.baseUrl || "").trim()) {
        merged.baseUrl = normalizedBaseUrl;
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
          localStorage.setItem(scopedKey, JSON.stringify(merged));
        } catch (_err) {}
      }

      if (fromLegacy) {
        try {
          localStorage.setItem(scopedKey, JSON.stringify(merged));
        } catch (_err) {}
      }

      return merged;
    } catch (_err) {
      return getDeploymentDefaults();
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
      colorWarmth: coerceFloat(elements.colorWarmth.value, defaults.colorWarmth),
    };
  }

  function saveSettings() {
    const settings = getSettings();
    try {
      localStorage.setItem(getScopedSettingsKey(), JSON.stringify(settings));
      return true;
    } catch (_err) {
      return false;
    }
  }

  function applySettings(settings) {
    const deploymentDefaultBaseUrl = getDeploymentDefaultBaseUrl();
    const visibleBaseUrl = String(settings.baseUrl || "").trim();
    elements.baseUrl.value = visibleBaseUrl === deploymentDefaultBaseUrl ? "" : visibleBaseUrl;
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
    elements.colorWarmth.value = settings.colorWarmth ?? defaults.colorWarmth;
  }

  return {
    loadSettings,
    getSettings,
    saveSettings,
    applySettings,
  };
}
