<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import {
		clearCatalogCache,
		exportMods,
		importList,
		type ExportFormat,
		type ImportDiff,
		type ImportEntry
	} from '$lib/api/client';
	import { forgetGameDir, loadGameDir } from '$lib/gamedir';

	let path: string | null = $state(null);

	let format: ExportFormat = $state('text');
	let clientOnly = $state(false);
	let exported = $state('');
	let exportError = $state('');
	let exporting = $state(false);
	let copyNote = $state('');

	let listFile = $state('');
	let listContent = $state('');
	let diff: ImportDiff | null = $state(null);
	let importError = $state('');
	let importNote = $state('');
	let importing = $state(false);

	let clearing = $state(false);
	let cacheNote = $state('');
	let cacheError = $state('');

	let downloadHref = $derived.by(
		() =>
			`data:${format === 'json' ? 'application/json' : 'text/plain'};charset=utf-8,${encodeURIComponent(exported)}`
	);

	onMount(() => {
		path = loadGameDir();
	});

	function changePath(): void {
		forgetGameDir();
		void goto('/');
	}

	async function runExport(): Promise<void> {
		if (path === null) {
			return;
		}
		exporting = true;
		exportError = '';
		copyNote = '';
		try {
			exported = (await exportMods(path, format, clientOnly)).content;
		} catch (cause) {
			exportError = cause instanceof Error ? cause.message : String(cause);
		} finally {
			exporting = false;
		}
	}

	async function copyExport(): Promise<void> {
		try {
			await navigator.clipboard.writeText(exported);
			copyNote = 'Copied to the clipboard.';
		} catch (cause) {
			copyNote = cause instanceof Error ? cause.message : String(cause);
		}
	}

	async function runImport(apply: boolean): Promise<void> {
		if (path === null || !hasList()) {
			return;
		}
		importing = true;
		importError = '';
		importNote = '';
		try {
			diff = await importList(
				path,
				listContent.trim() === ''
					? { listFile: listFile.trim() }
					: { content: listContent },
				apply
			);
			if (apply) {
				importNote = 'The missing mods were installed.';
			}
		} catch (cause) {
			importError = cause instanceof Error ? cause.message : String(cause);
		} finally {
			importing = false;
		}
	}

	function hasList(): boolean {
		return listFile.trim() !== '' || listContent.trim() !== '';
	}

	async function clearCache(): Promise<void> {
		clearing = true;
		cacheNote = '';
		cacheError = '';
		try {
			const result = await clearCatalogCache();
			cacheNote = result.removed
				? `Removed ${result.db_path}. Run a catalog sync to rebuild it.`
				: `Nothing to remove; ${result.db_path} does not exist.`;
		} catch (cause) {
			cacheError = cause instanceof Error ? cause.message : String(cause);
		} finally {
			clearing = false;
		}
	}

	function entries(): { title: string; rows: ImportEntry[] }[] {
		if (diff === null) {
			return [];
		}
		return [
			{ title: 'Missing', rows: diff.missing },
			{ title: 'Different version', rows: diff.different },
			{ title: 'Extra', rows: diff.extra }
		];
	}
</script>

