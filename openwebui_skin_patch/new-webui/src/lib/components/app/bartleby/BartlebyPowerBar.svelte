<script lang="ts">
	import type { BartlebyPowerMetrics } from '$lib/stores/bartleby-power.svelte';

	interface Props {
		metrics: BartlebyPowerMetrics;
		isActive?: boolean;
		onOpenInfo?: () => void;
	}

	let { metrics, isActive = false, onOpenInfo }: Props = $props();

	function formatWatts(watts: number): string {
		if (!Number.isFinite(watts)) return '-- Watts';
		return `${(Math.round(watts * 10) / 10).toFixed(1)} Watts`;
	}

	function formatCo2(value: number): string {
		if (!Number.isFinite(value)) return '-- gCO2/hr';
		return `${value.toFixed(1)} gCO2/hr`;
	}

	function formatCost(value: number): string {
		if (!Number.isFinite(value)) return '--/hr';
		const decimals = Math.abs(value) < 0.05 ? 3 : 2;
		return `$${value.toFixed(decimals)}/hr`;
	}
</script>

<div class="power-display" class:is-active={isActive} role="button" tabindex="0" onclick={onOpenInfo}>
	<span class="power-label">is costing ~</span>
	<span class="power-val">{formatWatts(metrics.watts)}</span>
	<span class="power-sep">·</span>
	<span class="power-val">{formatCo2(metrics.co2PerHr)}</span>
	<span class="power-sep">·</span>
	<span class="power-val">{formatCost(metrics.costPerHr)}</span>
	<span class="power-sep">·</span>
	<span class="power-val">{metrics.activeCountText}</span>
	<button class="power-info-btn" type="button" onclick={onOpenInfo} aria-label="Explain these figures">
		?
	</button>
</div>

<style>
	.power-display {
		display: flex;
		align-items: baseline;
		flex-wrap: wrap;
		gap: 0.3rem;
		font-size: 0.9rem;
		cursor: pointer;
	}

	.power-display.is-active :global(.power-val:first-of-type),
	.power-display.is-active :global(.power-val:nth-of-type(2)) {
		color: #b03a3a;
	}

	.power-label {
		opacity: 0.8;
	}

	.power-sep {
		opacity: 0.5;
	}

	.power-info-btn {
		width: 1rem;
		height: 1rem;
		border: 1px solid hsl(var(--border));
		border-radius: 999px;
		background: transparent;
		font-size: 0.68rem;
		padding: 0;
		cursor: pointer;
	}
</style>

