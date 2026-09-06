<script lang="ts">
	import { onMount } from 'svelte';
	import Dialog from '$lib/components/Dialog.svelte';
	import {
		disableMod,
		history,
		listMods,
		modFiles,
		removeMod,
		type CommitInfo,
		type Conflict,
		type ModFile,
		type ModInfo,
		type ProfileResidue
	} from '$lib/api/client';
	import { ApiError } from '$lib/api/transport';
	import { loadGameDir } from '$lib/gamedir';
	import { cached, invalidate } from '$lib/viewcache';

	const UNDOABLE_STATES: ModInfo['state'][] = ['installed', 'updated', 'enabled'];

	const STATE_BADGES = {
		installed: 'badge-success',
		updated: 'badge-success',
		enabled: 'badge-success',
		disabled: 'badge-neutral',
		removed: 'badge-danger'
	} satisfies Record<ModInfo['state'], string>;

	const FORCEABLE_CODES = [
		'CONFLICT',
		'DEPENDENCY',
		'UNRESOLVABLE_DEPENDENCY',
		'PROFILE_RESIDUE'
	];

	let path: string | null = $state(null);
	let commits: CommitInfo[] = $state([]);
	let modStates: Map<string, ModInfo['state']> = $state(new Map());
	let loading = $state(true);
	let error = $state('');
	let expanded: string | null = $state(null);
	let files: ModFile[] = $state([]);
	let filesError = $state('');
	let confirmation: { kind: 'remove' | 'disable'; name: string } | null = $state(null);
	let failure: {
		name: string;
		message: string;
		conflicts: Conflict[];
		residues: ProfileResidue[];
		forceable: boolean;
	} | null = $state(null);
	let busy = $state(false);

	onMount(() => {
		const stored = loadGameDir();
		path = stored;
		if (stored === null) {
			loading = false;
			return;
		}
		void load(stored);
	});

	function apply(log: CommitInfo[], mods: ModInfo[]): void {
		commits = log;
		modStates = new Map(mods.map((mod) => [mod.name.toLowerCase(), mod.state] as const));
	}

	async function load(gameDir: string): Promise<void> {
		const historyView = cached(gameDir, 'history', [], () => history(gameDir));
		const modsView = cached(gameDir, 'list_mods', [], () => listMods(gameDir, true));
		const historyHit = historyView.value;
		const modsHit = modsView.value;
		if (historyHit !== undefined && modsHit !== undefined) {
			apply(historyHit, modsHit);
			loading = false;
		} else {
			loading = true;
		}
		error = '';
		try {
			const [log, mods] = await Promise.all([historyView.refresh(), modsView.refresh()]);
			apply(log, mods);
		} catch (cause) {
			error = cause instanceof Error ? cause.message : String(cause);
		} finally {
			loading = false;
		}
	}

	function modName(commit: CommitInfo): string {
		return commit.trailers['Mod'] ?? '';
	}

	function summary(commit: CommitInfo): string {
		const name = modName(commit);
		if (name === '') {
			return '';
		}
		const parts = [commit.trailers['Mod-Version'], commit.trailers['Mod-Type']].filter(
			(part) => part !== undefined && part !== ''
		);
		return [name, ...parts].join(' · ');
	}

	function modStateLabel(commit: CommitInfo): string {
		return commit.trailers['Mod-State'] ?? '';
	}

	function modStateBadge(commit: CommitInfo): string {
		const label = modStateLabel(commit);
		return label in STATE_BADGES
			? STATE_BADGES[label as ModInfo['state']]
			: 'badge-neutral';
	}

	function undoable(commit: CommitInfo): boolean {
		const state = modStates.get(modName(commit).toLowerCase());
		return state !== undefined && UNDOABLE_STATES.includes(state);
	}

	async function toggleFiles(commit: CommitInfo): Promise<void> {
		if (expanded === commit.hash) {
			expanded = null;
			return;
		}
		expanded = commit.hash;
		files = [];
		filesError = '';
		if (path === null) {
			return;
		}
		try {
			files = await modFiles(path, modName(commit));
		} catch (cause) {
			filesError = cause instanceof Error ? cause.message : String(cause);
		}
	}

	async function undo(
		kind: 'remove' | 'disable',
		name: string,
		force: boolean = false
	): Promise<void> {
		if (path === null) {
			return;
		}
		busy = true;
		error = '';
		failure = null;
		try {
			if (kind === 'remove') {
				await removeMod(path, name, force);
			} else {
				await disableMod(path, name);
			}
			invalidate(path);
			await load(path);
		} catch (cause) {
			if (cause instanceof ApiError) {
				failure = {
					name,
					message: cause.message,
					conflicts: cause.details?.conflicts ?? [],
					residues: cause.details?.residues ?? [],
					forceable: kind === 'remove' && !force && FORCEABLE_CODES.includes(cause.code)
				};
			} else {
				error = cause instanceof Error ? cause.message : String(cause);
			}
		} finally {
			busy = false;
		}
	}
</script>

