<script lang="ts">
	import ModRow from './ModRow.svelte';
	import {
		browseCatalog,
		listFavorites,
		listMods,
		searchCatalog,
		type CatalogEntry,
		type ModInfo
	} from '$lib/api/client';
	import {
		filterEntries,
		rowState,
		sortEntries,
		type ModSort,
		type ModTab,
		type SortKey
	} from '$lib/modfilter';
	import { updatableFromCatalog, type UpdateTarget } from '$lib/updateall';

	interface Props {
		path: string;
		refreshKey: number;
		locked: boolean;
		onbusy: (busy: boolean) => void;
		onupdatable: (targets: UpdateTarget[]) => void;
	}

	let { path, refreshKey, locked, onbusy, onupdatable }: Props = $props();

	const busyIds = new Set<number>();

	const TABS: { id: ModTab; label: string }[] = [
		{ id: 'all', label: 'All' },
		{ id: 'installed', label: 'Installed' },
		{ id: 'favorited', label: 'Favorited' }
	];

	let tab: ModTab = $state('all');
	let compatibleOnly = $state(true);
	let favorites: number[] = $state([]);
	let searchInput = $state('');
	let search = $state('');
	let searchTimer: ReturnType<typeof setTimeout> | undefined;
	let entries: CatalogEntry[] = $state([]);
	let mods: ModInfo[] = $state([]);
	let loading = $state(false);
	let error = $state('');
	let reload = $state(0);
	let requestId = 0;

	let disabledMods = $derived(
		mods.filter((mod) => mod.state === 'disabled').map((mod) => mod.name)
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
	let favoriteIds = $derived(new Set(favorites));
	let sort = $state<ModSort | null>(null);
	let rows = $derived(
		sortEntries(
			filterEntries(entries, {
				tab,
				compatibleOnly,
				favorites: favoriteIds,
				search
			}),
			sort
		)
	);

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
		void load(path, compatibleOnly, search, refreshKey, reload);
	});

	async function load(
		gameDir: string,
		compatible: boolean,
		query: string,
		_refreshKey: number,
		_reload: number
	): Promise<void> {
		const request = ++requestId;
		loading = true;
		error = '';
		try {
			const [catalog, installed, favored] = await Promise.all([
				query.trim() === ''
					? browseCatalog(gameDir, 'all', compatible)
					: searchCatalog(gameDir, query, compatible),
				listMods(gameDir, true),
				listFavorites()
			]);
			if (request !== requestId) {
				return;
			}
			entries = catalog;
			mods = installed;
			favorites = favored.mod_ids;
			onupdatable(updatableFromCatalog(catalog, installed));
		} catch (cause) {
			if (request === requestId) {
				error = cause instanceof Error ? cause.message : String(cause);
				onupdatable([]);
			}
		} finally {
			if (request === requestId) {
				loading = false;
			}
		}
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
	<div class="controls">
		<div class="tabs">
			{#each TABS as entry (entry.id)}
				<button
					type="button"
					class:selected={tab === entry.id}
					onclick={() => (tab = entry.id)}
				>
					{entry.label}
				</button>
			{/each}
		</div>
		<input
			type="search"
			placeholder="Search name or author"
			aria-label="Search mods"
			value={searchInput}
			oninput={(event) => onSearch(event.currentTarget.value)}
		/>
		<label class="compat">
			<input type="checkbox" bind:checked={compatibleOnly} />
			Only compatible with my SPT version
		</label>
	</div>

	{#if error !== ''}
		<p class="error">{error}</p>
	{/if}

	<div class="list">
		<div class="head">
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
		{#each rows as entry (entry.mod.id)}
			<ModRow
				{path}
				{entry}
				modState={rowState(entry, disabledMods)}
				installed={installedByForgeId.get(String(entry.mod.id)) ??
					installedByName.get(entry.mod.name.toLowerCase()) ??
					null}
				{locked}
				favorite={favoriteIds.has(entry.mod.id)}
				onfavorite={(ids) => (favorites = ids)}
				onchanged={() => (reload += 1)}
				onbusy={onRowBusy}
			/>
		{/each}
		{#if rows.length === 0}
			<p class="empty">
				{loading ? 'Loading…' : 'No mods match the current tab, search and filter.'}
			</p>
		{/if}
	</div>
</section>

<style>
	.mods {
		padding: 1rem;
	}

	.controls {
		display: flex;
		flex-wrap: wrap;
		gap: 0.8rem;
		align-items: center;
		margin-bottom: 0.8rem;
	}

	.tabs button {
		background: none;
		border: 1px solid #3a4048;
		color: inherit;
		padding: 0.35rem 0.8rem;
		cursor: pointer;
	}

	.tabs button:first-child {
		border-radius: 4px 0 0 4px;
	}

	.tabs button:last-child {
		border-radius: 0 4px 4px 0;
	}

	.tabs button.selected {
		background: #2c3138;
	}

	input[type='search'] {
		flex: 1;
		min-width: 12rem;
	}

	.compat {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		white-space: nowrap;
	}

	.list {
		--mods-grid: minmax(12rem, 1fr) 5rem 5rem 6rem 5.5rem 5.5rem 5rem 7.5rem 8rem;
		border: 1px solid #2c3138;
		border-radius: 4px;
	}

	@media (max-width: 1000px) {
		.list {
			--mods-grid: minmax(10rem, 1fr) 5rem 5rem 6rem 7.5rem 8rem;
		}

		.head > :nth-child(n + 5):nth-child(-n + 7) {
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
