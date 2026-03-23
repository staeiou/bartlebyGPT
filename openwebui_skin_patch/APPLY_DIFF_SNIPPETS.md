# Apply Diff Snippets

Use these as starting snippets when integrating into llama.cpp webui.

## 1) `src/app.css` token override block

Append near the bottom:

```css
/* Bartleby skin seed */
:root {
	--radius: 0.22rem;
	--background: oklch(0.93 0.02 85);
	--foreground: oklch(0.2 0.01 75);
	--card: oklch(0.955 0.02 85);
	--card-foreground: oklch(0.2 0.01 75);
	--muted: oklch(0.9 0.01 85);
	--muted-foreground: oklch(0.42 0.01 75);
	--border: oklch(0.82 0.01 75);
	--input: oklch(0.87 0.01 80);
	--accent: oklch(0.86 0.01 85);
	--accent-foreground: oklch(0.2 0.01 75);
	--primary: oklch(0.27 0.01 70);
	--primary-foreground: oklch(0.95 0.01 85);
}

body {
	font-family: 'EB Garamond', 'Palatino Linotype', 'Book Antiqua', Georgia, serif;
	background:
		radial-gradient(ellipse 80% 50% at 50% 0%, rgba(168, 162, 140, 0.14), transparent 60%),
		linear-gradient(180deg, #ece8dc 0%, #e4dfd0 100%);
}
```

## 2) `ChatScreen.svelte` empty-state replacement

In the script imports:

```ts
import { WelcomeBartleby } from '$lib/components/app/bartleby';
```

In empty branch replace current placeholder card:

```svelte
<div class="w-full max-w-[72rem] px-4">
	<WelcomeBartleby
		onPromptSelect={(prompt) => {
			initialMessage = prompt;
		}}
	/>
	<div class="mt-4">
		<ChatScreenForm
			disabled={hasPropsError}
			{initialMessage}
			isLoading={isCurrentConversationLoading}
			onFileRemove={handleFileRemove}
			onFileUpload={handleFileUpload}
			onSend={handleSendMessage}
			onStop={() => chatStore.stopGeneration()}
			onSystemPromptAdd={handleSystemPromptAdd}
			showHelperText
			bind:uploadedFiles
		/>
	</div>
</div>
```

## 3) `ChatScreenHeader.svelte` power bar seed

Add imports:

```ts
import { BartlebyPowerBar, DialogPowerInfo } from '$lib/components/app/bartleby';
import { computeFallbackMetrics } from '$lib/stores/bartleby-power.svelte';
import { config } from '$lib/stores/settings.svelte';
```

Seed metrics:

```ts
let powerDialogOpen = $state(false);
let fallback = $derived(
	computeFallbackMetrics(
		{
			powerProfile: 'auto-live',
			wattsIdle: Number(config().wattsIdle ?? 120),
			wattsActive: Number(config().wattsActive ?? 260),
			gco2PerWh: Number(config().gco2PerWh ?? 0.2),
			costPerHr: Number(config().costPerHr ?? 0.09),
			costPerKwh: Number(config().costPerKwh ?? 0.55)
		},
		false
	)
);
```

Render:

```svelte
<div class="pointer-events-auto flex items-center gap-2">
	<BartlebyPowerBar metrics={fallback.metrics} onOpenInfo={() => (powerDialogOpen = true)} />
	<Button variant="ghost" size="icon-lg" onclick={toggleSettings} class="rounded-full backdrop-blur-lg">
		<Settings class="h-4 w-4" />
	</Button>
</div>

<DialogPowerInfo
	open={powerDialogOpen}
	onOpenChange={(open) => (powerDialogOpen = open)}
	html="<p>BartlebyGPT is a satire, but the cost data is genuine.</p>"
/>
```

