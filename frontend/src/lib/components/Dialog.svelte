<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		title: string;
		onclose: () => void;
		children: Snippet;
		actions?: Snippet;
	}

	let { title, onclose, children, actions }: Props = $props();

	let dialog: HTMLDivElement | undefined = $state();

	$effect(() => {
		dialog?.focus();
	});
</script>

<div
	class="backdrop"
	role="presentation"
	onclick={(event) => {
		if (event.target === event.currentTarget) {
			onclose();
		}
	}}
>
	<div
		class="dialog"
		role="dialog"
		aria-modal="true"
		aria-label={title}
		tabindex="-1"
		bind:this={dialog}
		onkeydown={(event) => {
			if (event.key === 'Escape') {
				event.stopPropagation();
				onclose();
			}
		}}
	>
		<h2>{title}</h2>
		<div class="body">
			{@render children()}
		</div>
		<div class="actions">
			{#if actions}
				{@render actions()}
			{/if}
			<button type="button" onclick={onclose}>Close</button>
		</div>
	</div>
</div>

<style>
	.backdrop {
		position: fixed;
		inset: 0;
		background: var(--color-overlay);
		display: flex;
		align-items: center;
		justify-content: center;
		padding: var(--space-4);
		z-index: var(--z-dialog);
	}

	.dialog {
		background: var(--color-elevated);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-lg);
		box-shadow: var(--shadow-lg);
		max-width: 44rem;
		width: 100%;
		max-height: 80vh;
		display: flex;
		flex-direction: column;
	}

	h2 {
		padding: var(--space-4) var(--space-5);
		border-bottom: 1px solid var(--color-border);
	}

	.body {
		padding: var(--space-5);
		overflow: auto;
	}

	.actions {
		display: flex;
		justify-content: flex-end;
		gap: var(--space-2);
		padding: var(--space-4) var(--space-5);
		border-top: 1px solid var(--color-border);
	}
</style>
