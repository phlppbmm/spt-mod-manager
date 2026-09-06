<script lang="ts">
	import Popover from '$lib/components/Popover.svelte';
	import type { PillDefinition, PillKey } from '$lib/filterpills';

	interface Props {
		available: PillDefinition[];
		onadd: (key: PillKey) => void;
	}

	let { available, onadd }: Props = $props();

	let pill: HTMLButtonElement | undefined = $state();
	let open = $state(false);

	function add(key: PillKey): void {
		open = false;
		onadd(key);
	}
</script>

<button
	type="button"
	class="add"
	aria-haspopup="dialog"
	aria-expanded={open}
	bind:this={pill}
	onclick={() => (open = !open)}
>
	<svg viewBox="0 0 14 14" width="13" height="13" aria-hidden="true" fill="none">
		<circle cx="7" cy="7" r="6" stroke="currentColor" stroke-width="1.2" />
		<path d="M7 4.2v5.6M4.2 7h5.6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" />
	</svg>
	Add filter
</button>

{#if open}
	<Popover anchor={pill ?? null} label="Add filter" onclose={() => (open = false)}>
		<p class="title">Add filter</p>
		<div class="choices">
			{#each available as def (def.key)}
				<button type="button" class="choice" onclick={() => add(def.key)}>{def.label}</button>
			{/each}
		</div>
	</Popover>
{/if}

<style>
	.add {
		gap: var(--space-2);
		height: var(--control-height);
		padding: 0 var(--space-3);
		background: transparent;
		border: 1px dashed var(--color-border-strong);
		border-radius: var(--radius-pill);
		color: var(--color-text-muted);
		font-size: var(--text-sm);
		white-space: nowrap;
	}

	.add:hover {
		background: var(--color-hover);
		color: var(--color-text);
	}

	.title {
		margin: 0 0 var(--space-2);
		font-weight: var(--weight-semibold);
	}

	.choices {
		display: flex;
		flex-direction: column;
	}

	.choice {
		justify-content: flex-start;
		background: transparent;
		border: none;
		border-radius: var(--radius-sm);
		font-size: var(--text-base);
	}

	.choice:hover {
		background: var(--color-hover);
	}
</style>
