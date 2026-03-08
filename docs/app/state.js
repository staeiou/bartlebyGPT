export function createState() {
  return {
    busy: false,
    history: [],
    messages: [],
    modelCache: {},
    turnCount: 0,
    goatStartSent: false,
    powerTelemetry: null,
    powerTelemetryAvailable: false,
    powerTelemetryInFlight: false,
    powerTelemetryTimer: null,
    powerTelemetryMode: "",
    idle: false,
  };
}