<section class="settings">
	<h1>Settings</h1>

	<h2>Game directory</h2>
	{#if path === null}
		<p class="note">No game directory yet. <a href="/">Run the setup</a> first.</p>
	{:else}
		<p class="row"><code>{path}</code></p>
		<p class="note">
			Changing the path returns to the setup, where another SPT installation can be
			selected. The current repository stays untouched.
		</p>
		<button type="button" onclick={changePath}>Change path</button>
	{/if}

	<h2>Export the mod list</h2>
	<div class="row">
		<label>
			Format
			<select bind:value={format}>
				<option value="text">Text</option>
				<option value="json">JSON</option>
			</select>
		</label>
		<label>
			<input type="checkbox" bind:checked={clientOnly} />
			Only mods with a client part (Fika)
		</label>
		<button type="button" disabled={path === null || exporting} onclick={() => void runExport()}>
			{exporting ? 'Exporting…' : 'Export'}
		</button>
	</div>
	{#if exportError !== ''}
		<p class="error">{exportError}</p>
	{/if}
	{#if exported !== ''}
		<textarea readonly rows="10" aria-label="Exported mod list" value={exported}></textarea>
		<div class="row">
			<button type="button" onclick={() => void copyExport()}>Copy</button>
			<a href={downloadHref} download={format === 'json' ? 'mods.json' : 'mods.txt'}>
				Download {format === 'json' ? 'JSON' : 'text'}
			</a>
			{#if copyNote !== ''}<span class="note">{copyNote}</span>{/if}
		</div>
	{/if}

	<h2>Import a mod list</h2>
	<p class="note">
		Either name a file the server can read or paste the list itself. Pasted content wins over
		the file path.
	</p>
	<div class="row">
		<input
			type="text"
			placeholder="/path/to/mods.txt"
			aria-label="Mod list file"
			bind:value={listFile}
		/>
	</div>
	<div class="row">
		<textarea
			rows="6"
			placeholder="Paste an exported text or JSON list"
			aria-label="Mod list content"
			bind:value={listContent}
		></textarea>
		<button
			type="button"
			disabled={path === null || importing || !hasList()}
			onclick={() => void runImport(false)}
		>
			Compare
		</button>
		<button
			type="button"
			disabled={path === null || importing || diff === null}
			onclick={() => void runImport(true)}
		>
			Install missing mods
		</button>
	</div>
	{#if importError !== ''}
		<p class="error">{importError}</p>
	{/if}
	{#if importNote !== ''}
		<p class="note">{importNote}</p>
	{/if}
	{#if diff !== null}
		{#each entries() as group (group.title)}
			<h3>{group.title} ({group.rows.length})</h3>
			<ul class="diff">
				{#each group.rows as entry (entry.name)}
					<li>
						<span class="name">{entry.name}</span>
						<span class="versions">
							{entry.installed_version === '' ? '—' : entry.installed_version}
							→
							{entry.wanted_version === '' ? '—' : entry.wanted_version}
						</span>
						{#if entry.link !== ''}
							<a href={entry.link} target="_blank" rel="noreferrer">Forge page</a>
						{/if}
					</li>
				{/each}
				{#if group.rows.length === 0}
					<li class="note">Nothing.</li>
				{/if}
			</ul>
		{/each}
	{/if}

	<h2>Catalog cache</h2>
	<p class="note">
		The Forge catalog is cached in <code>forge-catalog.db</code> inside the sptmod configuration
		directory of the machine running the server, never inside the game directory.
	</p>
	<div class="row">
		<button type="button" disabled={clearing} onclick={() => void clearCache()}>
			{clearing ? 'Clearing…' : 'Clear the catalog cache'}
		</button>
		{#if cacheNote !== ''}<span class="note">{cacheNote}</span>{/if}
	</div>
	{#if cacheError !== ''}
		<p class="error">{cacheError}</p>
	{/if}
</section>

<style>
	.settings {
		padding: 1rem;
		max-width: 52rem;
	}

	h1 {
		font-size: 1.1rem;
		margin: 0 0 0.8rem;
	}

	h2 {
		font-size: 1rem;
		margin: 1.4rem 0 0.4rem;
	}

	h3 {
		font-size: 0.9rem;
		margin: 0.8rem 0 0.3rem;
		color: #98a2ad;
	}

	.row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.6rem;
		margin: 0.4rem 0;
	}

	.row input[type='text'] {
		flex: 1;
		min-width: 16rem;
	}

	label {
		display: flex;
		align-items: center;
		gap: 0.35rem;
	}

	select {
		font: inherit;
		background: #1e2228;
		color: inherit;
		border: 1px solid #3a4048;
		border-radius: 4px;
		padding: 0.3rem;
	}

	textarea {
		width: 100%;
		font-family: ui-monospace, monospace;
		font-size: 0.9em;
		background: #1e2228;
		color: inherit;
		border: 1px solid #3a4048;
		border-radius: 4px;
		padding: 0.5rem;
		box-sizing: border-box;
	}

	.diff {
		list-style: none;
		margin: 0;
		padding: 0;
		border: 1px solid #2c3138;
		border-radius: 4px;
	}

	.diff li {
		display: flex;
		gap: 0.6rem;
		padding: 0.3rem 0.6rem;
		border-bottom: 1px solid #2c3138;
	}

	.name {
		flex: 1;
	}

	.versions {
		color: #98a2ad;
	}

	.note {
		color: #98a2ad;
	}

	.error {
		color: #ff8f8f;
	}
</style>
