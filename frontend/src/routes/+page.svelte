<script lang="ts">
	import { onMount } from 'svelte';
	import ModsView from '$lib/components/ModsView.svelte';
	import SetupScreen from '$lib/components/SetupScreen.svelte';
	import {
		launchStatus,
		status,
		syncCatalog,
		updateMod,
		type LaunchStatus,
		type SyncCatalogResponse
	} from '$lib/api/client';
	import { ApiError, subscribe, type ProgressEvent } from '$lib/api/transport';
	import { forgetGameDir, loadGameDir, saveGameDir } from '$lib/gamedir';
	import { bannerText, isGameUp, nextPollDelay } from '$lib/launch';
	import { runUpdates, summarize, type UpdateTarget } from '$lib/updateall';

	type Phase = 'checking' | 'setup' | 'ready' | 'unreachable';

	let phase: Phase = $state('checking');
	let path = $state('');
	let error = $state('');
	let syncProgress: ProgressEvent | null = $state(null);
	let syncNote = $state('');
	let syncNoteSuccess = $state(false);
	let syncing = $state(false);
	let refreshKey = $state(0);
	let candidates: UpdateTarget[] = $state([]);
	let updatesChecked = $state(false);
	let updatingAll = $state(false);
	let updateProgress: ProgressEvent | null = $state(null);
	let updateName = $state('');
	let updateIndex = $state(0);
	let updateTotal = $state(0);
	let rowsBusy = $state(false);
	let gameStatus: LaunchStatus | null = $state(null);
	let pollTimer: ReturnType<typeof setTimeout> | undefined;

	onMount(() => {
		void detect();
		return () => clearTimeout(pollTimer);
	});

	async function pollGameStatus(gameDir: string): Promise<void> {
		try {
			gameStatus = await launchStatus(gameDir);
		} catch {
			gameStatus = null;
		}
		pollTimer = setTimeout(() => void pollGameStatus(gameDir), nextPollDelay(gameStatus));
	}

	async function detect(): Promise<void> {
		phase = 'checking';
		error = '';
		const stored = loadGameDir();
		if (stored === null) {
			phase = 'setup';
			return;
		}
		try {
			await status(stored);
			path = stored;
			phase = 'ready';
			void pollGameStatus(stored);
			// Keep the catalog fresh on every app start; delta syncs are cheap
			// and the list refreshes when it finishes.
			void sync();
		} catch (cause) {
			if (cause instanceof ApiError && cause.code === 'NOT_A_GAME_DIR') {
				forgetGameDir();
				path = stored;
				phase = 'setup';
				return;
			}
			error = cause instanceof Error ? cause.message : String(cause);
			phase = 'unreachable';
		}
	}

	function onReady(gameDir: string): void {
		saveGameDir(gameDir);
		path = gameDir;
		phase = 'ready';
		void pollGameStatus(gameDir);
		void sync();
	}

	function syncSummary(result: SyncCatalogResponse): string {
		const parts: string[] = [];
		if (result.new_mods > 0) {
			parts.push(`${result.new_mods} new`);
		}
		if (result.updated_mods > 0) {
			parts.push(`${result.updated_mods} changed`);
		}
		const versions = result.new_versions + result.updated_versions;
		if (versions > 0) {
			parts.push(versions === 1 ? '1 version update' : `${versions} version updates`);
		}
		if (parts.length === 0) {
			return 'Catalog is up to date';
		}
		return `Catalog updated · ${parts.join(', ')}`;
	}

	async function sync(): Promise<void> {
		syncing = true;
		syncNote = '';
		syncNoteSuccess = false;
		syncProgress = null;
		const operationId = crypto.randomUUID();
		const unsubscribe = await subscribe(operationId, (event) => {
			syncProgress = event;
		});
		try {
			const result = await syncCatalog(path, false, operationId);
			syncNote = syncSummary(result);
			syncNoteSuccess = true;
			refreshKey += 1;
		} catch (cause) {
			syncNote = cause instanceof Error ? cause.message : String(cause);
			syncNoteSuccess = false;
		} finally {
			unsubscribe();
			syncProgress = null;
			syncing = false;
		}
	}

	async function updateAll(): Promise<void> {
		const targets = candidates;
		updatingAll = true;
		syncNote = '';
		syncNoteSuccess = false;
		const result = await runUpdates(targets, {
			update: updateOne,
			onProgress: (index, total, name) => {
				updateIndex = index;
				updateTotal = total;
				updateName = name;
				updateProgress = null;
			}
		});
		candidates = targets.filter((target) =>
			result.failed.some((failure) => failure.name === target.name)
		);
		syncNote = summarize(result);
		refreshKey += 1;
		updatingAll = false;
		updateName = '';
	}

	async function updateOne(name: string, operationId: string): Promise<void> {
		const unsubscribe = await subscribe(operationId, (event) => {
			updateProgress = event;
		});
		try {
			await updateMod(path, name, false, operationId);
		} finally {
			unsubscribe();
			updateProgress = null;
		}
	}
