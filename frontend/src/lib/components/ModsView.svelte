<script lang="ts">
	import ModRow from './ModRow.svelte';
	import {
		browseCatalog,
		listMods,
		searchCatalog,
		type CatalogEntry,
		type ModInfo
	} from '$lib/api/client';
	import { filterEntries, rowState, type ModTab } from '$lib/modfilter';

	interface Props {
		path: string;
		refreshKey: number;
	}

	let { path, refreshKey }: Props = $props();

	const TABS: { id: ModTab; label: string }[] = [
		{ id: 'all', label: 'All' },
		{ id: 'installed', label: 'Installed' },
		{ id: 'available', label: 'Not installed' }
	];

	let tab: ModTab = $state('all');
	let compatibleOnly = $state(true);
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
	let rows = $derived(filterEntries(entries, { tab, compatibleOnly, search }));

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
			const [catalog, installed] = await Promise.all([
				query.trim() === ''
					? browseCatalog(gameDir, 'all', compatible)
					: searchCatalog(gameDir, query, compatible),
				listMods(gameDir, true)
			]);
			if (request !== requestId) {
				return;
			}
			entries = catalog;
			mods = installed;
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
			<div>Mod</div>
			<div>Installed</div>
			<div>Latest</div>
			<div>Type</div>
			<div>State</div>
			<div></div>
		</div>
		{#each rows as entry (entry.mod.id)}
			<ModRow
				{path}
				{entry}
				modState={rowState(entry, disabledMods)}
				installed={installedByName.get(entry.mod.name.toLowerCase()) ?? null}
				onchanged={() => (reload += 1)}
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
		--mods-grid: minmax(12rem, 3fr) 6rem 6rem 6rem 9rem minmax(12rem, auto);
		border: 1px solid #2c3138;
		border-radius: 4px;
	}

	.head {
		display: grid;
		grid-template-columns: var(--mods-grid);
		gap: 0.6rem;
		padding: 0.45rem 0.6rem;
		border-bottom: 1px solid #3a4048;
		color: #98a2ad;
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
