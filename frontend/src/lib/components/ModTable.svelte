<script lang="ts">
	import ModRow from './ModRow.svelte';
	import type { CatalogEntry, ModInfo } from '$lib/api/client';
	import { rowState, type ModSort, type SortKey } from '$lib/modfilter';
	import { headerCheckbox, toggleAll, toggleSelection } from '$lib/selection';

	interface Props {
		path: string;
		rows: CatalogEntry[];
		disabledNames: ReadonlySet<string>;
		installedByForgeId: ReadonlyMap<string, ModInfo>;
		installedByName: ReadonlyMap<string, ModInfo>;
		locked: boolean;
		favoriteIds: ReadonlySet<number>;
		selected: ReadonlySet<number>;
		sort: ModSort | null;
		onsort: (key: SortKey) => void;
		onselect: (next: Set<number>) => void;
		onfavorite: (ids: number[]) => void;
		onchanged: () => void;
		onbusy: (modId: number, busy: boolean) => void;
		loading: boolean;
	}

	let {
		path,
		rows,
		disabledNames,
		installedByForgeId,
		installedByName,
		locked,
		favoriteIds,
		selected,
		sort,
		onsort,
		onselect,
		onfavorite,
		onchanged,
		onbusy,
		loading
	}: Props = $props();

	let ids = $derived(rows.map((entry) => entry.mod.id));
	let header = $derived(headerCheckbox(selected, ids));

	function sortMark(key: SortKey): string {
		if (sort?.key !== key) {
			return '';
		}
		return sort.descending ? ' ▼' : ' ▲';
	}
</script>

<div class="table">
	<div class="panel list">
		<div class="panel-head head">
			<div class="pick">
				<input
					type="checkbox"
					aria-label="Select all mods"
					checked={header === 'all'}
					indeterminate={header === 'some'}
					onchange={() => onselect(toggleAll(selected, ids))}
				/>
			</div>
			<div class="index">#</div>
			<button class="sort" onclick={() => onsort('name')}>Mod{sortMark('name')}</button>
			<button class="sort" onclick={() => onsort('installed')}>
				Installed{sortMark('installed')}
			</button>
			<button class="sort" onclick={() => onsort('latest')}>Latest{sortMark('latest')}</button>
			<button class="sort" onclick={() => onsort('spt')}>SPT{sortMark('spt')}</button>
			<button class="sort" onclick={() => onsort('downloads')}>
				Downloads{sortMark('downloads')}
			</button>
			<button class="sort" onclick={() => onsort('updated')}>Updated{sortMark('updated')}</button>
			<div>Type</div>
			<div>State</div>
			<div></div>
		</div>
		{#each rows as entry, position (entry.mod.id)}
			<ModRow
				{path}
				index={position + 1}
				{entry}
				modState={rowState(entry, disabledNames)}
				installed={installedByForgeId.get(String(entry.mod.id)) ??
					installedByName.get(entry.mod.name.toLowerCase()) ??
					null}
				{locked}
				favorite={favoriteIds.has(entry.mod.id)}
				selected={selected.has(entry.mod.id)}
				onselect={() => onselect(toggleSelection(selected, entry.mod.id))}
				{onfavorite}
				{onchanged}
				{onbusy}
			/>
		{/each}
		{#if rows.length === 0}
			<p class="empty">
				{loading ? 'Loading…' : 'No mods match the current filters.'}
			</p>
		{/if}
	</div>
</div>
<p class="result-count">{rows.length === 1 ? '1 result' : `${rows.length} results`}</p>

<style>
	.table {
		container: mods / inline-size;
	}

	.list {
		--mods-grid: 1.75rem 2rem minmax(12rem, 1fr) 5rem 5rem 5rem 5.5rem 5.5rem 4.5rem 7rem 8rem;
	}

	@container mods (max-width: 1090px) {
		.list {
			--mods-grid: 1.75rem 2rem minmax(10rem, 1fr) 5rem 5rem 5rem 5.5rem 5.5rem 4.5rem 7rem 8rem;
		}
	}

	@container mods (max-width: 1060px) {
		.list {
			--mods-grid: 1.75rem 2rem minmax(10rem, 1fr) 5rem 5rem 5rem 7rem 8rem;
		}

		.head > :nth-child(n + 7):nth-child(-n + 9) {
			display: none;
		}
	}

	.head {
		display: grid;
		grid-template-columns: var(--mods-grid);
		align-items: center;
		gap: var(--space-2);
		padding: var(--space-2) var(--space-3);
	}

	.head .pick {
		display: flex;
		align-items: center;
	}

	.head .index {
		text-align: right;
	}

	.head .sort {
		display: block;
		height: auto;
		padding: 0;
		background: none;
		border: none;
		font: inherit;
		color: inherit;
		letter-spacing: inherit;
		text-transform: inherit;
		text-align: left;
		cursor: pointer;
	}

	.head .sort:hover {
		background: none;
		color: var(--color-text);
	}

	.empty {
		margin: 0;
		padding: var(--space-4);
		color: var(--color-text-muted);
	}

	.result-count {
		margin: var(--space-3) 0 0;
	}
</style>
