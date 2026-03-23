import { POWER_PROFILES, defaults } from '$lib/constants/bartleby-power-profiles';

export interface BartlebyPowerSettings {
	powerProfile: string;
	wattsIdle: number;
	wattsActive: number;
	gco2PerWh: number;
	costPerHr: number;
	costPerKwh: number;
	baseUrl?: string;
}

export interface BartlebyPowerMetrics {
	watts: number;
	co2PerHr: number;
	costPerHr: number;
	activeCount: number;
	activeCountText: string;
	costLabel?: string;
}

export interface BartlebyPowerResolution {
	selectedProfileId: string;
	resolvedProfileId: string;
	profile: any;
	isAuto: boolean;
}

const AUTO_PROFILE_ID = 'auto-live';
const ECO_TOTAL_INTERCEPT_WATTS = 5;
const ECO_TOTAL_SLOPE = 1.5;
const ECO_TOTAL_MIN_WATTS = 8;
const ECO_TOTAL_MAX_WATTS = 21;

const SDGE_DR2_ALL_IN_RATES = {
	summer: {
		onPeak: 0.70103,
		offPeak: 0.42936
	},
	winter: {
		onPeak: 0.622,
		offPeak: 0.48485
	}
};

function parseHostnameFromUrl(url: string): string {
	try {
		return new URL(url).hostname.toLowerCase();
	} catch {
		return '';
	}
}

function getSelectedPowerProfileId(settings: BartlebyPowerSettings): string {
	const candidate = String(settings.powerProfile || '').trim();
	if (candidate && POWER_PROFILES[candidate]) return candidate;
	return defaults.powerProfile;
}

export function resolveAutoProfileId(
	settings: BartlebyPowerSettings,
	payload: Record<string, unknown> | null | undefined,
	appHostname?: string
): string {
	const telemetryProfileId = String((payload && payload.deployment_profile) || '').trim();
	if (telemetryProfileId && POWER_PROFILES[telemetryProfileId]) {
		return telemetryProfileId;
	}

	const host = String(appHostname || '').toLowerCase();
	if (host === 'eco.bartlebygpt.org' || host === 'apij.bartlebygpt.org') return 'eco-orin';
	if (host === 'api.bartlebygpt.org') return 'home-sd';

	const baseHost = parseHostnameFromUrl(String(settings.baseUrl || ''));
	if (baseHost === 'eco.bartlebygpt.org' || baseHost === 'apij.bartlebygpt.org') return 'eco-orin';
	if (baseHost === 'api.bartlebygpt.org') return 'home-sd';

	return 'home-sd';
}

export function resolvePowerProfile(
	settings: BartlebyPowerSettings,
	payload: Record<string, unknown> | null | undefined,
	appHostname?: string
): BartlebyPowerResolution {
	const selectedProfileId = getSelectedPowerProfileId(settings);

	if (selectedProfileId !== AUTO_PROFILE_ID) {
		const selectedProfile = POWER_PROFILES[selectedProfileId] || POWER_PROFILES[defaults.powerProfile];
		return {
			selectedProfileId,
			resolvedProfileId: selectedProfileId,
			profile: selectedProfile,
			isAuto: false
		};
	}

	const resolvedProfileId = resolveAutoProfileId(settings, payload, appHostname);
	const resolvedProfile =
		POWER_PROFILES[resolvedProfileId] || POWER_PROFILES['home-sd'] || POWER_PROFILES[defaults.powerProfile];

	return {
		selectedProfileId,
		resolvedProfileId,
		profile: resolvedProfile,
		isAuto: true
	};
}

function getPacificDateParts(now: Date) {
	try {
		const formatter = new Intl.DateTimeFormat('en-US', {
			timeZone: 'America/Los_Angeles',
			month: 'numeric',
			hour: 'numeric',
			hourCycle: 'h23'
		});
		const parts = formatter.formatToParts(now);
		const month = Number.parseInt(parts.find((p) => p.type === 'month')?.value || '', 10);
		const hour = Number.parseInt(parts.find((p) => p.type === 'hour')?.value || '', 10);
		if (Number.isFinite(month) && Number.isFinite(hour)) return { month, hour };
	} catch {
		// Fall through to local clock fallback.
	}
	return { month: now.getMonth() + 1, hour: now.getHours() };
}

function resolveSdgeTouRate(now: Date) {
	const { month, hour } = getPacificDateParts(now);
	const season = month >= 6 && month <= 10 ? 'summer' : 'winter';
	const isOnPeak = hour >= 16 && hour < 21;
	const periodKey = isOnPeak ? 'onPeak' : 'offPeak';
	return {
		rateKwh: SDGE_DR2_ALL_IN_RATES[season][periodKey],
		label: `${season === 'summer' ? 'Summer' : 'Winter'} ${isOnPeak ? 'On-Peak' : 'Off-Peak'}`
	};
}

function isSdTouProfile(profileId: string): boolean {
	return profileId === 'home-sd' || profileId === 'eco-orin';
}

