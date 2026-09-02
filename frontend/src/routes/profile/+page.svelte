<script lang="ts">
	import { onMount } from 'svelte';
	import { checkProfiles, snapshot, type CheckResult } from '$lib/api/client';
	import { loadGameDir } from '$lib/gamedir';

	let path: string | null = $state(null);
	let result: CheckResult | null = $state(null);
	let loading = $state(true);
	let error = $state('');
	let message = $state('');
	let snapshotNote = $state('');
	let saving = $state(false);

	let profiles = $derived.by(() => Object.entries(result?.residues ?? {}));

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

		{#each profiles as [profile, residues] (profile)}
			<h2>{profile}</h2>
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

		{#if profiles.length === 0}
			<p class="note">
				{loading ? 'Checking profiles…' : 'No profile references mod content of removed mods.'}
			</p>
		{/if}
	{/if}
</section>

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
