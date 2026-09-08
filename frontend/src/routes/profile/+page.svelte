<script lang="ts">
	import { onMount } from 'svelte';
	import {
		checkProfiles,
		cleanProfile,
		launchProfile,
		launchStatus,
		listProfiles,
		planProfileCleanup,
		snapshot,
		stopGame,
		type CheckResult,
		type CleanupPlan,
		type LaunchStatus,
		type ProfileSummary
	} from '$lib/api/client';
	import { subscribe, type ProgressEvent } from '$lib/api/transport';
	import { launchAction, loadStopServerOnExit } from '$lib/launch';
	import Dialog from '$lib/components/Dialog.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import {
		categoryLabel,
		characterLabel,
		formatProfileList,
		groupResiduesByProfile,
		groupRemovalsByProfile,
		profileLabel
	} from '$lib/cleanup';
	import { loadGameDir } from '$lib/gamedir';
	import { onIntents, takeIntents } from '$lib/intents';
	import { cached, invalidate } from '$lib/viewcache';
	import { onSubNavSelect, setSearchEnabled, setSubNav } from '$lib/shellstate';

	let path: string | null = $state(null);
	let summaries: ProfileSummary[] = $state([]);
	let result: CheckResult | null = $state(null);
	let loading = $state(true);
	let error = $state('');
	let message = $state('');
	let snapshotNote = $state('');

	let saving = $state(false);
	let gameStatus: LaunchStatus | null = $state(null);
	let launching = $state('');
	let launchProgress: ProgressEvent | null = $state(null);
	let launchNote = $state('');
	let stopTarget = $state('');
	let stopping = $state(false);
	let plan: CleanupPlan | null = $state(null);
	let planProfile = $state('');
	let planning = $state('');
	let cleaning = $state(false);

	let activeProfile = $state('');

	let groups = $derived.by(() => groupResiduesByProfile(summaries, result?.residues ?? {}));
	let subNavActive = $derived(
		groups.some((group) => group.profile.profile_id === activeProfile)
			? activeProfile
			: (groups[0]?.profile.profile_id ?? null)
	);
	let planGroups = $derived.by(() =>
		groupRemovalsByProfile(
			(plan?.removals ?? []).filter((residue) => residue.profile === planProfile)
		)
	);

	onMount(() => {
		setSubNav([], null);
		setSearchEnabled(false);
		const unsubscribe = onSubNavSelect(showProfile);
		const stored = loadGameDir();
		path = stored;
		if (stored === null) {
			loading = false;
		} else {
			void check(stored);
			void refreshGameStatus(stored);
		}
		runIntents();
		const unsubscribeIntents = onIntents(runIntents);
		return () => {
			unsubscribe();
			unsubscribeIntents();
			setSubNav([], null);
		};
	});

	function runIntents(): void {
		for (const intent of takeIntents(['snapshot', 'launch'])) {
			if (intent.kind === 'snapshot') {
				void takeSnapshot();
			} else {
				void play(intent.profileId);
			}
		}
	}

	$effect(() => {
		setSubNav(
			groups.map((group) => ({
				id: group.profile.profile_id,
				label: profileLabel(group.profile)
			})),
			subNavActive
		);
	});

	function showProfile(profileId: string): void {
		activeProfile = profileId;
		document
			.getElementById(`profile-${profileId}`)
			?.scrollIntoView({ behavior: 'smooth', block: 'start' });
	}

	async function refreshGameStatus(gameDir: string): Promise<void> {
		try {
			gameStatus = await launchStatus(gameDir);
		} catch {
			gameStatus = null;
		}
	}

	async function play(profileId: string): Promise<void> {
		if (path === null) {
			return;
		}
		launching = profileId;
		launchNote = '';
		launchProgress = null;
		const operationId = crypto.randomUUID();
		const unsubscribe = await subscribe(operationId, (event) => {
			launchProgress = event;
		});
		try {
			const started = await launchProfile(
				path,
				profileId,
				loadStopServerOnExit(),
				operationId
			);
			launchNote = `The game is starting for ${started.nickname}.`;
		} catch (cause) {
			launchNote = cause instanceof Error ? cause.message : String(cause);
		} finally {
			unsubscribe();
			launchProgress = null;
			launching = '';
			invalidate(path);
			await refreshGameStatus(path);
		}
	}

	async function stop(): Promise<void> {
		if (path === null) {
			return;
		}
		stopping = true;
		launchNote = '';
		try {
			await stopGame(path);
			launchNote = 'The game was closed.';
		} catch (cause) {
			launchNote = cause instanceof Error ? cause.message : String(cause);
		} finally {
			stopping = false;
			stopTarget = '';
			invalidate(path);
			await refreshGameStatus(path);
		}
	}

	async function check(gameDir: string): Promise<void> {
		const listView = cached(gameDir, 'list_profiles', [], () => listProfiles(gameDir));
		const checkView = cached(gameDir, 'check_profiles', [], () => checkProfiles(gameDir));
		const listHit = listView.value;
		const checkHit = checkView.value;
		if (listHit !== undefined && checkHit !== undefined) {
			summaries = listHit;
			result = checkHit;
			loading = false;
		} else {
			loading = true;
		}
		error = '';
		try {
			const [listed, checked] = await Promise.all([listView.refresh(), checkView.refresh()]);
			summaries = listed;
			result = checked;
		} catch (cause) {
			error = cause instanceof Error ? cause.message : String(cause);
		} finally {
			loading = false;
		}
	}

	function lastChanged(modified: string): string {
		return new Date(modified).toLocaleString();
	}

	function busyKey(profile: string, mod: string): string {
		return `${profile}::${mod}`;
	}

	async function takeSnapshot(): Promise<void> {
		if (path === null) {
			return;
		}
		saving = true;
		snapshotNote = '';
		try {
			const taken = await snapshot(path, message.trim() === '' ? null : message.trim());
			snapshotNote = `Snapshot "${taken.message}" (${taken.files.length} files) as ${taken.commit.slice(0, 8)}.`;
			message = '';
			invalidate(path);
			await check(path);
		} catch (cause) {
			snapshotNote = cause instanceof Error ? cause.message : String(cause);
		} finally {
			saving = false;
		}
	}

	async function preview(profile: string, mod: string): Promise<void> {
		if (path === null) {
			return;
		}
		planning = busyKey(profile, mod);
		error = '';
		try {
			plan = await planProfileCleanup(path, mod);
			planProfile = profile;
		} catch (cause) {
			error = cause instanceof Error ? cause.message : String(cause);
		} finally {
			planning = '';
		}
	}

	async function clean(): Promise<void> {
		if (path === null || plan === null) {
			return;
		}
		const pending = plan;
		const profile = planProfile;
		cleaning = true;
		error = '';
		snapshotNote = '';
		try {
			const cleaned = await cleanProfile(path, pending.mod, [profile]);
			plan = null;
			snapshotNote =
				cleaned.snapshot === '' ? 'Profiles cleaned.' : 'Profiles cleaned. Snapshot taken.';
			invalidate(path);
			await check(path);
		} catch (cause) {
			plan = null;
			error = cause instanceof Error ? cause.message : String(cause);
		} finally {
			cleaning = false;
		}
	}
