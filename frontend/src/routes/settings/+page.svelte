<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { open, save } from '@tauri-apps/plugin-dialog';
	import { writeTextFile } from '@tauri-apps/plugin-fs';
	import { writeText } from '@tauri-apps/plugin-clipboard-manager';
	import {
		applyModSet,
		applySptRelease,
		checkSptCompatibility,
		clearCatalogCache,
		deleteModSet,
		exportMods,
		exportModSet,
		getFikaSettings,
		importList,
		importModSet,
		installMod,
		listModSets,
		listSptReleases,
		planFikaInstall,
		renameModSet,
		revertSptUpdate,
		setFikaSettings,
		type ExportFormat,
		type FikaInstallPlan,
		type FikaRole,
		type FikaSettings,
		type ImportDiff,
		type ImportEntry,
		type ModSet,
		type SptReleaseInfo,
		type SptReleaseList
	} from '$lib/api/client';
	import { ApiError, subscribe, type ProgressEvent } from '$lib/api/transport';
	import Dialog from '$lib/components/Dialog.svelte';
	import ModSetDialog from '$lib/components/ModSetDialog.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import { addressError, FIKA_ROLES, hostLabel } from '$lib/fika';
	import { forgetGameDir, loadGameDir } from '$lib/gamedir';
	import { loadStopServerOnExit, saveStopServerOnExit } from '$lib/launch';
	import { exportFileName, installTargets, missingFromError } from '$lib/modsets';
	import { setSearchEnabled, setSubNav } from '$lib/shellstate';
	import {
		canApply,
		confirmBlocked,
		confirmationFor,
		formatReleaseDate,
		modCheckView,
		needsModCheck,
		releaseState,
		type ChangeKind,
		type ModCheck
	} from '$lib/spt';
	import {
		applyTheme,
		loadPreference,
		resolveTheme,
		savePreference,
		type ThemePreference
	} from '$lib/theme';
	import { appVersion, checkForUpdate, installUpdate, restartApp } from '$lib/updater';
	import {
		describe,
		progressPercent,
		runCheck,
		runInstall,
		type UpdateDeps,
		type UpdateState
	} from '$lib/updatestate';
	import { cached, invalidate } from '$lib/viewcache';

	const THEME_OPTIONS: { value: ThemePreference; label: string }[] = [
		{ value: 'system', label: 'Use system setting' },
		{ value: 'light', label: 'Light' },
		{ value: 'dark', label: 'Dark' }
	];

	const SECTIONS = [
		{ id: 'game-directory', label: 'Game directory' },
		{ id: 'spt-version', label: 'SPT version' },
		{ id: 'appearance', label: 'Appearance' },
		{ id: 'launching', label: 'Launching' },
		{ id: 'fika', label: 'Fika' },
		{ id: 'mod-sets', label: 'Mod sets' },
		{ id: 'export', label: 'Export the mod list' },
		{ id: 'import', label: 'Import a mod list' },
		{ id: 'catalog-cache', label: 'Catalog cache' },
		{ id: 'updates', label: 'Updates' }
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
	let prompt: 'rename' | 'import' | 'host' | null = $state(null);
	let promptTarget = $state('');
	let promptName = $state('');

	let fikaRole: FikaRole = $state('none');
	let fikaAddress = $state('');
	let fikaBaseUrl = $state('');
	let fikaSaving = $state(false);
	let fikaError = $state('');
	let fikaNote = $state('');
	let fikaPlan: FikaInstallPlan | null = $state(null);
	let fikaBusy = $state(false);
	let fikaProgress: ProgressEvent | null = $state(null);

	let hostFile = $state('');
	let hostCode = $state('');
	let hostSet: ModSet | null = $state(null);
	let hostMissing: string[] = $state([]);
	let hostBusy = $state(false);
	let hostError = $state('');
	let hostNote = $state('');

	let listFile = $state('');
	let listContent = $state('');
	let diff: ImportDiff | null = $state(null);
	let importError = $state('');
	let importNote = $state('');
	let importing = $state(false);

	let version = $state('');
	let updateState: UpdateState = $state({ kind: 'idle' });
	let restartError = $state('');

	let clearing = $state(false);
	let cacheNote = $state('');
	let cacheError = $state('');

	let releases: SptReleaseList | null = $state(null);
	let releasesLoading = $state(false);
	let sptError = $state('');
	let sptNote = $state('');
	let sptBusy = $state(false);
	let sptProgress: ProgressEvent | null = $state(null);
	let pendingKind: ChangeKind | null = $state(null);
	let pendingRelease: SptReleaseInfo | null = $state(null);
	let modCheck: ModCheck | null = $state(null);
	let allowIncompatible = $state(false);

	const currentVersion = $derived(installedVersion());
	const confirmation = $derived(
		pendingKind === null ? null : confirmationFor(pendingRelease, currentVersion, pendingKind)
	);
	const modVerdict = $derived(modCheck === null ? null : modCheckView(modCheck));

	const fikaAddressError = $derived(addressError(fikaRole, fikaAddress));

	const active = $derived(
		SECTIONS.some((section) => `#${section.id}` === page.url.hash)
			? page.url.hash.slice(1)
			: SECTIONS[0].id
	);

	onMount(() => {
		setSubNav([], null);
		setSearchEnabled(false);
		path = loadGameDir();
		void readVersion();
		void loadReleases();
		void loadSets();
		void loadFika();
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

	async function readVersion(): Promise<void> {
		try {
			version = await appVersion();
		} catch {
			version = '';
		}
	}

	function updateDeps(): UpdateDeps {
		return {
			check: checkForUpdate,
			install: installUpdate,
			onState: (state) => {
				updateState = state;
			}
		};
	}

	async function checkUpdate(): Promise<void> {
		restartError = '';
		await runCheck(updateDeps());
	}

	async function installNewVersion(newVersion: string): Promise<void> {
		await runInstall(newVersion, updateDeps());
	}

	async function restart(): Promise<void> {
		restartError = '';
		try {
			await restartApp();
		} catch (cause) {
			restartError = cause instanceof Error ? cause.message : String(cause);
		}
	}

	function updateBusy(): boolean {
		return (
			updateState.kind === 'checking' ||
			updateState.kind === 'downloading' ||
			updateState.kind === 'installing'
		);
	}

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

	async function loadFika(): Promise<void> {
		if (path === null) {
			return;
		}
		try {
			applyFika(await getFikaSettings(path));
		} catch (cause) {
			fikaError = cause instanceof Error ? cause.message : String(cause);
		}
	}

	function applyFika(settings: FikaSettings): void {
		fikaRole = settings.role;
		fikaAddress = settings.host_address;
		fikaBaseUrl = settings.base_url;
	}

	async function saveFika(): Promise<void> {
		if (path === null) {
			return;
		}
		fikaSaving = true;
		fikaError = '';
		fikaNote = '';
		try {
			applyFika(await setFikaSettings(path, fikaRole, fikaRole === 'none' ? '' : fikaAddress));
			fikaNote =
				fikaBaseUrl === ''
					? 'Saved.'
					: `Saved. The game connects to ${hostLabel(fikaBaseUrl)}.`;
		} catch (cause) {
			fikaError = cause instanceof Error ? cause.message : String(cause);
		} finally {
			fikaSaving = false;
		}
	}

	async function openFikaPlan(): Promise<void> {
		if (path === null) {
			return;
		}
		fikaBusy = true;
		fikaError = '';
		fikaNote = '';
		try {
			fikaPlan = await planFikaInstall(path);
		} catch (cause) {
			fikaError = cause instanceof Error ? cause.message : String(cause);
		} finally {
			fikaBusy = false;
		}
	}

	async function installFika(): Promise<void> {
		const target = fikaPlan;
		if (path === null || target === null) {
			return;
		}
		fikaPlan = null;
		fikaBusy = true;
		fikaError = '';
		fikaNote = '';
		const operationId = crypto.randomUUID();
		const unsubscribe = await subscribe(operationId, (event) => {
			fikaProgress = event;
		});
		try {
			await installMod(path, target.server_mod_id, null, false, operationId);
			await installMod(path, target.client_mod_id, null, false, operationId);
			fikaNote = 'Fika is installed: the server mod and the client plugin.';
		} catch (cause) {
			fikaError = cause instanceof Error ? cause.message : String(cause);
		} finally {
			unsubscribe();
			fikaProgress = null;
			fikaBusy = false;
		}
	}

	function planSize(bytes: number | null): string {
		return bytes === null ? 'unknown size' : `${(bytes / 1_048_576).toFixed(1)} MB`;
	}

	async function pickHostFile(): Promise<void> {
		hostError = '';
		try {
			const picked = await open({
				multiple: false,
				filters: [{ name: 'Mod set', extensions: ['json'] }]
			});
			if (picked !== null) {
				hostFile = picked;
			}
		} catch (cause) {
			hostError = cause instanceof Error ? cause.message : String(cause);
		}
	}

	function hasHostSource(): boolean {
		return hostFile.trim() !== '' || hostCode.trim() !== '';
	}

	async function importHostSet(name: string | null): Promise<void> {
		if (path === null || !hasHostSource()) {
			return;
		}
		hostBusy = true;
		hostError = '';
		hostNote = '';
		let imported: ModSet | null = null;
		try {
			imported = await importModSet(
				path,
				hostCode.trim() === '' ? { setFile: hostFile.trim() } : { content: hostCode },
				name
			);
			hostSet = imported;
			hostFile = '';
			hostCode = '';
		} catch (cause) {
			if (cause instanceof ApiError && cause.code === 'MOD_SET_EXISTS') {
				promptName = '';
				prompt = 'host';
			} else {
				hostError = cause instanceof Error ? cause.message : String(cause);
			}
		} finally {
			hostBusy = false;
		}
		if (imported !== null) {
			await loadSets();
			await applyHostSet(imported.name, false);
		}
	}

	async function applyHostSet(name: string, ignoreMissing: boolean): Promise<void> {
		if (path === null) {
			return;
		}
		hostBusy = true;
		hostError = '';
		hostNote = '';
		try {
			const applied = await applyModSet(path, name, ignoreMissing);
			hostMissing = [];
			hostNote = `"${applied.name}": ${applied.enabled.length} on, ${applied.disabled.length} off, ${applied.missing.length} missing.`;
		} catch (cause) {
			const missing = missingFromError(cause);
			if (missing.length > 0) {
				hostMissing = missing;
			} else {
				hostError = cause instanceof Error ? cause.message : String(cause);
			}
		} finally {
			hostBusy = false;
		}
	}

	async function installHostMissing(): Promise<void> {
		const set = hostSet;
		if (path === null || set === null) {
			return;
		}
		hostBusy = true;
		hostError = '';
		const failed: string[] = [];
		for (const { name, target } of installTargets(set, hostMissing)) {
			try {
				await installMod(path, target);
			} catch (cause) {
				failed.push(`${name}: ${cause instanceof Error ? cause.message : String(cause)}`);
			}
		}
		hostBusy = false;
		if (failed.length > 0) {
			hostError = `Some mods could not be installed. ${failed.join(' · ')}`;
			return;
		}
		hostMissing = [];
		await applyHostSet(set.name, false);
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
		const target = prompt;
		prompt = null;
		if (target === 'host') {
			await importHostSet(name);
			return;
		}
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

	function installedVersion(): string {
		return releases === null ? '' : releases.current_version;
	}

	/**
	 * Show the SPT releases, asking the release source only when told to.
	 * Without ``refresh`` the answer of this app run is reused, so opening the
	 * page repeatedly costs the source nothing; the refresh button is the one
	 * way to fetch again.
	 */
	async function loadReleases(refresh = false): Promise<void> {
		const gameDir = path;
		if (gameDir === null) {
			return;
		}
		const view = cached(gameDir, 'list_spt_releases', [], () =>
			listSptReleases(gameDir, refresh)
		);
		if (!refresh && view.value !== undefined) {
			releases = view.value;
			return;
		}
		releasesLoading = true;
		sptError = '';
		try {
			releases = await view.refresh();
		} catch (cause) {
			sptError = cause instanceof Error ? cause.message : String(cause);
		} finally {
			releasesLoading = false;
		}
	}

	function ask(release: SptReleaseInfo | null, kind: ChangeKind): void {
		pendingRelease = release;
		pendingKind = kind;
		modCheck = null;
		allowIncompatible = false;
		if (needsModCheck(release, kind)) {
			void checkInstalledMods(release.version);
		}
	}

	async function checkInstalledMods(target: string): Promise<void> {
		const gameDir = path;
		if (gameDir === null) {
			return;
		}
		modCheck = { kind: 'pending' };
		try {
			const report = await checkSptCompatibility(gameDir, target);
			if (pendingRelease?.version === target) {
				modCheck = { kind: 'ready', report };
			}
		} catch {
			if (pendingRelease?.version === target) {
				modCheck = { kind: 'failed' };
			}
		}
	}

	function closeConfirmation(): void {
		pendingRelease = null;
		pendingKind = null;
		modCheck = null;
		allowIncompatible = false;
	}

	async function runPending(): Promise<void> {
		const gameDir = path;
		const kind = pendingKind;
		const release = pendingRelease;
		const allowed = allowIncompatible;
		closeConfirmation();
		if (gameDir === null || kind === null) {
			return;
		}
		sptBusy = true;
		sptError = '';
		sptNote = '';
		try {
			const version =
				kind === 'undo' || release === null
					? await undoChange(gameDir)
					: await applyRelease(gameDir, release.version, allowed);
			// The mods view serves the catalog, the installed mods and the SPT
			// version from this cache; dropping it makes it check them again.
			invalidate(gameDir);
			const changed = version === '' ? 'The SPT version changed' : `SPT is now ${version}`;
			sptNote = `${changed}. Mods built for the other version show up under Updates.`;
			await loadReleases();
		} catch (cause) {
			sptError = cause instanceof Error ? cause.message : String(cause);
		} finally {
			sptBusy = false;
			sptProgress = null;
		}
	}

	async function applyRelease(
		gameDir: string,
		version: string,
		allowed: boolean
	): Promise<string> {
		const operationId = crypto.randomUUID();
		const unsubscribe = await subscribe(operationId, (event) => {
			sptProgress = event;
		});
		try {
			const result = await applySptRelease(gameDir, version, operationId, allowed);
			return result.version === '' ? version : result.version;
		} finally {
			unsubscribe();
			sptProgress = null;
		}
	}

	async function undoChange(gameDir: string): Promise<string> {
		const result = await revertSptUpdate(gameDir);
		return result.version;
	}

	function stateLabel(release: SptReleaseInfo): string {
		const state = releaseState(release, currentVersion);
		if (state === 'installed') {
			return 'Installed';
		}
		return state === 'newer' ? 'Newer' : 'Older';
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
				<h2 id="spt-version">SPT version</h2>
				{#if path === null}
					<p class="note">No game directory yet. <a href="/">Run the setup</a> first.</p>
				{:else}
					<p class="row">
						<span>
							Installed: <strong
								>{currentVersion === '' ? 'not readable' : currentVersion}</strong
							>
						</span>
						<span class="note">
							Escape from Tarkov build {releases === null || releases.client_build === ''
								? 'not readable'
								: releases.client_build}
						</span>
						<button
							type="button"
							class="btn-sm"
							disabled={releasesLoading || sptBusy}
							onclick={() => void loadReleases(true)}
						>
							{releasesLoading ? 'Checking…' : 'Check for new versions'}
						</button>
					</p>

					{#if releases !== null && !releases.source_ok}
						<p class="warn-text">{releases.notice}</p>
					{/if}

					{#if sptBusy}
						<div class="status-progress">
							<progress max="100" value={sptProgress?.percent ?? 0}></progress>
							<span
								>Changing SPT…{#if sptProgress !== null}
									{Math.round(sptProgress.percent)}% — {sptProgress.message}
								{/if}</span
							>
						</div>
					{/if}

					{#if sptNote !== ''}
						<p class="note">{sptNote}</p>
					{/if}
					{#if sptError !== ''}
						<p class="error-text">{sptError}</p>
					{/if}

					{#if releasesLoading && releases === null}
						<p class="note">Looking for SPT releases…</p>
					{:else if releases !== null}
						<table class="releases">
							<thead>
								<tr>
									<th>Version</th>
									<th>Required EFT build</th>
									<th>Released</th>
									<th>State</th>
									<th></th>
								</tr>
							</thead>
							<tbody>
								{#each releases.releases as release (release.version)}
									{@const state = releaseState(release, currentVersion)}
									{@const check = canApply(release, currentVersion)}
									<tr>
										<td>
											<a href={release.url} target="_blank" rel="noreferrer">
												{release.version}
											</a>
										</td>
										<td>
											{release.client_build === '' ? '—' : release.client_build}
										</td>
										<td>{formatReleaseDate(release.published_at)}</td>
										<td>{stateLabel(release)}</td>
										<td>
											{#if state !== 'installed'}
												<button
													type="button"
													class="btn-sm"
													disabled={!check.ok || sptBusy}
													title={check.reason}
													onclick={() =>
														ask(
															release,
															state === 'newer' ? 'update' : 'downgrade'
														)}
												>
													{state === 'newer'
														? 'Update'
														: 'Go back to this version'}
												</button>
											{/if}
										</td>
									</tr>
								{/each}
								{#if releases.releases.length === 0}
									<tr><td colspan="5" class="note">No SPT releases found.</td></tr>
								{/if}
							</tbody>
						</table>
						<p class="note">
							SPT keeps a download only for the newest release of a line. To get back to
							the version you had before, use the undo below.
						</p>
					{/if}

					<div class="row">
						<button type="button" disabled={sptBusy} onclick={() => ask(null, 'undo')}>
							Undo the last SPT change
						</button>
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
				<h2 id="fika">Fika</h2>
				<p class="note">
					Fika lets several players share one SPT server. Pick what this machine does.
				</p>
				<div class="roles">
					{#each FIKA_ROLES as option (option.id)}
						<label>
							<input
								type="radio"
								name="fika-role"
								value={option.id}
								checked={fikaRole === option.id}
								onchange={() => (fikaRole = option.id)}
							/>
							{option.label}
						</label>
						<p class="note hint">{option.hint}</p>
					{/each}
				</div>
				{#if fikaRole !== 'none'}
					<div class="row">
						<label>
							Host address
							<input
								type="text"
								placeholder="192.168.1.20"
								aria-label="Host address"
								bind:value={fikaAddress}
							/>
						</label>
					</div>
					{#if fikaAddressError !== ''}
						<p class="error-text">{fikaAddressError}</p>
					{/if}
				{/if}
				<div class="row">
					<button
						type="button"
						class="btn-primary"
						disabled={path === null || fikaSaving || fikaAddressError !== ''}
						onclick={() => void saveFika()}
					>
						{fikaSaving ? 'Saving…' : 'Save'}
					</button>
					{#if fikaBaseUrl !== ''}
						<span class="note">Stored address: {hostLabel(fikaBaseUrl)}</span>
					{/if}
				</div>
				{#if fikaError !== ''}
					<p class="error-text">{fikaError}</p>
				{/if}
				{#if fikaNote !== ''}
					<p class="note">{fikaNote}</p>
				{/if}

				<h3>Install Fika</h3>
				<p class="note">
					Fika needs two parts: a mod for the server and a plugin for the game. Both are
					installed together.
				</p>
				<div class="row">
					<button
						type="button"
						disabled={path === null || fikaBusy}
						onclick={() => void openFikaPlan()}
					>
						Install Fika (server mod + client plugin)
					</button>
					{#if fikaProgress !== null}
						<span class="note">{fikaProgress.message}</span>
					{/if}
				</div>

				<h3>Apply the host's mod set</h3>
				<p class="note">
					The host shares the mods they play with, either as a file or as a share code. A
					pasted code wins over the file path.
				</p>
				<div class="row">
					<input
						type="text"
						placeholder="/path/to/mod-set.json"
						aria-label="Host mod set file"
						bind:value={hostFile}
					/>
					<button type="button" onclick={() => void pickHostFile()}>Choose file…</button>
				</div>
				<div class="row">
					<textarea
						rows="3"
						placeholder="Paste the share code the host sent"
						aria-label="Host mod set share code"
						bind:value={hostCode}
					></textarea>
					<button
						type="button"
						class="btn-primary"
						disabled={path === null || hostBusy || !hasHostSource()}
						onclick={() => void importHostSet(null)}
					>
						Use the host's mods
					</button>
				</div>
				{#if hostError !== ''}
					<p class="error-text">{hostError}</p>
				{/if}
				{#if hostNote !== ''}
					<p class="note">{hostNote}</p>
				{/if}
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

			<section class="section panel">
				<h2 id="updates">Updates</h2>
				<p class="row">
					Installed version: <code>{version === '' ? 'unknown' : version}</code>
				</p>
				<div class="row">
					<button
						type="button"
						class="btn-primary"
						disabled={updateBusy()}
						onclick={() => void checkUpdate()}
					>
						Check for updates
					</button>
					{#if updateState.kind === 'available'}
						{@const newVersion = updateState.version}
						<button type="button" onclick={() => void installNewVersion(newVersion)}>
							Install
						</button>
					{/if}
					{#if updateState.kind === 'ready'}
						<button type="button" onclick={() => void restart()}>Restart now</button>
					{/if}
					{#if progressPercent(updateState) !== null}
						<span class="note">{progressPercent(updateState)}%</span>
					{/if}
				</div>
				{#if describe(updateState) !== ''}
					<p class={updateState.kind === 'failed' ? 'error-text' : 'note'}>
						{describe(updateState)}
					</p>
				{/if}
				{#if updateState.kind === 'available' && updateState.notes !== ''}
					<p class="note">{updateState.notes}</p>
				{/if}
				{#if restartError !== ''}
					<p class="error-text">{restartError}</p>
				{/if}
			</section>
		</div>
	</div>
</div>

{#if confirmation !== null}
	<Dialog title={confirmation.title} onclose={closeConfirmation}>
		<p>{confirmation.body}</p>
		{#if modVerdict !== null}
			<p class={modVerdict.tone === 'warn' ? 'warn-text' : 'note'}>{modVerdict.message}</p>
			{#if modVerdict.incompatible.length > 0}
				<ul class="mod-verdict">
					{#each modVerdict.incompatible as mod (mod)}
						<li>{mod}</li>
					{/each}
				</ul>
			{/if}
			{#if modVerdict.skippedNote !== ''}
				<p class="note">{modVerdict.skippedNote}</p>
			{/if}
			{#if modVerdict.needsOverride}
				<label class="override">
					<input type="checkbox" bind:checked={allowIncompatible} />
					Change the SPT version anyway
				</label>
			{/if}
		{/if}
		{#snippet actions()}
			<button
				type="button"
				class="btn-primary"
				disabled={confirmBlocked(modVerdict, allowIncompatible)}
				onclick={() => void runPending()}
			>
				Continue
			</button>
		{/snippet}
	</Dialog>
{/if}

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
				disabled={setsBusy || hostBusy || promptName.trim() === ''}
				onclick={() => void confirmPrompt()}
			>
				Save
			</button>
		{/snippet}
	</Dialog>
{/if}

{#if fikaPlan !== null}
	<Dialog title="Install Fika" onclose={() => (fikaPlan = null)}>
		<ul class="plan">
			{#each fikaPlan.plan.entries as planned (planned.version_id)}
				<li>
					<span class="name">{planned.name} {planned.version}</span>
					{#if planned.is_dependency}<span class="kind">dependency</span>{/if}
					<span class="kind">{planSize(planned.content_length)}</span>
				</li>
			{/each}
		</ul>
		<p class="note">Resolved for SPT {fikaPlan.plan.spt_version}.</p>

		{#snippet actions()}
			<button type="button" class="btn-primary" onclick={() => void installFika()}>
				Install
			</button>
		{/snippet}
	</Dialog>
{/if}

{#if hostMissing.length > 0}
	<ModSetDialog
		title="Mods are missing"
		missing={hostMissing}
		busy={hostBusy}
		onInstall={() => void installHostMissing()}
		onIgnore={() => void applyHostSet(hostSet?.name ?? '', true)}
		onclose={() => (hostMissing = [])}
	/>
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

	.releases {
		width: 100%;
		border-collapse: collapse;
		font-size: var(--text-sm);
	}

	.releases th {
		padding: var(--space-1) var(--space-2);
		color: var(--color-text-muted);
		font-size: var(--text-xs);
		font-weight: var(--weight-semibold);
		letter-spacing: var(--tracking-caps);
		text-align: left;
		text-transform: uppercase;
	}

	.releases td {
		padding: var(--space-1) var(--space-2);
		border-top: 1px solid var(--color-divider);
	}

	.releases td:last-child {
		text-align: right;
	}

	.status-progress {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}

	.status-progress progress {
		width: 100%;
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

	.roles {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}

	.hint {
		margin-left: var(--space-6);
	}

	.plan {
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.plan li {
		padding: var(--space-1) 0;
	}

	.kind {
		margin-right: var(--space-2);
		color: var(--color-text-muted);
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

	.mod-verdict {
		margin: 0 0 var(--space-3);
		padding-left: var(--space-5);
		max-height: 12rem;
		overflow-y: auto;
	}

	.override {
		display: flex;
		align-items: center;
		gap: var(--space-2);
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
