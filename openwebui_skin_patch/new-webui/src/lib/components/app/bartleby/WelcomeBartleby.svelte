<script lang="ts">
	import { getAllPrompts, getStarterPrompts, PROMPT_ORDER } from '$lib/constants/bartleby-prompts';

	interface Props {
		onPromptSelect?: (prompt: string) => void;
	}

	let { onPromptSelect }: Props = $props();
	let allPrompts = $state(getAllPrompts());
	let starterPrompts = $state(getStarterPrompts());
	let view = $state<'starter' | 'cards' | 'index'>('starter');
	let query = $state('');

	let filteredPrompts = $derived.by(() => {
		const needle = query.trim().toLowerCase();
		if (!needle) return allPrompts;
		return allPrompts.filter((starter: any) => {
			const haystack = `${starter.kicker || ''} ${starter.title || ''} ${starter.prompt || ''}`.toLowerCase();
			return haystack.includes(needle);
		});
	});

	let groupedIndex = $derived.by(() => {
		const groups: Record<string, any[]> = {};
		PROMPT_ORDER.forEach((kicker: string) => {
			groups[kicker] = [];
		});
		allPrompts.forEach((prompt: any) => {
			const key = prompt.kicker || 'Other';
			if (!groups[key]) groups[key] = [];
			groups[key].push(prompt);
		});
		return groups;
	});

	function selectPrompt(prompt: string) {
		onPromptSelect?.(prompt);
	}
</script>

<section class="welcome-shell">
	<h2 class="welcome-title">The world's most ethical AI. For good.</h2>
	<p class="welcome-subtitle">Trust <em>Bartleby</em> for work that requires serious judgment.</p>

	<div class="welcome-controls">
		<button class:active={view === 'starter'} onclick={() => (view = 'starter')}>Starter</button>
		<button class:active={view === 'cards'} onclick={() => (view = 'cards')}>All Prompts</button>
		<button class:active={view === 'index'} onclick={() => (view = 'index')}>Index</button>
	</div>

	{#if view === 'starter'}
		<div class="suggestion-grid">
			{#each starterPrompts as starter (starter.title)}
				<button class="suggestion-card" type="button" onclick={() => selectPrompt(starter.prompt)}>
					<h3 class="suggestion-title">{starter.title}</h3>
					<p class="suggestion-prompt">{starter.prompt}</p>
				</button>
			{/each}
		</div>
	{:else if view === 'cards'}
		<div class="gallery-controls">
			<input bind:value={query} type="search" placeholder="Search prompts by title, category, or text" />
			<p>{filteredPrompts.length} of {allPrompts.length} prompts</p>
		</div>
		<div class="suggestion-grid expanded">
			{#if filteredPrompts.length === 0}
				<div class="empty-state">No prompts matched your search.</div>
			{:else}
				{#each filteredPrompts as starter (starter.title)}
					<button class="suggestion-card" type="button" onclick={() => selectPrompt(starter.prompt)}>
						<p class="suggestion-kicker">{starter.kicker}</p>
						<h3 class="suggestion-title">{starter.title}</h3>
						<p class="suggestion-prompt">{starter.prompt}</p>
					</button>
				{/each}
			{/if}
		</div>
	{:else}
		<div class="prompt-index">
			{#each PROMPT_ORDER as kicker (kicker)}
				{#if groupedIndex[kicker]?.length}
					<section class="index-group">
						<h4>{kicker}</h4>
						<ul>
							{#each groupedIndex[kicker] as starter (starter.title)}
								<li>
									<button type="button" onclick={() => selectPrompt(starter.prompt)}>
										<strong>{starter.title}</strong>
										<span>{starter.prompt}</span>
									</button>
								</li>
							{/each}
						</ul>
					</section>
				{/if}
			{/each}
		</div>
	{/if}
</section>

<style>
	.welcome-shell {
		margin: 0 auto;
		max-width: 64rem;
		display: grid;
		gap: 0.75rem;
	}

	.welcome-title {
		margin: 0;
		font-size: clamp(1.75rem, 4vw, 2.75rem);
		line-height: 1.05;
	}

	.welcome-subtitle {
		margin: 0;
		font-size: clamp(1.15rem, 3vw, 2rem);
		opacity: 0.8;
	}

	.welcome-controls {
		display: flex;
		gap: 0.5rem;
		margin-top: 0.5rem;
	}

	.welcome-controls button {
		border: 1px solid hsl(var(--border));
		background: transparent;
		padding: 0.4rem 0.7rem;
		border-radius: 999px;
		cursor: pointer;
	}

	.welcome-controls button.active {
		background: hsl(var(--muted) / 0.7);
	}

	.gallery-controls {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
	}

	.gallery-controls input {
		flex: 1;
	}

	.suggestion-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 0.65rem;
	}

	.suggestion-grid.expanded {
		grid-template-columns: repeat(3, minmax(0, 1fr));
	}

	.suggestion-card {
		text-align: left;
		border: 1px solid hsl(var(--border));
		background: hsl(var(--card));
		color: hsl(var(--card-foreground));
		border-radius: 0.75rem;
		padding: 0.7rem 0.75rem;
		cursor: pointer;
		display: grid;
		gap: 0.45rem;
	}

	.suggestion-kicker {
		margin: 0;
		font-size: 0.68rem;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		opacity: 0.7;
	}

	.suggestion-title {
		margin: 0;
		font-size: 1rem;
	}

	.suggestion-prompt {
		margin: 0;
		font-size: 0.92rem;
		opacity: 0.9;
	}

	.empty-state {
		grid-column: 1 / -1;
		border: 1px solid hsl(var(--border));
		border-radius: 0.75rem;
		padding: 1rem;
	}

	.prompt-index {
		display: grid;
		gap: 1.1rem;
	}

	.index-group h4 {
		margin: 0 0 0.4rem;
	}

	.index-group ul {
		margin: 0;
		padding: 0;
		list-style: none;
		display: grid;
		gap: 0.3rem;
	}

	.index-group button {
		width: 100%;
		text-align: left;
		border: 0;
		background: transparent;
		padding: 0.25rem 0;
		display: grid;
		gap: 0.1rem;
		cursor: pointer;
	}

	@media (max-width: 900px) {
		.suggestion-grid,
		.suggestion-grid.expanded {
			grid-template-columns: 1fr;
		}
	}
</style>

