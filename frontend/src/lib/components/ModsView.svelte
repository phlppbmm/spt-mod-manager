<script lang="ts">
	import ModRow from './ModRow.svelte';
	import {
		browseCatalog,
		listFavorites,
		listMods,
		type CatalogEntry,
		type ModInfo,
		type ModType
	} from '$lib/api/client';
	import {
		categoriesOf,
		chipCounts,
		filterEntries,
		rowState,
		sortEntries,
		CHIP_IDS,
		CHIP_LABELS,
		type ChipId,
		type FilterContext,
		type ModFilter,
		type ModSort,
		type SortKey
	} from '$lib/modfilter';
	import { clearFilter, getFilter, isDefaultFilter, setFilter } from '$lib/filterstate';
	import { updatableFromCatalog, type UpdateTarget } from '$lib/updateall';
	import { cached, invalidate } from '$lib/viewcache';

	interface Props {
		path: string;
		refreshKey: number;
		locked: boolean;
		onbusy: (busy: boolean) => void;
		onupdatable: (targets: UpdateTarget[]) => void;
	}

	let { path, refreshKey, locked, onbusy, onupdatable }: Props = $props();

	const busyIds = new Set<number>();
	const restored = getFilter();

	let chips = $state(new Set(restored.chips));
	let category: string | null = $state(restored.category);
	let favorites: number[] = $state([]);
	let searchInput = $state(restored.search);
	let search = $state(restored.search);
	let searchTimer: ReturnType<typeof setTimeout> | undefined;
	let entries: CatalogEntry[] = $state([]);
	let mods: ModInfo[] = $state([]);
	let loading = $state(false);
	let error = $state('');
	let reload = $state(0);
	let requestId = 0;
	let seenMutations: string | null = null;
	let now = $state(Date.now());

	let disabledNames = $derived(
		new Set(
			mods.filter((mod) => mod.state === 'disabled').map((mod) => mod.name.toLowerCase())
		)
	);
	let installedByName = $derived(
		new Map(mods.map((mod) => [mod.name.toLowerCase(), mod] as const))
	);
	// The trailer name comes from the mod's own package.json and often differs
	// from the Forge display name, so the Forge id is the reliable link.
	let installedByForgeId = $derived(
		new Map(
			mods
				.filter((mod) => mod.forge_id !== '')
				.map((mod) => [mod.forge_id, mod] as const)
		)
	);
	let modTypes = $derived.by(() => {
		const types = new Map<number, ModType>();
		for (const mod of mods) {
			const forgeId = Number(mod.forge_id);
			if (mod.forge_id !== '' && Number.isFinite(forgeId)) {
				types.set(forgeId, mod.mod_type);
			}
		}
		return types;
	});
	let favoriteIds = $derived(new Set(favorites));
	let sort = $state<ModSort | null>(null);
	let filter = $derived<ModFilter>({ chips, category, search });
	let context = $derived<FilterContext>({
		favorites: favoriteIds,
		disabled: disabledNames,
		modTypes,
		now
	});
	let rows = $derived(sortEntries(filterEntries(entries, filter, context), sort));
	let counts = $derived(chipCounts(entries, filter, context));
	let categories = $derived(categoriesOf(entries));

	$effect(() => {
		setFilter(filter);
	});

	function toggleChip(chip: ChipId): void {
		const next = new Set(chips);
		if (!next.delete(chip)) {
			next.add(chip);
		}
		chips = next;
	}

	function clearAll(): void {
		clearFilter();
		const fresh = getFilter();
		chips = new Set(fresh.chips);
		category = fresh.category;
		searchInput = fresh.search;
		search = fresh.search;
		clearTimeout(searchTimer);
	}

	function toggleSort(key: SortKey): void {
		if (sort?.key === key) {
			sort = sort.descending ? null : { key, descending: true };
		} else {
			// Numeric/date columns start descending (biggest/newest first).
			sort = { key, descending: key === 'downloads' || key === 'updated' };
		}
	}

	function sortMark(key: SortKey): string {
		if (sort?.key !== key) {
			return '';
		}
		return sort.descending ? ' ▼' : ' ▲';
	}

	$effect(() => {
		// Any mutating action bumps `reload` (mod rows) or `refreshKey` (page
		// level), so the cached views for this game directory are stale then.
		const mutations = `${refreshKey}:${reload}`;
		if (seenMutations !== null && seenMutations !== mutations) {
			invalidate(path);
		}
		seenMutations = mutations;
		void load(path, refreshKey, reload);
	});

	function apply(catalog: CatalogEntry[], installed: ModInfo[], favored: number[]): void {
		entries = catalog;
		mods = installed;
		favorites = favored;
		onupdatable(updatableFromCatalog(catalog, installed));
	}

	async function load(gameDir: string, _refreshKey: number, _reload: number): Promise<void> {
		const request = ++requestId;
		now = Date.now();
		const catalogView = cached(gameDir, 'browse_catalog', [], () =>
			browseCatalog(gameDir, 'all', false)
		);
		const modsView = cached(gameDir, 'list_mods', [], () => listMods(gameDir, true));
		const favoritesView = cached(gameDir, 'list_favorites', [], async () => {
			const favored = await listFavorites();
			return favored.mod_ids;
		});
		const catalogHit = catalogView.value;
		const modsHit = modsView.value;
		const favoritesHit = favoritesView.value;
		if (catalogHit !== undefined && modsHit !== undefined && favoritesHit !== undefined) {
			apply(catalogHit, modsHit, favoritesHit);
			loading = false;
		} else {
			loading = true;
		}
		error = '';
		try {
			const [catalog, installed, favored] = await Promise.all([
				catalogView.refresh(),
				modsView.refresh(),
				favoritesView.refresh()
			]);
			if (request !== requestId) {
				return;
			}
			apply(catalog, installed, favored);
		} catch (cause) {
			if (request === requestId) {
				error = cause instanceof Error ? cause.message : String(cause);
			}
		} finally {
			if (request === requestId) {
				loading = false;
			}
		}
	}

	function onFavorites(ids: number[]): void {
		favorites = ids;
		void cached(path, 'list_favorites', [], () => Promise.resolve(ids)).refresh();
	}

	function onRowBusy(modId: number, busy: boolean): void {
		if (busy) {
			busyIds.add(modId);
		} else {
			busyIds.delete(modId);
		}
		onbusy(busyIds.size > 0);
	}

	function onSearch(value: string): void {
		searchInput = value;
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => {
			search = value;
		}, 250);
	}