</script>

{#if phase === 'checking'}
	<p class="notice">Starting the sptmod backend…</p>
{:else if phase === 'unreachable'}
	<div class="notice">
		<p>{error}</p>
		<button type="button" onclick={() => void detect()}>Try again</button>
	</div>
{:else if phase === 'setup'}
	<SetupScreen initialPath={path} onready={onReady} />
{:else}
	<header>
		<h1>sptmod</h1>
		<span class="path" title={path}>{path}</span>
		{#if syncing}
			<div class="sync-slot">
				<progress max="100" value={syncProgress?.percent ?? 0}></progress>
				<span
					>Updating the mod catalog…{#if syncProgress !== null}
						{Math.round(syncProgress.percent)}% — {syncProgress.message}
					{/if}</span
				>
			</div>
		{:else if updatingAll}
			<div class="sync-slot">
				<progress max="100" value={updateProgress?.percent ?? 0}></progress>
				<span>Updating {updateName} ({updateIndex} of {updateTotal})</span>
			</div>
		{:else if syncNote !== ''}
			<p class="sync-slot note" class:success={syncNoteSuccess}>{syncNote}</p>
		{/if}
		<button type="button" disabled={syncing || updatingAll} onclick={() => void sync()}>
			{syncing ? 'Syncing…' : 'Sync catalog'}
		</button>
		<button
			type="button"
			disabled={syncing || updatingAll || rowsBusy || candidates.length === 0}
			title={updatesChecked && candidates.length === 0 ? 'Everything is up to date' : undefined}
			onclick={() => void updateAll()}
		>
			{updatingAll ? 'Updating…' : `Update all (${candidates.length})`}
		</button>
	</header>
	{#if bannerText(gameStatus) !== ''}
		<p class="banner">{bannerText(gameStatus)}</p>
	{/if}
	<ModsView
		{path}
		{refreshKey}
		locked={updatingAll || isGameUp(gameStatus)}
		onbusy={(busy) => (rowsBusy = busy)}
		onupdatable={(targets) => {
			candidates = targets;
			updatesChecked = true;
		}}
	/>
{/if}

<style>
	header {
		display: flex;
		align-items: baseline;
		gap: 0.8rem;
		padding: 0.8rem 1rem;
		border-bottom: 1px solid #2c3138;
	}

	h1 {
		margin: 0;
		font-size: 1.2rem;
	}

	.path {
		flex: 1;
		color: #98a2ad;
		font-size: 0.9em;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.notice {
		padding: 2rem;
	}

	.banner {
		margin: 0;
		padding: 0.5rem 1rem;
		border-bottom: 1px solid #2c3138;
		background: #2a2419;
		color: #f0cf7d;
	}

	.sync-slot {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		align-self: center;
		max-width: 25%;
		min-width: 0;
		margin: 0;
		font-size: 0.85em;
		color: #98a2ad;
	}

	.sync-slot progress {
		width: 100%;
	}

	.sync-slot span,
	.sync-slot.note {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.sync-slot.note {
		padding: 0.2rem 0.4rem;
	}

	.sync-slot.note.success {
		border-left: 2px solid #4ba36a;
		border-radius: 3px;
		background: #1c2a21;
		color: #b6d9c1;
	}
</style>
