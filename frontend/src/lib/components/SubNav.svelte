<script lang="ts">
	import { selectSubNav, type SubNavItem } from '$lib/shellstate';

	interface Props {
		heading: string;
		items: SubNavItem[];
		active: string | null;
	}

	let { heading, items, active }: Props = $props();
</script>

<nav class="subnav" aria-label={heading}>
	<div class="heading">{heading}</div>
	{#each items as item (item.id)}
		<button
			class="item"
			class:is-active={item.id === active}
			type="button"
			aria-current={item.id === active ? 'true' : undefined}
			onclick={() => selectSubNav(item.id)}
		>
			{item.label}
		</button>
	{/each}
</nav>

<style>
	.subnav {
		flex: 0 0 var(--subnav-width);
		position: sticky;
		top: 0;
		align-self: flex-start;
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		padding: var(--space-6) var(--space-4) var(--space-6) var(--page-gutter, 32px);
	}

	.heading {
		margin-bottom: var(--space-2);
		color: var(--color-text);
		font-size: var(--text-md);
		font-weight: var(--weight-semibold);
	}

	.item {
		justify-content: flex-start;
		height: var(--control-height);
		padding: 0 var(--space-3);
		background: transparent;
		border: none;
		border-left: 2px solid transparent;
		border-radius: 0;
		color: var(--color-text-muted);
		font-size: var(--text-base);
		text-align: left;
	}

	.item:hover:not(:disabled) {
		background: transparent;
		color: var(--color-text);
	}

	.item.is-active {
		border-left-color: var(--color-accent);
		color: var(--color-accent-text);
		font-weight: var(--weight-medium);
	}
</style>
