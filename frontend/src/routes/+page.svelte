<script lang="ts">
	import { onMount } from 'svelte';
	import ModsView from '$lib/components/ModsView.svelte';
	import SetupScreen from '$lib/components/SetupScreen.svelte';
	import { status, syncCatalog, updateMod } from '$lib/api/client';
	import { ApiError, subscribe, type ProgressEvent } from '$lib/api/transport';
	import { forgetGameDir, loadGameDir, saveGameDir } from '$lib/gamedir';
	import { runUpdates, summarize, type UpdateTarget } from '$lib/updateall';

	type Phase = 'checking' | 'setup' | 'ready' | 'unreachable';

	let phase: Phase = $state('checking');
	let path = $state('');
	let error = $state('');
	let syncProgress: ProgressEvent | null = $state(null);
	let syncNote = $state('');
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

	onMount(() => {
		void detect();
	});

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
		void sync();
	}

	async function sync(): Promise<void> {
		syncing = true;
		syncNote = '';
		const operationId = crypto.randomUUID();
		const unsubscribe = await subscribe(operationId, (event) => {
			syncProgress = event;
		});
		try {
			const result = await syncCatalog(path, false, operationId);
			syncNote = `Catalog synced: ${result.new_mods} new mods, ${result.updated_mods} updated.`;
			refreshKey += 1;
		} catch (cause) {
			syncNote = cause instanceof Error ? cause.message : String(cause);
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
	{#if syncProgress !== null}
		<div class="sync">
			<progress max="100" value={syncProgress.percent}></progress>
			<span>{syncProgress.step}: {syncProgress.message}</span>
		</div>
	{:else if updatingAll}
		<div class="sync">
			<progress max="100" value={updateProgress?.percent ?? 0}></progress>
			<span>Updating {updateName} ({updateIndex} of {updateTotal})</span>
		</div>
	{:else if syncNote !== ''}
		<p class="sync-note">{syncNote}</p>
	{/if}
	<ModsView
		{path}
		{refreshKey}
		locked={updatingAll}
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

	.sync {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		padding: 0.5rem 1rem;
		font-size: 0.9em;
		color: #98a2ad;
	}

	.sync-note {
		padding: 0.2rem 1rem;
		margin: 0;
		font-size: 0.9em;
		color: #98a2ad;
	}
</style>
