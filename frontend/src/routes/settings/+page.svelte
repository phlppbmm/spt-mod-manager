<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { open, save } from '@tauri-apps/plugin-dialog';
	import { writeTextFile } from '@tauri-apps/plugin-fs';
	import { writeText } from '@tauri-apps/plugin-clipboard-manager';
	import {
		clearCatalogCache,
		deleteModSet,
		exportMods,
		exportModSet,
		importList,
		importModSet,
		listModSets,
		renameModSet,
		type ExportFormat,
		type ImportDiff,
		type ImportEntry,
		type ModSet
	} from '$lib/api/client';
	import { ApiError } from '$lib/api/transport';
	import Dialog from '$lib/components/Dialog.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import { forgetGameDir, loadGameDir } from '$lib/gamedir';
	import { loadStopServerOnExit, saveStopServerOnExit } from '$lib/launch';
	import { exportFileName } from '$lib/modsets';
	import { setSearchEnabled, setSubNav } from '$lib/shellstate';
	import {
		applyTheme,
		loadPreference,
		resolveTheme,
		savePreference,
		type ThemePreference
	} from '$lib/theme';

	const THEME_OPTIONS: { value: ThemePreference; label: string }[] = [
		{ value: 'system', label: 'Use system setting' },
		{ value: 'light', label: 'Light' },
		{ value: 'dark', label: 'Dark' }
	];

	const SECTIONS = [
		{ id: 'game-directory', label: 'Game directory' },
		{ id: 'appearance', label: 'Appearance' },
		{ id: 'launching', label: 'Launching' },
		{ id: 'mod-sets', label: 'Mod sets' },
		{ id: 'export', label: 'Export the mod list' },
		{ id: 'import', label: 'Import a mod list' },
		{ id: 'catalog-cache', label: 'Catalog cache' }
	];

	let path: string | null = $state(null);
	let stopServerOnExit = $state(true);
	let themePreference: ThemePreference = $state('system');

	let format: ExportFormat = $state('text');
	let clientOnly = $state(false);
	let exported = $state('');
	let exportError = $state('');
	let exporting = $state(false);
	let copyNote = $state('');

	let sets: ModSet[] = $state([]);
	let setsError = $state('');
	let setsNote = $state('');
	let setsBusy = $state(false);
	let setFile = $state('');
	let setCode = $state('');
	let prompt: 'rename' | 'import' | null = $state(null);
	let promptTarget = $state('');
	let promptName = $state('');

	let listFile = $state('');
	let listContent = $state('');
	let diff: ImportDiff | null = $state(null);
	let importError = $state('');
	let importNote = $state('');
	let importing = $state(false);

	let clearing = $state(false);
	let cacheNote = $state('');
	let cacheError = $state('');

	const active = $derived(
		SECTIONS.some((section) => `#${section.id}` === page.url.hash)
			? page.url.hash.slice(1)
			: SECTIONS[0].id
	);

	onMount(() => {
		setSubNav([], null);
		setSearchEnabled(false);
		path = loadGameDir();
		void loadSets();
		stopServerOnExit = loadStopServerOnExit();
		themePreference = loadPreference();
		const observer = new MutationObserver(() => {
			themePreference = loadPreference();
		});
		observer.observe(document.documentElement, { attributeFilter: ['data-theme'] });
		return () => {
			observer.disconnect();
			setSubNav([], null);
		};
	});

	async function loadSets(): Promise<void> {
		if (path === null) {
			return;
		}
		setsError = '';
		try {
			sets = (await listModSets(path)).sets;
		} catch (cause) {
			setsError = cause instanceof Error ? cause.message : String(cause);
		}
	}

	async function removeSet(name: string): Promise<void> {
		if (path === null) {
			return;
		}
		setsBusy = true;
		setsError = '';
		setsNote = '';
		try {
			sets = (await deleteModSet(path, name)).sets;
			setsNote = `"${name}" was deleted.`;
		} catch (cause) {
			setsError = cause instanceof Error ? cause.message : String(cause);
		} finally {
			setsBusy = false;
		}
	}

	async function saveSetFile(name: string): Promise<void> {
		if (path === null) {
			return;
		}
		setsBusy = true;
		setsError = '';
		setsNote = '';
		try {
			const shared = await exportModSet(path, name);
			const target = await save({ defaultPath: exportFileName(name) });
			if (target !== null) {
				await writeTextFile(target, shared.content);
				setsNote = `Saved to ${target}.`;
			}
		} catch (cause) {
			setsError = cause instanceof Error ? cause.message : String(cause);
		} finally {
			setsBusy = false;
		}
	}

	async function copySetCode(name: string): Promise<void> {
		if (path === null) {
			return;
		}
		setsBusy = true;
		setsError = '';
		setsNote = '';
		try {
			await writeText((await exportModSet(path, name)).code);
			setsNote = `The share code for "${name}" is in the clipboard.`;
		} catch (cause) {
			setsError = cause instanceof Error ? cause.message : String(cause);
		} finally {
			setsBusy = false;
		}
	}

	async function pickSetFile(): Promise<void> {
		setsError = '';
		try {
			const picked = await open({
				multiple: false,
				filters: [{ name: 'Mod set', extensions: ['json'] }]
			});
			if (picked !== null) {
				setFile = picked;
			}
		} catch (cause) {
			setsError = cause instanceof Error ? cause.message : String(cause);
		}
	}

	async function runImportSet(name: string | null): Promise<void> {
		if (path === null || !hasSetSource()) {
			return;
		}
		setsBusy = true;
		setsError = '';
		setsNote = '';
		try {
			const imported = await importModSet(
				path,
				setCode.trim() === '' ? { setFile: setFile.trim() } : { content: setCode },
				name
			);
			setFile = '';
			setCode = '';
			setsNote = `"${imported.name}" was added.`;
			await loadSets();
		} catch (cause) {
			if (cause instanceof ApiError && cause.code === 'MOD_SET_EXISTS') {
				promptName = '';
				prompt = 'import';
			} else {
				setsError = cause instanceof Error ? cause.message : String(cause);
			}
		} finally {
			setsBusy = false;
		}
	}

	async function runRename(): Promise<void> {
		if (path === null) {
			return;
		}
		setsBusy = true;
		setsError = '';
		setsNote = '';
		try {
			const renamed = await renameModSet(path, promptTarget, promptName.trim());
			prompt = null;
			setsNote = `The set is now called "${renamed.name}".`;
			await loadSets();
		} catch (cause) {
			setsError = cause instanceof Error ? cause.message : String(cause);
		} finally {
			setsBusy = false;
		}
	}

	function askRename(name: string): void {
		promptTarget = name;
		promptName = name;
		prompt = 'rename';
	}

	async function confirmPrompt(): Promise<void> {
		if (prompt === 'rename') {
			await runRename();
			return;
		}
		const name = promptName.trim();
		prompt = null;
		await runImportSet(name);
	}

	function hasSetSource(): boolean {
		return setFile.trim() !== '' || setCode.trim() !== '';
	}

	function changeStopServerOnExit(value: boolean): void {
		stopServerOnExit = value;
		saveStopServerOnExit(value);
	}

	function changeTheme(value: string): void {
		const option = THEME_OPTIONS.find((entry) => entry.value === value);
		if (option === undefined) {
			return;
		}
		themePreference = option.value;
		savePreference(option.value);
		applyTheme(resolveTheme(option.value));
	}

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
			await writeText(exported);
			copyNote = 'Copied to the clipboard.';
		} catch (cause) {
			copyNote = cause instanceof Error ? cause.message : String(cause);
		}
	}

	async function saveExport(): Promise<void> {
		exportError = '';
		try {
			const target = await save({ defaultPath: format === 'json' ? 'mods.json' : 'mods.txt' });
			if (target === null) {
				return;
			}
			await writeTextFile(target, exported);
			copyNote = `Saved to ${target}.`;
		} catch (cause) {
			exportError = cause instanceof Error ? cause.message : String(cause);
		}
	}

	async function pickListFile(): Promise<void> {
		importError = '';
		try {
			const picked = await open({
				multiple: false,
				filters: [{ name: 'Mod list', extensions: ['txt', 'json'] }]
			});
			if (picked !== null) {
				listFile = picked;
			}
		} catch (cause) {
			importError = cause instanceof Error ? cause.message : String(cause);
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

	async function clearCache(downloads: boolean): Promise<void> {
		clearing = true;
		cacheNote = '';
		cacheError = '';
		try {
			const result = await clearCatalogCache({ downloads });
			const catalog = result.removed
				? `Removed ${result.db_path}. Run a catalog sync to rebuild it.`
				: `Nothing to remove; ${result.db_path} does not exist.`;
			cacheNote = downloads
				? `${catalog} Freed ${megabytes(result.downloads_freed_bytes)} of kept mod files.`
				: catalog;
		} catch (cause) {
			cacheError = cause instanceof Error ? cause.message : String(cause);
		} finally {
			clearing = false;
		}
	}

	function megabytes(bytes: number): string {
		return `${(bytes / 1_000_000).toFixed(1)} MB`;
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

<div class="page">
	<PageHeader title="Settings" />

	<div class="layout">
		<nav class="subnav" aria-label="Settings sections">
			{#each SECTIONS as section (section.id)}
				<a
					href="#{section.id}"
					class:is-active={active === section.id}
					aria-current={active === section.id ? 'page' : undefined}
				>
					{section.label}
				</a>
			{/each}
		</nav>

		<div class="sections">
			<section class="section panel">
				<h2 id="game-directory">Game directory</h2>
				{#if path === null}
					<p class="note">No game directory yet. <a href="/">Run the setup</a> first.</p>
				{:else}
					<p class="row"><code>{path}</code></p>
					<p class="note">
						Changing the path returns to the setup, where another SPT installation can be
						selected. The current repository stays untouched.
					</p>
					<div class="row">
						<button type="button" onclick={changePath}>Change path</button>
					</div>
				{/if}
			</section>

			<section class="section panel">
				<h2 id="appearance">Appearance</h2>
				<div class="row">
					<label>
						Theme
						<select
							value={themePreference}
							onchange={(event) => changeTheme(event.currentTarget.value)}
						>
							{#each THEME_OPTIONS as option (option.value)}
								<option value={option.value}>{option.label}</option>
							{/each}
						</select>
					</label>
				</div>
				<p class="note">
					The sun and moon button in the title bar switches between light and dark.
					<em>Use system setting</em> follows the operating system instead.
				</p>
			</section>

			<section class="section panel">
				<h2 id="launching">Launching</h2>
				<div class="row">
					<label>
						<input
							type="checkbox"
							checked={stopServerOnExit}
							onchange={(event) => changeStopServerOnExit(event.currentTarget.checked)}
						/>
						Stop the server when the game closes
					</label>
				</div>
				<p class="note">
					Applies to games started from the Profiles page. A server that was already running
					when the game started stays running.
				</p>
			</section>

			<section class="section panel">
				<h2 id="mod-sets">Mod sets</h2>
				<p class="note">
					A mod set holds a list of mods and whether each of them is on or off. A profile
					started from the Profiles page uses the set it is set up with. Sets are saved from
					the Mods page.
				</p>
				{#if sets.length === 0}
					<p class="note">No sets yet.</p>
				{:else}
					<ul class="sets panel">
						{#each sets as set (set.name)}
							<li class="panel-row">
								<span class="name">{set.name}</span>
								<span class="versions">
									{set.entries.length}
									{set.entries.length === 1 ? 'mod' : 'mods'}
								</span>
								<button
									type="button"
									class="btn-sm"
									disabled={setsBusy}
									onclick={() => askRename(set.name)}
								>
									Rename
								</button>
								<button
									type="button"
									class="btn-sm"
									disabled={setsBusy}
									onclick={() => void saveSetFile(set.name)}
								>
									Export
								</button>
								<button
									type="button"
									class="btn-sm"
									disabled={setsBusy}
									onclick={() => void copySetCode(set.name)}
								>
									Copy share code
								</button>
								<button
									type="button"
									class="btn-sm"
									disabled={setsBusy}
									onclick={() => void removeSet(set.name)}
								>
									Delete
								</button>
							</li>
						{/each}
					</ul>
				{/if}
				<h3>Add a set</h3>
				<p class="note">
					Either name a file someone shared or paste a share code. A pasted code wins over the
					file path.
				</p>
				<div class="row">
					<input
						type="text"
						placeholder="/path/to/mod-set.json"
						aria-label="Mod set file"
						bind:value={setFile}
					/>
					<button type="button" onclick={() => void pickSetFile()}>Choose file…</button>
				</div>
				<div class="row">
					<textarea
						rows="3"
						placeholder="Paste a share code"
						aria-label="Mod set share code"
						bind:value={setCode}
					></textarea>
					<button
						type="button"
						class="btn-primary"
						disabled={path === null || setsBusy || !hasSetSource()}
						onclick={() => void runImportSet(null)}
					>
						Add the set
					</button>
				</div>
				{#if setsError !== ''}
					<p class="error-text">{setsError}</p>
				{/if}
				{#if setsNote !== ''}
					<p class="note">{setsNote}</p>
				{/if}
			</section>

			<section class="section panel">
				<h2 id="export">Export the mod list</h2>
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
					<button
						type="button"
						class="btn-primary"
						disabled={path === null || exporting}
						onclick={() => void runExport()}
					>
						{exporting ? 'Exporting…' : 'Export'}
					</button>
				</div>
				{#if exportError !== ''}
					<p class="error-text">{exportError}</p>
				{/if}
				{#if exported !== ''}
					<textarea readonly rows="10" aria-label="Exported mod list" value={exported}
					></textarea>
					<div class="row">
						<button type="button" onclick={() => void copyExport()}>Copy</button>
						<button type="button" onclick={() => void saveExport()}>
							Save as {format === 'json' ? 'JSON' : 'text'}
						</button>
						{#if copyNote !== ''}<span class="note">{copyNote}</span>{/if}
					</div>
				{/if}
			</section>

			<section class="section panel">
				<h2 id="import">Import a mod list</h2>
				<p class="note">
					Either name a file sptmod can read or paste the list itself. Pasted content wins
					over the file path.
				</p>
				<div class="row">
					<input
						type="text"
						placeholder="/path/to/mods.txt"
						aria-label="Mod list file"
						bind:value={listFile}
					/>
					<button type="button" onclick={() => void pickListFile()}>Choose file…</button>
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
						class="btn-primary"
						disabled={path === null || importing || !hasList()}
						onclick={() => void runImport(false)}
					>
						Compare
					</button>
					<button
						type="button"
						class="btn-primary"
						disabled={path === null || importing || diff === null}
						onclick={() => void runImport(true)}
					>
						Install missing mods
					</button>
				</div>
				{#if importError !== ''}
					<p class="error-text">{importError}</p>
				{/if}
				{#if importNote !== ''}
					<p class="note">{importNote}</p>
				{/if}
				{#if diff !== null}
					{#each entries() as group (group.title)}
						<div class="group">
							<h3>{group.title} ({group.rows.length})</h3>
							<ul class="diff panel">
								{#each group.rows as entry (entry.name)}
									<li class="panel-row">
										<span class="name">{entry.name}</span>
										<span class="versions">
											{entry.installed_version === ''
												? '—'
												: entry.installed_version}
											→
											{entry.wanted_version === ''
												? '—'
												: entry.wanted_version}
										</span>
										{#if entry.link !== ''}
											<a href={entry.link} target="_blank" rel="noreferrer">
												Forge page
											</a>
										{/if}
									</li>
								{/each}
								{#if group.rows.length === 0}
									<li class="panel-row note">Nothing.</li>
								{/if}
							</ul>
						</div>
					{/each}
				{/if}
			</section>

			<section class="section panel">
				<h2 id="catalog-cache">Catalog cache</h2>
				<p class="note">
					The Forge catalog is cached in <code>forge-catalog.db</code> inside the sptmod
					configuration directory, never inside the game directory.
				</p>
				<div class="row">
					<button type="button" disabled={clearing} onclick={() => void clearCache(false)}>
						{clearing ? 'Clearing…' : 'Clear the catalog cache'}
					</button>
					<button type="button" disabled={clearing} onclick={() => void clearCache(true)}>
						{clearing ? 'Clearing…' : 'Clear the cache and the downloaded mod files'}
					</button>
					{#if cacheNote !== ''}<span class="note">{cacheNote}</span>{/if}
				</div>
				{#if cacheError !== ''}
					<p class="error-text">{cacheError}</p>
				{/if}
			</section>
		</div>
	</div>
</div>

{#if prompt !== null}
	<Dialog
		title={prompt === 'rename' ? 'Rename the set' : 'Choose another name'}
		onclose={() => (prompt = null)}
	>
		<p>
			{prompt === 'rename'
				? `Give "${promptTarget}" a new name.`
				: 'A set of that name already exists. Give the new set another name.'}
		</p>
		<label>
			Name
			<input type="text" aria-label="Set name" bind:value={promptName} />
		</label>

		{#snippet actions()}
			<button
				type="button"
				class="btn-primary"
				disabled={setsBusy || promptName.trim() === ''}
				onclick={() => void confirmPrompt()}
			>
				Save
			</button>
		{/snippet}
	</Dialog>
{/if}

<style>
	.page {
		padding: var(--page-gutter, 32px);
	}

	.layout {
		display: flex;
		align-items: flex-start;
		gap: var(--space-8);
	}

	.subnav {
		position: sticky;
		top: 0;
		display: flex;
		flex: none;
		flex-direction: column;
		gap: var(--space-1);
		width: 12rem;
	}

	.subnav a {
		padding: var(--space-1) var(--space-3);
		border-left: 2px solid transparent;
		color: var(--color-text-muted);
		font-size: var(--text-base);
	}

	.subnav a:hover {
		color: var(--color-text);
		text-decoration: none;
	}

	.subnav a.is-active {
		border-left-color: var(--color-accent);
		color: var(--color-text);
	}

	.sections {
		display: flex;
		flex: 1;
		flex-direction: column;
		gap: var(--space-5);
		min-width: 0;
		max-width: 52rem;
	}

	.section {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		padding: var(--space-5);
	}

	.section h2 {
		scroll-margin-top: var(--space-4);
	}

	.section p {
		margin: 0;
	}

	.row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-3);
	}

	.row input[type='text'] {
		flex: 1;
		min-width: 16rem;
	}

	label {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	textarea {
		width: 100%;
		font-family: var(--font-mono);
		font-size: var(--text-sm);
	}

	.group {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.group h3 {
		color: var(--color-text-muted);
		font-size: var(--text-xs);
		letter-spacing: var(--tracking-caps);
		text-transform: uppercase;
	}

	.diff,
	.sets {
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.diff li,
	.sets li {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		padding: var(--space-2) var(--space-3);
	}

	.name {
		flex: 1;
	}

	.versions {
		color: var(--color-text-muted);
	}

	@media (max-width: 900px) {
		.subnav {
			display: none;
		}
	}
</style>