<section class="page">
	<div class="page-head">
		<h1 class="page-title">History</h1>
	</div>

	{#if path === null}
		<p class="page-empty">No game directory yet. <a href="/">Run the setup</a> first.</p>
	{:else}
		{#if error !== ''}
			<p class="error-text">{error}</p>
		{/if}

		<ul class="panel entries">
			{#each commits as commit (commit.hash)}
				<li class="panel-row">
					<div class="entry">
						<div class="text">
							<span class="subject">{commit.subject}</span>
							<span class="meta muted">
								<code>{commit.hash.slice(0, 8)}</code>
								{new Date(commit.date).toLocaleString()}
								{#if summary(commit) !== ''}
									· {summary(commit)}
								{/if}
								{#if modStateLabel(commit) !== ''}
									<span class="badge {modStateBadge(commit)}">{modStateLabel(commit)}</span>
								{/if}
							</span>
						</div>
						<div class="actions">
							{#if modName(commit) !== ''}
								<button class="btn-sm" type="button" onclick={() => void toggleFiles(commit)}>
									{expanded === commit.hash ? 'Hide files' : 'Files'}
								</button>
							{/if}
							{#if undoable(commit)}
								<button
									class="btn-sm"
									type="button"
									disabled={busy}
									onclick={() =>
										(confirmation = { kind: 'disable', name: modName(commit) })}
								>
									Disable
								</button>
								<button
									class="btn-sm"
									type="button"
									disabled={busy}
									onclick={() =>
										(confirmation = { kind: 'remove', name: modName(commit) })}
								>
									Remove
								</button>
							{/if}
						</div>
					</div>
					{#if expanded === commit.hash}
						<ul class="files">
							{#if filesError !== ''}
								<li class="error-text">{filesError}</li>
							{/if}
							{#each files as file (file.path)}
								<li><code>{file.change}</code> {file.path}</li>
							{/each}
						</ul>
					{/if}
				</li>
			{/each}
			{#if commits.length === 0}
				<li class="empty">{loading ? 'Loading…' : 'No history yet.'}</li>
			{/if}
		</ul>

		<p class="result-count">
			{commits.length}
			{commits.length === 1 ? 'entry' : 'entries'}
		</p>
	{/if}
</section>

{#if confirmation !== null}
	<Dialog
		title={confirmation.kind === 'remove'
			? `Remove ${confirmation.name}?`
			: `Disable ${confirmation.name}?`}
		onclose={() => (confirmation = null)}
	>
		<p>
			{confirmation.kind === 'remove'
				? 'The mod files are removed from the game directory; the history keeps a record of the change.'
				: 'The mod files leave the game directory until you enable the mod again.'}
		</p>
		{#snippet actions()}
			<button
				class="btn-primary"
				type="button"
				onclick={() => {
					const pending = confirmation;
					confirmation = null;
					if (pending !== null) {
						void undo(pending.kind, pending.name);
					}
				}}
			>
				{confirmation?.kind === 'remove' ? 'Remove' : 'Disable'}
			</button>
		{/snippet}
	</Dialog>
{/if}

{#if failure !== null}
	<Dialog title={`${failure.name} failed`} onclose={() => (failure = null)}>
		<p>{failure.message}</p>
		{#if failure.conflicts.length > 0}
			<ul class="findings">
				{#each failure.conflicts as conflict, index (index)}
					<li>
						<span
							class="badge {conflict.severity === 'error' ? 'badge-danger' : 'badge-warn'}"
						>
							{conflict.severity}
						</span>
						<span class="kind">{conflict.kind}</span>
						{conflict.message}
						{#if conflict.path}<code>{conflict.path}</code>{/if}
					</li>
				{/each}
			</ul>
		{/if}
		{#if failure.residues.length > 0}
			<ul class="findings">
				{#each failure.residues as residue, index (index)}
					<li>
						<span class="kind">{residue.profile}</span>
						{residue.category}: {residue.count}
						{residue.count === 1 ? 'reference' : 'references'}
						<code>{residue.ids.join(', ')}</code>
					</li>
				{/each}
			</ul>
		{/if}
		{#snippet actions()}
			{#if failure?.forceable}
				<button
					class="btn-primary"
					type="button"
					onclick={() => {
						const name = failure?.name;
						failure = null;
						if (name !== undefined) {
							void undo('remove', name, true);
						}
					}}
				>
					Remove anyway
				</button>
			{/if}
		{/snippet}
	</Dialog>
{/if}

<style>
	.entries {
		list-style: none;
		margin: 0;
		padding: 0;
	}

	.entry {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		padding: var(--space-2) var(--space-3);
	}

	.text {
		flex: 1;
		min-width: 0;
	}

	.subject {
		display: block;
		font-weight: var(--weight-medium);
	}

	.meta {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
		font-size: var(--text-sm);
	}

	.actions {
		display: flex;
		gap: var(--space-2);
	}

	.files {
		list-style: none;
		margin: 0 var(--space-3) var(--space-3) var(--space-6);
		padding: var(--space-2) var(--space-3);
		background: var(--color-bg);
		border-radius: var(--radius-md);
		font-family: var(--font-mono);
		font-size: var(--text-sm);
	}

	.findings {
		list-style: none;
		margin: 0;
		padding: 0;
	}

	.findings li {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
		padding: var(--space-1) 0;
	}

	.kind {
		color: var(--color-text-muted);
		font-size: var(--text-sm);
	}

	.empty {
		padding: var(--space-5) var(--space-3);
		color: var(--color-text-muted);
	}

	.result-count {
		margin: var(--space-3) 0 0;
	}
</style>
