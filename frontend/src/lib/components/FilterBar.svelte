<script lang="ts">
	import AddFilterPill from '$lib/components/AddFilterPill.svelte';
	import FilterPill from '$lib/components/FilterPill.svelte';
	import {
		activePills,
		applyPill,
		availablePills,
		removePill,
		type PillDefinition,
		type PillKey
	} from '$lib/filterpills';
	import { clearFilter, getFilter } from '$lib/filterstate';
	import type { ChipId, ModFilter } from '$lib/modfilter';

	interface Props {
		filter: ModFilter;
		definitions: PillDefinition[];
		counts: Record<ChipId, number>;
		onchange: (next: ModFilter) => void;
	}

	let { filter, definitions, counts, onchange }: Props = $props();

	interface Row {
		def: PillDefinition;
		selected: string[];
		valueLabel: string;
		fresh: boolean;
	}

	/** The pill just picked from the add menu, until it carries a value. */
	let pending: PillKey | null = $state(null);

	let rows = $derived.by((): Row[] => {
		const active = activePills(filter, definitions);
		const result: Row[] = [];
		for (const def of definitions) {
			const pill = active.find((entry) => entry.key === def.key);
			if (pill) {
				result.push({ def, selected: pill.optionIds, valueLabel: pill.valueLabel, fresh: false });
			} else if (def.key === pending) {
				result.push({ def, selected: [], valueLabel: 'Select…', fresh: true });
			}
		}
		return result;
	});

	let available = $derived(availablePills(filter, definitions).filter((def) => def.key !== pending));

	function apply(def: PillDefinition, optionIds: string[]): void {
		pending = null;
		onchange(applyPill(filter, def, optionIds));
	}

	function dismiss(def: PillDefinition): void {
		if (pending === def.key) {
			pending = null;
		}
	}

	function remove(def: PillDefinition): void {
		if (pending === def.key) {
			pending = null;
		}
		onchange(removePill(filter, def));
	}

	function clearAll(): void {
		pending = null;
		clearFilter();
		onchange({ ...getFilter(), search: filter.search });
	}
</script>

<div class="bar">
	{#each rows as row (row.def.key)}
		<FilterPill
			definition={row.def}
			selected={row.selected}
			valueLabel={row.valueLabel}
			{counts}
			openOnMount={row.fresh}
			onapply={(optionIds) => apply(row.def, optionIds)}
			onremove={() => remove(row.def)}
			ondismiss={() => dismiss(row.def)}
		/>
	{/each}
	{#if available.length > 0}
		<AddFilterPill {available} onadd={(key) => (pending = key)} />
	{/if}
	<button type="button" class="clear" onclick={clearAll}>
		<span aria-hidden="true">✕</span>
		Clear filters
	</button>
</div>

<style>
	.bar {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
	}

	.clear {
		margin-left: auto;
		gap: var(--space-1);
		background: transparent;
		border: none;
		color: var(--color-text-muted);
		font-size: var(--text-sm);
	}

	.clear:hover:not(:disabled) {
		background: transparent;
		color: var(--color-text);
	}
</style>
