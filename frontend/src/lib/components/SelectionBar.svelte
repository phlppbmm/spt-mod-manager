<script lang="ts">
	import type { BulkAction, BulkActionId, BulkProgress } from '$lib/selection';

	interface Props {
		count: number;
		actions: BulkAction[];
		busy: boolean;
		progress: BulkProgress | null;
		onrun: (id: BulkActionId) => void;
		onclear: () => void;
	}

	let { count, actions, busy, progress, onrun, onclear }: Props = $props();
</script>

<div class="bar">
	<span class="count">{count} selected</span>
	{#each actions as action (action.id)}
		<button type="button" class="btn-sm" disabled={busy} onclick={() => onrun(action.id)}>
			{action.label}
		</button>
	{/each}
	{#if busy && progress !== null}
		<span class="progress">
			Updating {progress.index} of {progress.total}: {progress.name}
		</span>
	{/if}
	<button type="button" class="btn-ghost btn-sm clear" onclick={onclear}>Clear selection</button>
</div>

<style>
	.bar {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
		margin-bottom: var(--space-3);
		padding: var(--space-2) var(--space-3);
		background: var(--color-accent-soft);
		border: 1px solid var(--color-accent-border);
		border-radius: var(--radius-lg);
	}

	.count {
		color: var(--color-accent-text);
		font-weight: var(--weight-medium);
	}

	.progress {
		color: var(--color-text-muted);
		font-size: var(--text-sm);
	}

	.clear {
		margin-left: auto;
	}
</style>
