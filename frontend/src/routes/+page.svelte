<script module lang="ts">
	const autoSynced = new Set<string>();
</script>

<script lang="ts">
	import { onMount } from 'svelte';
	import ModsView from '$lib/components/ModsView.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import SetupScreen from '$lib/components/SetupScreen.svelte';
	import {
		launchStatus,
		status,
		syncCatalog,
		updateMod,
		type LaunchStatus,
		type StatusReport,
		type SyncCatalogResponse
	} from '$lib/api/client';
	import { ApiError, subscribe, type ProgressEvent } from '$lib/api/transport';
	import { forgetGameDir, loadGameDir, saveGameDir } from '$lib/gamedir';
	import { bannerText, isGameUp, nextPollDelay } from '$lib/launch';
	import { onChangeFolder, onSyncRequest, setGameDir } from '$lib/shellstate';
	import { runUpdates, summarize, type UpdateTarget } from '$lib/updateall';
	import { cached } from '$lib/viewcache';

	type Phase = 'checking' | 'setup' | 'ready' | 'unreachable';

	let phase: Phase = $state('checking');
	let path = $state('');
	let error = $state('');
	let sptVersion: string | null = $state(null);
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
		const unsubscribeSync = onSyncRequest(() => void sync());
		const unsubscribeFolder = onChangeFolder(() => {
			clearTimeout(pollTimer);
			forgetGameDir();
			phase = 'setup';
		});
		return () => {
			clearTimeout(pollTimer);
			unsubscribeSync();
			unsubscribeFolder();
			setGameDir(null);
		};
	});

	$effect(() => {
		setGameDir(phase === 'ready' ? path : null);
	});

	async function pollGameStatus(gameDir: string): Promise<void> {
		try {
			gameStatus = await launchStatus(gameDir);
		} catch {
			gameStatus = null;
		}
		pollTimer = setTimeout(() => void pollGameStatus(gameDir), nextPollDelay(gameStatus));
	}

	function noteSptVersion(report: StatusReport): void {
		sptVersion = report.spt_version === '' ? null : report.spt_version;
	}

	async function detect(): Promise<void> {
		error = '';
		const stored = loadGameDir();
		if (stored === null) {
			phase = 'setup';
			return;
		}
		const view = cached(stored, 'status', [], () => status(stored));
		if (view.value !== undefined) {
			noteSptVersion(view.value);
			path = stored;
			phase = 'ready';
			void pollGameStatus(stored);
			void view
				.refresh()
				.then(noteSptVersion)
				.catch((cause: unknown) => {
					if (cause instanceof ApiError && cause.code === 'NOT_A_GAME_DIR') {
						clearTimeout(pollTimer);
						forgetGameDir();
						phase = 'setup';
					}
				});
			autoSync(stored);
			return;
		}
		phase = 'checking';
		try {
			noteSptVersion(await view.refresh());
			path = stored;
			phase = 'ready';
			void pollGameStatus(stored);
			autoSync(stored);
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

	// Keep the catalog fresh once per game directory and app session; delta
	// syncs are cheap, but a sync on every tab switch is not.
	function autoSync(gameDir: string): void {
		if (autoSynced.has(gameDir)) {
			return;
		}
		autoSynced.add(gameDir);
		void sync();
	}

	function onReady(gameDir: string): void {
		saveGameDir(gameDir);
		path = gameDir;
		phase = 'ready';
		void pollGameStatus(gameDir);
		autoSync(gameDir);
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
	<p class="page-empty">Starting the sptmod backend…</p>
{:else if phase === 'unreachable'}
	<div class="page-empty">
		<p>{error}</p>
		<button type="button" onclick={() => void detect()}>Try again</button>
	</div>
{:else if phase === 'setup'}
	<SetupScreen initialPath={path} onready={onReady} />
{:else}
	{#if bannerText(gameStatus) !== ''}
		<p class="banner">{bannerText(gameStatus)}</p>
	{/if}
	<div class="page">
		<PageHeader title="Mods">
			{#snippet secondary()}
				<button
					type="button"
					class="btn-secondary"
					disabled={syncing || updatingAll}
					onclick={() => void sync()}
				>
					{syncing ? 'Syncing…' : 'Sync catalog'}
				</button>
			{/snippet}
			{#snippet primary()}
				<button
					type="button"
					class="btn-primary"
					disabled={syncing || updatingAll || rowsBusy || candidates.length === 0}
					title={updatesChecked && candidates.length === 0
						? 'Everything is up to date'
						: undefined}
					onclick={() => void updateAll()}
				>
					{updatingAll ? 'Updating…' : `Update all (${candidates.length})`}
				</button>
			{/snippet}
		</PageHeader>
		{#if syncing}
			<div class="page-status status-progress">
				<progress max="100" value={syncProgress?.percent ?? 0}></progress>
				<span
					>Updating the mod catalog…{#if syncProgress !== null}
						{Math.round(syncProgress.percent)}% — {syncProgress.message}
					{/if}</span
				>
			</div>
		{:else if updatingAll}
			<div class="page-status status-progress">
				<progress max="100" value={updateProgress?.percent ?? 0}></progress>
				<span>Updating {updateName} ({updateIndex} of {updateTotal})</span>
			</div>
		{:else if syncNote !== ''}
			<p
				class="page-status note-line"
				class:success={syncNoteSuccess}
				class:failure={!syncNoteSuccess}
			>
				{syncNote}
			</p>
		{/if}
		<ModsView
			{path}
			{refreshKey}
			{sptVersion}
			locked={updatingAll || isGameUp(gameStatus)}
			onbusy={(busy) => (rowsBusy = busy)}
			onupdatable={(targets) => {
				candidates = targets;
				updatesChecked = true;
			}}
		/>
	</div>
{/if}

<style>
	.page-empty p {
		margin: 0 0 var(--space-4);
	}

	.banner {
		margin: 0;
		padding: var(--space-2) var(--space-6);
		background: var(--color-warn-soft);
		color: var(--color-warn);
		border-bottom: 1px solid var(--color-warn-border);
		font-size: var(--text-base);
	}

	.page-status {
		margin-bottom: var(--space-4);
	}

	.status-progress {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}

	.status-progress progress {
		width: 100%;
	}

	.status-progress span {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.note-line {
		margin: 0 0 var(--space-4);
		padding: var(--space-2) var(--space-3);
		border-radius: var(--radius-md);
	}

	.note-line.success {
		background: var(--color-success-soft);
		color: var(--color-success);
	}

	.note-line.failure {
		background: var(--color-danger-soft);
		color: var(--color-danger);
	}
</style>
