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
		background: rgba(0, 0, 0, 0.55);
		display: flex;
		align-items: center;
		justify-content: center;
		padding: 1rem;
		z-index: 10;
	}

	.dialog {
		background: #1e2228;
		border: 1px solid #3a4048;
		border-radius: 6px;
		max-width: 44rem;
		width: 100%;
		max-height: 80vh;
		display: flex;
		flex-direction: column;
	}

	h2 {
		margin: 0;
		padding: 0.9rem 1.1rem;
		font-size: 1.05rem;
		border-bottom: 1px solid #3a4048;
	}

	.body {
		padding: 1.1rem;
		overflow: auto;
	}

	.actions {
		display: flex;
		justify-content: flex-end;
		gap: 0.5rem;
		padding: 0.8rem 1.1rem;
		border-top: 1px solid #3a4048;
	}
</style>
