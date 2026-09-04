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
		const parts = [
			commit.trailers['Mod-Version'],
			commit.trailers['Mod-Type'],
			commit.trailers['Mod-State']
		].filter((part) => part !== undefined && part !== '');
		return [name, ...parts].join(' · ');
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

<section class="history">
	<h1>History</h1>

	{#if path === null}
		<p class="notice">No game directory yet. <a href="/">Run the setup</a> first.</p>
	{:else}
		{#if error !== ''}
			<p class="error">{error}</p>
		{/if}

		<ul class="commits">
			{#each commits as commit (commit.hash)}
				<li>
					<div class="entry">
						<div class="text">
							<strong>{commit.subject}</strong>
							<span class="meta">
								<code>{commit.hash.slice(0, 8)}</code>
								{new Date(commit.date).toLocaleString()}
								{#if summary(commit) !== ''}
									· {summary(commit)}
								{/if}
							</span>
						</div>
						<div class="actions">
							{#if modName(commit) !== ''}
								<button type="button" onclick={() => void toggleFiles(commit)}>
									{expanded === commit.hash ? 'Hide files' : 'Files'}
								</button>
							{/if}
							{#if undoable(commit)}
								<button
									type="button"
									disabled={busy}
									onclick={() =>
										(confirmation = { kind: 'disable', name: modName(commit) })}
								>
									Disable
								</button>
								<button
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
								<li class="error">{filesError}</li>
							{/if}
							{#each files as file (file.path)}
								<li><code>{file.change}</code> {file.path}</li>
							{/each}
						</ul>
					{/if}
				</li>
			{/each}
			{#if commits.length === 0}
				<li class="empty">{loading ? 'Loading…' : 'No commits yet.'}</li>
			{/if}
		</ul>
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
				? 'The mod files are reverted; the history keeps them.'
				: 'The mod files leave the game directory until you enable the mod again.'}
		</p>
		{#snippet actions()}
			<button
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
						<span class="severity {conflict.severity}">{conflict.severity}</span>
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
	.history {
		padding: 1rem;
	}

	h1 {
		font-size: 1.1rem;
		margin: 0 0 0.8rem;
	}

	.commits {
		list-style: none;
		margin: 0;
		padding: 0;
		border: 1px solid #2c3138;
		border-radius: 4px;
	}

	.commits > li {
		border-bottom: 1px solid #2c3138;
	}

	.entry {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		padding: 0.45rem 0.6rem;
	}

	.text {
		flex: 1;
		min-width: 0;
	}

	.text strong {
		display: block;
	}

	.meta {
		color: #98a2ad;
		font-size: 0.85em;
	}

	.actions {
		display: flex;
		gap: 0.4rem;
	}

	.files {
		list-style: none;
		margin: 0;
		padding: 0 0.6rem 0.6rem 1.6rem;
		font-size: 0.9em;
	}

	.findings {
		list-style: none;
		margin: 0;
		padding: 0;
	}

	.findings li {
		padding: 0.15rem 0;
	}

	.severity {
		font-size: 0.8em;
		text-transform: uppercase;
		margin-right: 0.4rem;
	}

	.severity.error {
		color: #f2a19c;
	}

	.severity.warning {
		color: #f0cf7d;
	}

	.kind {
		color: #98a2ad;
		margin-right: 0.4rem;
	}

	.empty,
	.notice {
		padding: 1rem;
		color: #98a2ad;
	}

	.error {
		color: #ff8f8f;
	}
</style>
