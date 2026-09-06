<script lang="ts">
	import type { RowState } from '$lib/modfilter';

	interface Props {
		state: RowState;
	}

	let { state }: Props = $props();

	const LABELS: Record<RowState, string> = {
		active: 'Active',
		disabled: 'Disabled',
		update_available: 'Update available',
		incompatible: 'Incompatible',
		available: 'Not installed'
	};

	const CLASSES: Record<RowState, string> = {
		active: 'badge-success',
		disabled: 'badge-neutral',
		update_available: 'badge-warn',
		incompatible: 'badge-danger',
		available: 'badge-neutral'
	};
</script>

<span class="badge {CLASSES[state]}">
	{#if state === 'active'}
		<svg class="icon" viewBox="0 0 12 12" aria-hidden="true">
			<path d="M2.5 6.3 4.8 8.6 9.5 3.9" />
		</svg>
	{:else if state === 'update_available'}
		<svg class="icon" viewBox="0 0 12 12" aria-hidden="true">
			<path d="M6 9.5V2.8" />
			<path d="M3.1 5.7 6 2.8l2.9 2.9" />
		</svg>
	{:else if state === 'disabled'}
		<svg class="icon" viewBox="0 0 12 12" aria-hidden="true">
			<path d="M3 6h6" />
		</svg>
	{:else if state === 'incompatible'}
		<svg class="icon" viewBox="0 0 12 12" aria-hidden="true">
			<path d="M3.4 3.4 8.6 8.6" />
			<path d="M8.6 3.4 3.4 8.6" />
		</svg>
	{/if}
	{LABELS[state]}
</span>

<style>
	.badge {
		gap: var(--space-1);
	}

	.icon {
		width: 12px;
		height: 12px;
		flex-shrink: 0;
		fill: none;
		stroke: currentColor;
		stroke-width: 1.5;
		stroke-linecap: round;
		stroke-linejoin: round;
	}
</style>
