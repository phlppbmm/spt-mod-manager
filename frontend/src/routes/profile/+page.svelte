<script lang="ts">
	import { onMount } from 'svelte';
	import {
		checkProfiles,
		cleanProfile,
		planProfileCleanup,
		snapshot,
		type CheckResult,
		type CleanupPlan
	} from '$lib/api/client';
	import Dialog from '$lib/components/Dialog.svelte';
	import {
		categoryLabel,
		characterLabel,
		formatProfileList,
		groupRemovalsByProfile
	} from '$lib/cleanup';
	import { loadGameDir } from '$lib/gamedir';

	let path: string | null = $state(null);
	let result: CheckResult | null = $state(null);
	let loading = $state(true);
	let error = $state('');
	let message = $state('');
	let snapshotNote = $state('');

	let saving = $state(false);
	let plan: CleanupPlan | null = $state(null);
	let planning = $state('');
	let cleaning = $state(false);

	let mods = $derived.by(() => Object.entries(result?.residues ?? {}));
	let planGroups = $derived.by(() => groupRemovalsByProfile(plan?.removals ?? []));

	onMount(() => {
		const stored = loadGameDir();
		path = stored;
		if (stored === null) {
			loading = false;
			return;
		}
		void check(stored);
	});

	async function check(gameDir: string): Promise<void> {
		loading = true;
		error = '';
		try {
			result = await checkProfiles(gameDir);
		} catch (cause) {
			error = cause instanceof Error ? cause.message : String(cause);
		} finally {
			loading = false;
		}
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
			await check(path);
		} catch (cause) {
			snapshotNote = cause instanceof Error ? cause.message : String(cause);
		} finally {
			saving = false;
		}
	}

	async function preview(mod: string): Promise<void> {
		if (path === null) {
			return;
		}
		planning = mod;
		error = '';
		try {
			plan = await planProfileCleanup(path, mod);
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
		cleaning = true;
		error = '';
		snapshotNote = '';
		try {
			const cleaned = await cleanProfile(path, pending.mod, pending.profiles);
			plan = null;
			snapshotNote =
				cleaned.snapshot === '' ? 'Profiles cleaned.' : 'Profiles cleaned. Snapshot taken.';
			await check(path);
		} catch (cause) {
			plan = null;
			error = cause instanceof Error ? cause.message : String(cause);
		} finally {
			cleaning = false;
		}
	}
</script>

<section class="profiles">
	<h1>Profiles</h1>

	{#if path === null}
		<p class="notice">No game directory yet. <a href="/">Run the setup</a> first.</p>
	{:else}
		<div class="snapshot">
			<input
				type="text"
				placeholder="Snapshot message (optional)"
				aria-label="Snapshot message"
				bind:value={message}
			/>
			<button type="button" disabled={saving} onclick={() => void takeSnapshot()}>
				{saving ? 'Saving…' : 'Snapshot profiles and configs'}
			</button>
		</div>
		{#if snapshotNote !== ''}
			<p class="note">{snapshotNote}</p>
		{/if}

		{#if error !== ''}
			<p class="error">{error}</p>
		{/if}

		{#each result?.warnings ?? [] as warning, index (index)}
			<p class="warning">{warning}</p>
		{/each}

		{#each mods as [mod, residues] (mod)}
			<div class="mod">
				<h2>{mod}</h2>
				<button
					type="button"
					disabled={planning !== '' || cleaning}
					onclick={() => void preview(mod)}
				>
					{planning === mod ? 'Loading…' : 'Preview cleanup'}
				</button>
			</div>
			<ul class="residues">
				{#each residues as residue, index (index)}
					<li>
						<span class="category">{residue.category}</span>
						<span class="count">
							{residue.count}
							{residue.count === 1 ? 'reference' : 'references'}
						</span>
						<code>{residue.ids.join(', ')}</code>
					</li>
				{/each}
			</ul>
		{/each}

		{#if mods.length === 0}
			<p class="note">
				{loading ? 'Checking profiles…' : 'No profile references mod content of removed mods.'}
			</p>
		{/if}
	{/if}
</section>

{#if plan !== null}
	<Dialog title={`Clean profiles for ${plan.mod}`} onclose={() => (plan = null)}>
		{#if planGroups.length === 0}
			<p>The player profiles hold nothing of this mod that sptmod can clean.</p>
		{:else}
			{#each planGroups as group (group.profile)}
				<h3>{group.file}</h3>
				<ul class="residues">
					{#each group.residues as residue, index (index)}
						<li>
							<span class="category">{categoryLabel(residue.category)}</span>
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
					<li>
						<span class="category">{categoryLabel(entry.residue.category)}</span>
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
			<p class="warning">{warning}</p>
		{/each}

		<p class="note">
			Quest and achievement progress that belongs to this mod is removed along with it.
		</p>
		{#if planGroups.length > 0}
			<p class="note">
				This changes {formatProfileList(plan.profiles)}. A copy of each file is saved in the game's
				own backups folder first, and the change shows up in History as a snapshot you can look at.
			</p>
		{/if}

		{#snippet actions()}
			<button
				type="button"
				disabled={cleaning || planGroups.length === 0}
				onclick={() => void clean()}
			>
				{cleaning ? 'Cleaning…' : 'Clean'}
			</button>
		{/snippet}
	</Dialog>
{/if}

<style>
	.profiles {
		padding: 1rem;
	}

	h1 {
		font-size: 1.1rem;
		margin: 0 0 0.8rem;
	}

	h2 {
		font-size: 1rem;
		margin: 0;
	}

	h3 {
		font-size: 0.9rem;
		margin: 0.8rem 0 0.3rem;
	}

	.mod {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		margin: 1rem 0 0.4rem;
	}

	.snapshot {
		display: flex;
		gap: 0.5rem;
		align-items: center;
	}

	.snapshot input {
		flex: 1;
		max-width: 24rem;
	}

	.residues {
		list-style: none;
		margin: 0;
		padding: 0;
		border: 1px solid #2c3138;
		border-radius: 4px;
	}

	.residues li {
		padding: 0.35rem 0.6rem;
		border-bottom: 1px solid #2c3138;
	}

	.category {
		display: inline-block;
		min-width: 5rem;
		color: #f0cf7d;
	}

	.count {
		color: #98a2ad;
		margin-right: 0.5rem;
	}

	.note,
	.notice {
		color: #98a2ad;
	}

	.warning {
		color: #f0cf7d;
	}

	.error {
		color: #ff8f8f;
	}
</style>