function estimateEcoBoardWattsFromMeasured(measuredLoadWatts: number): number {
	const boardWatts = ECO_TOTAL_INTERCEPT_WATTS + ECO_TOTAL_SLOPE * measuredLoadWatts;
	return Math.max(ECO_TOTAL_MIN_WATTS, Math.min(ECO_TOTAL_MAX_WATTS, boardWatts));
}

function computeCostPerHr(
	watts: number,
	settings: BartlebyPowerSettings,
	profile: any,
	profileId: string
): { value: number; label?: string } {
	if (isSdTouProfile(profileId)) {
		const tou = resolveSdgeTouRate(new Date());
		return {
			value: (watts / 1000) * tou.rateKwh,
			label: tou.label
		};
	}

	if (profile?.costMode === 'per-kwh') {
		return {
			value: (watts / 1000) * settings.costPerKwh
		};
	}

	return {
		value: settings.costPerHr
	};
}

function activeCountText(activeCount: number): string {
	const noun = activeCount === 1 ? 'reply' : 'replies';
	return `generating ${activeCount} ${noun}`;
}

function toNum(value: unknown): number {
	return Number.parseFloat(String(value));
}

export function computeTelemetryMetrics(
	settings: BartlebyPowerSettings,
	payload: Record<string, unknown>,
	appHostname?: string
): { metrics: BartlebyPowerMetrics; resolution: BartlebyPowerResolution } {
	const resolution = resolvePowerProfile(settings, payload, appHostname);
	const profileId = resolution.resolvedProfileId;
	const profile = resolution.profile;

	const telemetryTotalWatts = toNum(payload.estimated_total_watts);
	const measuredServerWatts = toNum(payload.measured_server_watts ?? payload.measured_gpu_watts);
	const measuredGpuWatts = toNum(payload.measured_gpu_watts);
	const baseSystemWatts = toNum(payload.base_system_watts);
	const running = toNum(payload.requests_running);

	const activeCount = Number.isFinite(running) ? Math.max(0, Math.round(running)) : 0;
	let watts = Number.NaN;

	if (profileId === 'home-sd') {
		if (Number.isFinite(measuredGpuWatts)) watts = settings.wattsIdle + measuredGpuWatts;
		else if (Number.isFinite(telemetryTotalWatts)) watts = telemetryTotalWatts;
		else watts = settings.wattsActive;
	} else if (profileId === 'eco-orin') {
		if (Number.isFinite(measuredServerWatts)) watts = estimateEcoBoardWattsFromMeasured(measuredServerWatts);
		else if (Number.isFinite(telemetryTotalWatts))
			watts = Math.max(ECO_TOTAL_MIN_WATTS, Math.min(ECO_TOTAL_MAX_WATTS, telemetryTotalWatts));
		else if (Number.isFinite(baseSystemWatts) && Number.isFinite(measuredGpuWatts))
			watts = Math.max(ECO_TOTAL_MIN_WATTS, Math.min(ECO_TOTAL_MAX_WATTS, baseSystemWatts + measuredGpuWatts));
		else if (Number.isFinite(measuredGpuWatts)) watts = estimateEcoBoardWattsFromMeasured(measuredGpuWatts);
		else watts = Math.max(ECO_TOTAL_MIN_WATTS, Math.min(ECO_TOTAL_MAX_WATTS, settings.wattsActive));
	} else {
		const fallbackBase = Number.isFinite(baseSystemWatts) ? baseSystemWatts : 300;
		const legacyWatts = Number.isFinite(measuredGpuWatts)
			? (fallbackBase + measuredGpuWatts) * (profile?.overheadMultiplier || 1.35)
			: Number.NaN;
		if (Number.isFinite(telemetryTotalWatts)) watts = telemetryTotalWatts;
		else if (Number.isFinite(legacyWatts)) watts = legacyWatts;
		else watts = settings.wattsActive;
	}

	const co2PerHr = watts * settings.gco2PerWh;
	const cost = computeCostPerHr(watts, settings, profile, profileId);

	return {
		resolution,
		metrics: {
			watts,
			co2PerHr,
			costPerHr: cost.value,
			activeCount,
			activeCountText: activeCountText(activeCount),
			costLabel: cost.label
		}
	};
}

export function computeFallbackMetrics(
	settings: BartlebyPowerSettings,
	isActive: boolean,
	payload?: Record<string, unknown> | null,
	appHostname?: string
): { metrics: BartlebyPowerMetrics; resolution: BartlebyPowerResolution } {
	const resolution = resolvePowerProfile(settings, payload, appHostname);
	const watts = isActive ? settings.wattsActive : settings.wattsIdle;
	const co2PerHr = watts * settings.gco2PerWh;
	const activeCount = isActive ? 1 : 0;
	const cost = computeCostPerHr(watts, settings, resolution.profile, resolution.resolvedProfileId);

	return {
		resolution,
		metrics: {
			watts,
			co2PerHr,
			costPerHr: cost.value,
			activeCount,
			activeCountText: activeCountText(activeCount),
			costLabel: cost.label
		}
	};
}

