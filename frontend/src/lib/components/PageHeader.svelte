<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		title: string;
		subtitle?: string;
		primary?: Snippet;
		secondary?: Snippet;
		aside?: Snippet;
	}

	let { title, subtitle, primary, secondary, aside }: Props = $props();
</script>

<div class="head">
	<div class="titles">
		<h1>{title}</h1>
		{#if subtitle}
			<p class="page-subtitle">{subtitle}</p>
		{/if}
	</div>
	{#if aside}
		<div class="aside">{@render aside()}</div>
	{/if}
	{#if secondary || primary}
		<div class="actions">
			{#if secondary}
				{@render secondary()}
			{/if}
			{#if primary}
				{@render primary()}
			{/if}
		</div>
	{/if}
</div>

<style>
	.head {
		display: flex;
		align-items: center;
		gap: var(--space-4);
		margin-bottom: var(--space-5);
	}

	.titles {
		min-width: 0;
	}

	h1 {
		margin: 0;
		font-size: var(--text-2xl, 24px);
		font-weight: var(--weight-semibold);
		line-height: var(--leading-tight);
	}

	.page-subtitle {
		margin: var(--space-1) 0 0;
	}

	.aside {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		margin-left: auto;
	}

	.actions {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		margin-left: auto;
	}

	.aside + .actions {
		margin-left: 0;
	}
</style>
