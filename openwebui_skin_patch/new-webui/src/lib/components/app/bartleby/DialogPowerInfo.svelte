<script lang="ts">
	import * as Dialog from '$lib/components/ui/dialog';

	interface Props {
		open?: boolean;
		title?: string;
		html?: string;
		onOpenChange?: (open: boolean) => void;
	}

	let {
		open = false,
		title = 'About These Figures',
		html = '<p>Power details unavailable.</p>',
		onOpenChange
	}: Props = $props();
</script>

<Dialog.Root {open} onOpenChange={(next) => onOpenChange?.(next)}>
	<Dialog.Content class="max-w-3xl">
		<Dialog.Header>
			<Dialog.Title>{title}</Dialog.Title>
			<Dialog.Description class="sr-only">Energy and carbon accounting details</Dialog.Description>
		</Dialog.Header>
		<div class="power-info-body">{@html html}</div>
	</Dialog.Content>
</Dialog.Root>

<style>
	.power-info-body :global(p) {
		margin: 0 0 0.8rem;
		line-height: 1.6;
	}

	.power-info-body :global(hr) {
		border: 0;
		border-top: 1px solid hsl(var(--border));
		margin: 1rem 0;
	}
</style>