</script>

<section class="page">
	<PageHeader title="Profiles" secondary={path === null ? undefined : snapshotAction} />

	{#if path === null}
		<p class="page-empty">No game directory yet. <a href="/">Run the setup</a> first.</p>
	{:else}
		{#if snapshotNote !== ''}
			<p class="note">{snapshotNote}</p>
		{/if}

		{#if error !== ''}
			<p class="error-text">{error}</p>
		{/if}

		{#each result?.warnings ?? [] as warning, index (index)}
			<p class="warn-text">{warning}</p>
		{/each}

		{#if launchNote !== ''}
			<p class="note">{launchNote}</p>
		{/if}

		{#each groups as group (group.profile.file)}
			{@const action = launchAction(gameStatus, group.profile.profile_id, launching !== '')}
			<article
				class="panel profile"
				class:is-active={group.profile.profile_id === subNavActive}
				id="profile-{group.profile.profile_id}"
			>
				<div class="header">
					<div class="identity">
						<h2>{profileLabel(group.profile)}</h2>
						<p class="meta">
							{#if group.profile.level > 0}<span>Level {group.profile.level}</span>{/if}
							{#if group.profile.side !== ''}<span>{group.profile.side}</span>{/if}
							<span>{group.profile.file}</span>
							{#if group.profile.modified !== ''}
								<span>Last changed {lastChanged(group.profile.modified)}</span>
							{/if}
						</p>
						{#if !group.profile.readable}
							<p class="error-text">{group.profile.error}</p>
						{/if}
					</div>

					{#if group.profile.readable}
						<div class="launch">
							<button
								type="button"
								class={action.mode === 'stop' ? '' : 'btn-primary'}
								disabled={action.disabled}
								onclick={() =>
									action.mode === 'stop'
										? (stopTarget = group.profile.profile_id)
										: void play(group.profile.profile_id)}
							>
								{action.label}
							</button>
							{#if launching === group.profile.profile_id && launchProgress !== null}
								<span class="note">{launchProgress.message}</span>
							{:else if action.hint !== ''}
								<span class="note">{action.hint}</span>
							{/if}
						</div>
					{/if}
				</div>

				{#if group.profile.readable}
					<div class="body">
						{#if group.mods.length === 0}
							<p class="note">No traces left by mods.</p>
						{:else}
							{#each group.mods as entry (entry.mod)}
								<div class="mod">
									<h3>{entry.mod}</h3>
									<button
										type="button"
										class="btn-sm"
										disabled={planning !== '' || cleaning}
										onclick={() => void preview(group.profile.profile_id, entry.mod)}
									>
										{planning === busyKey(group.profile.profile_id, entry.mod)
											? 'Loading…'
											: 'Preview cleanup'}
									</button>
								</div>
								<ul class="residues">
									{#each entry.residues as residue, index (index)}
										<li class="panel-row">
											<span class="badge badge-warn">{categoryLabel(residue.category)}</span>
											<span class="count">{characterLabel(residue.character)}</span>
											<span class="count">
												{residue.count}
												{residue.count === 1 ? 'reference' : 'references'}
											</span>
											<code>{residue.ids.join(', ')}</code>
										</li>
									{/each}
								</ul>
							{/each}
						{/if}
					</div>
				{/if}
			</article>
		{/each}

		{#if groups.length === 0}
			<p class="note">
				{loading ? 'Loading profiles…' : 'No player profiles found in this installation.'}
			</p>
		{/if}
	{/if}
</section>

{#snippet snapshotAction()}
	<input
		class="head-input"
		type="text"
		placeholder="Snapshot message (optional)"
		aria-label="Snapshot message"
		bind:value={message}
	/>
	<button type="button" disabled={saving} onclick={() => void takeSnapshot()}>
		{saving ? 'Saving…' : 'Snapshot'}
	</button>
{/snippet}

{#if stopTarget !== ''}
	<Dialog title="Close the game" onclose={() => (stopTarget = '')}>
		<p>This closes the game immediately. Progress since the last save is lost.</p>
		<p class="note">The SPT server is closed with it.</p>

		{#snippet actions()}
			<button type="button" class="btn-primary" disabled={stopping} onclick={() => void stop()}>
				{stopping ? 'Closing…' : 'Close the game'}
			</button>
		{/snippet}
	</Dialog>
{/if}

{#if plan !== null}
	<Dialog title={`Clean ${planProfile}.json for ${plan.mod}`} onclose={() => (plan = null)}>
		{#if planGroups.length === 0}
			<p>This profile holds nothing of this mod that sptmod can clean.</p>
		{:else}
			{#each planGroups as group (group.profile)}
				<h3>{group.file}</h3>
				<ul class="residues">
					{#each group.residues as residue, index (index)}
						<li class="panel-row">
							<span class="badge badge-warn">{categoryLabel(residue.category)}</span>
							<span class="count">{characterLabel(residue.character)}</span>
							<span class="count">
								{residue.count}
								{residue.count === 1 ? 'entry' : 'entries'}
							</span>
						</li>
					{/each}
				</ul>
			{/each}
		{/if}

		{#if plan.untouched.length > 0}
			<h3>Stays untouched</h3>
			<ul class="residues">
				{#each plan.untouched as entry, index (index)}
					<li class="panel-row">
						<span class="badge badge-warn">{categoryLabel(entry.residue.category)}</span>
						<span class="count">
							{entry.residue.count}
							{entry.residue.count === 1 ? 'entry' : 'entries'}
						</span>
						{entry.reason}
					</li>
				{/each}
			</ul>
		{/if}

		{#each plan.warnings as warning, index (index)}
			<p class="warn-text">{warning}</p>
		{/each}

		<p class="note">
			Quest and achievement progress that belongs to this mod is removed along with it.
		</p>
		{#if planGroups.length > 0}
			<p class="note">
				This changes {formatProfileList([planProfile])}. A copy of the file is saved in the game's
				own backups folder first, and the change shows up in History as a snapshot you can look at.
			</p>
		{/if}

		{#snippet actions()}
			<button
				type="button"
				class="btn-primary"
				disabled={cleaning || planGroups.length === 0}
				onclick={() => void clean()}
			>
				{cleaning ? 'Cleaning…' : 'Clean'}
			</button>
		{/snippet}
	</Dialog>
{/if}

<style>
	.page {
		padding: var(--page-gutter, 32px);
	}

	.head-input {
		width: 20rem;
	}

	.profile {
		margin: var(--space-4) 0;
	}

	.profile.is-active {
		border-color: var(--color-accent-border);
	}

	.header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-4);
		padding: var(--space-4);
		border-bottom: 1px solid var(--color-border);
	}

	.header:last-child {
		border-bottom: none;
	}

	.identity {
		min-width: 0;
	}

	.meta {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-3);
		margin: var(--space-1) 0 0;
		color: var(--color-text-muted);
		font-size: var(--text-sm);
	}

	.identity .error-text {
		margin: var(--space-1) 0 0;
	}

	.launch {
		display: flex;
		flex-direction: column;
		align-items: flex-end;
		gap: var(--space-1);
		flex-shrink: 0;
		text-align: right;
	}

	.body {
		padding: var(--space-4);
	}

	.body > :global(p) {
		margin: 0;
	}

	.mod {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		margin: var(--space-4) 0 var(--space-2);
	}

	.mod:first-child {
		margin-top: 0;
	}

	.residues {
		list-style: none;
		margin: 0;
		padding: 0;
	}

	.residues li {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
		padding: var(--space-2) 0;
	}

	.count {
		color: var(--color-text-muted);
		font-size: var(--text-sm);
	}
</style>
