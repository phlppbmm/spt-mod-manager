<script lang="ts">
	import { QUICK_TABS, type QuickTabId } from '$lib/filterpills';

	interface Props {
		active: QuickTabId;
		onselect: (id: QuickTabId) => void;
	}

	let { active, onselect }: Props = $props();
</script>

<div class="tabs">
	{#each QUICK_TABS as tab (tab.id)}
		<button
			type="button"
			class="tab"
			class:is-active={tab.id === active}
			aria-current={tab.id === active ? 'page' : undefined}
			onclick={() => onselect(tab.id)}
		>
			{tab.label}
		</button>
	{/each}
</div>

<style>
	.tabs {
		display: flex;
		gap: var(--space-5);
		border-bottom: 1px solid var(--color-border);
	}

	.tab {
		height: auto;
		padding: 0 0 var(--space-2);
		background: transparent;
		border: none;
		border-bottom: 2px solid transparent;
		border-radius: 0;
		color: var(--color-text-muted);
		margin-bottom: -1px;
	}

	.tab:hover:not(:disabled) {
		background: transparent;
		color: var(--color-text);
	}

	.tab.is-active {
		border-bottom-color: var(--color-accent);
		color: var(--color-accent-text);
		font-weight: var(--weight-medium);
	}
</style>