</script>

<section class="mods">
	<div class="chips">
		{#each CHIP_IDS as chip (chip)}
			<button
				type="button"
				class="chip"
				class:selected={chips.has(chip)}
				aria-pressed={chips.has(chip)}
				onclick={() => toggleChip(chip)}
			>
				{CHIP_LABELS[chip]}
				<span class="count">{counts[chip]}</span>
			</button>
		{/each}
	</div>
	<div class="controls">
		<select
			aria-label="Category"
			value={category ?? ''}
			onchange={(event) => (category = event.currentTarget.value || null)}
		>
			<option value="">All categories</option>
			{#each categories as title (title)}
				<option value={title}>{title}</option>
			{/each}
		</select>
		<input
			type="search"
			placeholder="Search name or author"
			aria-label="Search mods"
			value={searchInput}
			oninput={(event) => onSearch(event.currentTarget.value)}
		/>
		<button type="button" disabled={isDefaultFilter(filter)} onclick={clearAll}>
			Clear filters
		</button>
	</div>

	{#if error !== ''}
		<p class="error">{error}</p>
	{/if}

	<div class="list">
		<div class="head">
			<div class="index">#</div>
			<button class="sort" onclick={() => toggleSort('name')}>Mod{sortMark('name')}</button>
			<button class="sort" onclick={() => toggleSort('installed')}>
				Installed{sortMark('installed')}
			</button>
			<button class="sort" onclick={() => toggleSort('latest')}>Latest{sortMark('latest')}</button>
			<button class="sort" onclick={() => toggleSort('spt')}>SPT{sortMark('spt')}</button>
			<button class="sort" onclick={() => toggleSort('downloads')}>
				Downloads{sortMark('downloads')}
			</button>
			<button class="sort" onclick={() => toggleSort('updated')}>
				Updated{sortMark('updated')}
			</button>
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
				onfavorite={onFavorites}
				onchanged={() => (reload += 1)}
				onbusy={onRowBusy}
			/>
		{/each}
		{#if rows.length === 0}
			<p class="empty">
				{loading ? 'Loading…' : 'No mods match the current filters.'}
			</p>
		{/if}
	</div>
</section>

<style>
	.mods {
		padding: 1rem;
	}

	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: 0.4rem;
		margin-bottom: 0.6rem;
	}

	.chip {
		border-radius: 999px;
		padding: 0.25rem 0.7rem;
	}

	.chip.selected {
		background: #35506b;
		border-color: #4c7aa8;
	}

	.chip .count {
		color: #98a2ad;
		margin-left: 0.35rem;
		font-variant-numeric: tabular-nums;
	}

	.chip.selected .count {
		color: #cfe0f0;
	}

	.controls {
		display: flex;
		flex-wrap: wrap;
		gap: 0.8rem;
		align-items: center;
		margin-bottom: 0.8rem;
	}

	select {
		font: inherit;
		background: #1e2228;
		color: inherit;
		border: 1px solid #3a4048;
		border-radius: 4px;
		padding: 0.35rem 0.5rem;
	}

	input[type='search'] {
		flex: 1;
		min-width: 12rem;
	}

	.list {
		--mods-grid: 2.5rem minmax(12rem, 1fr) 5rem 5rem 6rem 5.5rem 5.5rem 5rem 7.5rem 8rem;
		border: 1px solid #2c3138;
		border-radius: 4px;
	}

	@media (max-width: 1000px) {
		.list {
			--mods-grid: 2.5rem minmax(10rem, 1fr) 5rem 5rem 6rem 7.5rem 8rem;
		}

		.head > :nth-child(n + 6):nth-child(-n + 8) {
			display: none;
		}
	}

	.head {
		display: grid;
		grid-template-columns: var(--mods-grid);
		gap: 0.6rem;
		padding: 0.45rem 0.6rem;
		border-bottom: 1px solid #3a4048;
		color: #98a2ad;
	}

	.head .index {
		text-align: right;
	}

	.head .sort {
		background: none;
		border: none;
		padding: 0;
		font: inherit;
		color: inherit;
		text-align: left;
		cursor: pointer;
	}

	.head .sort:hover {
		color: #d7dde3;
		font-size: 0.85em;
	}

	.empty {
		padding: 1rem;
		color: #98a2ad;
	}

	.error {
		color: #ff8f8f;
	}
</style>
