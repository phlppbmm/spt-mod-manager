<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import Dialog from './Dialog.svelte';
	import FilterBar from './FilterBar.svelte';
	import ModTable from './ModTable.svelte';
	import QuickTabs from './QuickTabs.svelte';
	import SelectionBar from './SelectionBar.svelte';
	import {
		browseCatalog,
		disableMod,
		enableMod,
		listFavorites,
		listMods,
		listModSets,
		saveModSet,
		updateMod,
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
		type FilterContext,
		type ModFilter,
		type ModSort,
		type SortKey
	} from '$lib/modfilter';
	import {
		activeQuickTab,
		applyQuickTab,
		pillDefinitions,
		QUICK_TABS,
		type QuickTabId
	} from '$lib/filterpills';
	import { filtersEqual, getFilter, setFilter, subscribeFilter } from '$lib/filterstate';
	import { findSet } from '$lib/modsets';
	import {
		bulkActions,
		pruneSelection,
		runBulk,
		type BulkActionId,
		type BulkProgress
	} from '$lib/selection';
	import { onSubNavSelect, setSearchEnabled, setSubNav } from '$lib/shellstate';
	import { updatableFromCatalog, type UpdateTarget } from '$lib/updateall';
	import { cached, invalidate } from '$lib/viewcache';

	interface Props {
		path: string;
		refreshKey: number;
		locked: boolean;
		sptVersion: string | null;
		onbusy: (busy: boolean) => void;
		onupdatable: (targets: UpdateTarget[]) => void;
	}

	let { path, refreshKey, locked, sptVersion, onbusy, onupdatable }: Props = $props();

	const SUB_NAV = [
		{ id: 'all', label: 'All mods' },
		{ id: 'installed', label: 'Installed' },
		{ id: 'updates', label: 'Updates' },
		{ id: 'favorites', label: 'Favorites' }
	];

	const busyIds = new Set<number>();
	const restored = getFilter();

	let chips = $state(new Set(restored.chips));
	let category: string | null = $state(restored.category);
	let search = $state(restored.search);
	let favorites: number[] = $state([]);
	let entries: CatalogEntry[] = $state([]);
	let mods: ModInfo[] = $state([]);
	let loading = $state(false);
	let error = $state('');
	let bulkError = $state('');
	let bulkBusy = $state(false);
	let bulkProgress: BulkProgress | null = $state(null);
	let selected = $state(new Set<number>());
	let setName = $state('');
	let namingSet = $state(false);
	let savingSet = $state(false);
	let setNote = $state('');
	let setError = $state('');
	let replaceName = $state('');
	let reload = $state(0);
	let requestId = 0;
	let seenMutations: string | null = null;
	let now = $state(Date.now());

	let replacePrompt = $derived(
		replaceName !== '' && replaceName.toLowerCase() === setName.trim().toLowerCase()
	);
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
	let selectedTargets = $derived(
		rows
			.filter((entry) => selected.has(entry.mod.id))
			.map((entry) => ({
				modId: entry.mod.id,
				name: installedFor(entry)?.name ?? entry.mod.name,
				state: rowState(entry, disabledNames)
			}))
	);
	let actions = $derived(bulkActions(selectedTargets));

	onMount(() => {
		setSearchEnabled(true);
		const unsubscribeFilter = subscribeFilter(adopt);
		const unsubscribeSubNav = onSubNavSelect(selectTab);
		return () => {
			setSearchEnabled(false);
			setSubNav([], null);
			unsubscribeFilter();
			unsubscribeSubNav();
		};
	});

	$effect(() => {
		setSubNav(SUB_NAV, activeQuickTab(filter));
	});

	$effect(() => {
		if (!filtersEqual(filter, getFilter())) {
			setFilter(filter);
		}
	});

	$effect(() => {
		const ids = rows.map((entry) => entry.mod.id);
		const pruned = pruneSelection(untrack(() => selected), ids);
		if (pruned.size !== untrack(() => selected).size) {
			selected = pruned;
		}
	});

	function adopt(next: ModFilter): void {
		if (filtersEqual(next, filter)) {
			return;
		}
		chips = new Set(next.chips);
		category = next.category;
		search = next.search;
	}

	function setFilterAndState(next: ModFilter): void {
		chips = new Set(next.chips);
		category = next.category;
		search = next.search;
		setFilter(next);
	}

	function selectTab(id: string): void {
		const tab = QUICK_TABS.find((entry) => entry.id === id);
		if (tab !== undefined) {
			setFilterAndState(applyQuickTab(filter, tab.id));
		}
	}

	function pickTab(tab: QuickTabId): void {
		setFilterAndState(applyQuickTab(filter, tab));
	}

	function installedFor(entry: CatalogEntry): ModInfo | null {
		return (
			installedByForgeId.get(String(entry.mod.id)) ??
			installedByName.get(entry.mod.name.toLowerCase()) ??
			null
		);
	}

	function toggleSort(key: SortKey): void {
		if (sort?.key === key) {
			sort = sort.descending ? null : { key, descending: true };
		} else {
			// Numeric/date columns start descending (biggest/newest first).
			sort = { key, descending: key === 'downloads' || key === 'updated' };
		}
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

	function runOne(id: BulkActionId, name: string): Promise<unknown> {
		if (id === 'enable') {
			return enableMod(path, name);
		}
		if (id === 'disable') {
			return disableMod(path, name);
		}
		return updateMod(path, name);
	}

	async function saveAsSet(): Promise<void> {
		const name = setName.trim();
		if (name === '') {
			return;
		}
		savingSet = true;
		setNote = '';
		setError = '';
		try {
			if (!replacePrompt && findSet(await listModSets(path), name) !== null) {
				replaceName = name;
				return;
			}
			const stored = await saveModSet(path, name);
			namingSet = false;
			setName = '';
			replaceName = '';
			setNote = `The mods that are on now are saved as "${stored.name}".`;
		} catch (cause) {
			setError = cause instanceof Error ? cause.message : String(cause);
		} finally {
			savingSet = false;
		}
	}

	async function runAction(id: BulkActionId): Promise<void> {
		const action = actions.find((entry) => entry.id === id);
		if (action === undefined) {
			return;
		}
		bulkBusy = true;
		bulkError = '';
		onbusy(true);
		const result = await runBulk(
			action.names,
			(name) => runOne(id, name),
			(progress) => (bulkProgress = progress)
		);
		bulkBusy = false;
		bulkProgress = null;
		bulkError = result.failed
			.map((failure) => `${failure.name}: ${failure.message}`)
			.join(' · ');
		selected = new Set();
		reload += 1;
		onbusy(busyIds.size > 0);
	}
</script>

<section>
	<div class="controls">
		<QuickTabs active={activeQuickTab(filter)} onselect={pickTab} />
		<FilterBar
			{filter}
			definitions={pillDefinitions(categories, sptVersion)}
			{counts}
			onchange={setFilterAndState}
		/>
		<div class="sets">
			<button type="button" class="btn-sm" onclick={() => (namingSet = true)}>
				Save current mods as set
			</button>
			{#if setNote !== ''}<span class="note">{setNote}</span>{/if}
		</div>
	</div>

	{#if setError !== ''}
		<p class="error-text">{setError}</p>
	{/if}

	{#if error !== ''}
		<p class="error-text">{error}</p>
	{/if}

	{#if bulkError !== ''}
		<p class="error-text">Some mods could not be changed. {bulkError}</p>
	{/if}

	{#if selected.size > 0}
		<SelectionBar
			count={selected.size}
			{actions}
			busy={bulkBusy}
			progress={bulkProgress}
			onrun={(id) => void runAction(id)}
			onclear={() => (selected = new Set())}
		/>
	{/if}

	<ModTable
		{path}
		{rows}
		{disabledNames}
		{installedByForgeId}
		{installedByName}
		locked={locked || bulkBusy}
		{favoriteIds}
		{selected}
		{sort}
		{loading}
		onsort={toggleSort}
		onselect={(next) => (selected = next)}
		onfavorite={onFavorites}
		onchanged={() => (reload += 1)}
		onbusy={onRowBusy}
	/>
</section>

{#if namingSet}
	<Dialog
		title="Save the current mods as a set"
		onclose={() => {
			namingSet = false;
			replaceName = '';
		}}
	>
		<p>
			The set holds every installed mod and whether it is on or off right now. A profile can
			be started with it from the Profiles page.
		</p>
		<label>
			Name
			<input type="text" aria-label="Set name" bind:value={setName} />
		</label>
		{#if replacePrompt}
			<p class="warn-text">A set named "{replaceName}" already exists. Replace its mods?</p>
		{/if}

		{#snippet actions()}
			<button
				type="button"
				class="btn-primary"
				disabled={savingSet || setName.trim() === ''}
				onclick={() => void saveAsSet()}
			>
				{savingSet ? 'Saving…' : replacePrompt ? 'Replace' : 'Save'}
			</button>
		{/snippet}
	</Dialog>
{/if}

<style>
	.controls {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		margin-bottom: var(--space-4);
	}

	.sets {
		display: flex;
		align-items: center;
		gap: var(--space-3);
	}

	label {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}
</style>
