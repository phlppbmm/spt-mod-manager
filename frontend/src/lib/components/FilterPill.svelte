<script lang="ts">
	import { untrack } from 'svelte';
	import Popover from '$lib/components/Popover.svelte';
	import type { PillDefinition, PillOption } from '$lib/filterpills';
	import type { ChipId } from '$lib/modfilter';

	interface Props {
		definition: PillDefinition;
		selected: readonly string[];
		valueLabel: string;
		counts: Record<ChipId, number>;
		onapply: (optionIds: string[]) => void;
		onremove: () => void;
		ondismiss: () => void;
		openOnMount?: boolean;
	}

	let {
		definition,
		selected,
		valueLabel,
		counts,
		onapply,
		onremove,
		ondismiss,
		openOnMount
	}: Props = $props();

	let value: HTMLButtonElement | undefined = $state();
	let open = $state(untrack(() => openOnMount) === true);
	let staged: string[] = $state(untrack(() => [...selected]));

	function toggle(): void {
		if (!open) {
			staged = [...selected];
		}
		open = !open;
	}

	function choose(option: PillOption): void {
		staged = [option.id];
	}

	function countOf(option: PillOption): number | null {
		const chip = option.chips.length === 1 ? option.chips[0] : null;
		return chip === null ? null : counts[chip];
	}

	function apply(): void {
		open = false;
		onapply([...staged]);
	}
</script>

<span class="pill">
	<button
		type="button"
		class="remove"
		aria-label={`Remove ${definition.label} filter`}
		onclick={onremove}
	>
		<svg viewBox="0 0 14 14" width="13" height="13" aria-hidden="true">
			<circle cx="7" cy="7" r="6" fill="currentColor" />
			<path d="M4.8 4.8 9.2 9.2M9.2 4.8 4.8 9.2" stroke="var(--color-surface)" stroke-width="1.4" />
		</svg>
	</button>
	<span class="label">{definition.label}</span>
	<span class="divider"></span>
	<button
		type="button"
		class="value"
		aria-haspopup="dialog"
		aria-expanded={open}
		bind:this={value}
		onclick={toggle}
	>
		{valueLabel}
		<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" fill="none">
			<path
				d="M3 4.5 6 7.5 9 4.5"
				stroke="currentColor"
				stroke-width="1.5"
				stroke-linecap="round"
				stroke-linejoin="round"
			/>
		</svg>
	</button>
</span>

{#if open}
	<Popover
		anchor={value ?? null}
		label={`Filter by ${definition.label}`}
		onclose={() => {
			open = false;
			ondismiss();
		}}
	>
		<p class="title">Filter by {definition.label}</p>
		<div class="options">
			{#each definition.options as option (option.id)}
				<label class="option">
					<input
						type={definition.kind}
						name={`pill-${definition.key}`}
						checked={staged.includes(option.id)}
						onchange={() => choose(option)}
					/>
					<span class="option-label">{option.label}</span>
					{#if countOf(option) !== null}
						<span class="count">{countOf(option)}</span>
					{/if}
				</label>
			{/each}
		</div>
		<button type="button" class="btn-primary apply" onclick={apply}>Apply</button>
	</Popover>
{/if}

<style>
	.pill {
		display: inline-flex;
		align-items: center;
		gap: var(--space-2);
		height: var(--control-height);
		padding: 0 var(--space-3);
		border: 1px solid var(--color-border-strong);
		border-radius: var(--radius-pill);
		font-size: var(--text-sm);
		white-space: nowrap;
	}

	.remove {
		width: 14px;
		height: 14px;
		padding: 0;
		background: transparent;
		border: none;
		border-radius: var(--radius-pill);
		color: var(--color-text-subtle);
	}

	.remove:hover {
		background: transparent;
		color: var(--color-text-muted);
	}

	.label {
		color: var(--color-text-muted);
	}

	.divider {
		width: 1px;
		height: 14px;
		background: var(--color-border-strong);
	}

	.value {
		height: auto;
		padding: 0;
		gap: var(--space-1);
		background: transparent;
		border: none;
		color: var(--color-accent-text);
		font-size: var(--text-sm);
	}

	.value:hover {
		background: transparent;
	}

	.title {
		margin: 0 0 var(--space-2);
		font-weight: var(--weight-semibold);
	}

	.options {
		display: flex;
		flex-direction: column;
		max-height: 18rem;
		overflow: auto;
	}

	.option {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		padding: var(--space-1) 0;
		cursor: pointer;
	}

	.option-label {
		font-size: var(--text-base);
	}

	.count {
		margin-left: auto;
		color: var(--color-text-subtle);
		font-size: var(--text-sm);
		font-variant-numeric: tabular-nums;
	}

	.apply {
		width: 100%;
		margin-top: var(--space-3);
	}
</style>
