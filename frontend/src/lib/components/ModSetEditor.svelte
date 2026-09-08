<script lang="ts">
	import {
		bindProfileSet,
		planModSet,
		saveModSet,
		type ModInfo,
		type ModSet,
		type ModSetEntry,
		type ModSetOverview
	} from '$lib/api/client';
	import {
		boundSetName,
		entriesFromMods,
		entryRows,
		planSummary,
		setOptions,
		toggleEntry,
		type EntryStatus
	} from '$lib/modsets';

	interface Props {
		path: string;
		profileId: string;
		sets: ModSetOverview;
		mods: ModInfo[];
		onchange: () => void;
	}

	let { path, profileId, sets, mods, onchange }: Props = $props();

	const STATUS_LABELS: Record<EntryStatus, string> = {
		on: 'On',
		off: 'Off',
		missing: 'Not installed',
		version: 'Other version'
	};

	const STATUS_CLASSES: Record<EntryStatus, string> = {
		on: 'badge-success',
		off: 'badge-neutral',
		missing: 'badge-danger',
		version: 'badge-warn'
	};

	let choice: string | null = $state(null);
	let draft: ModSetEntry[] | null = $state(null);
	let summary = $state('');
	let note = $state('');
	let busy = $state(false);

	let options = $derived(setOptions(sets, profileId));
	let current = $derived(choice ?? boundSetName(sets, profileId) ?? '');
	let selected = $derived(
		sets.sets.find((set) => set.name.toLowerCase() === current.toLowerCase()) ?? null
	);
	let entries = $derived(draft ?? selected?.entries ?? []);
	let shown = $derived<ModSet | null>(
		selected === null ? null : { ...selected, entries }
	);
	let rows = $derived(entryRows(shown, mods));

	$effect(() => {
		void loadSummary(current);
	});

	async function loadSummary(name: string): Promise<void> {
		if (name === '') {
			summary = '';
			return;
		}
		try {
			summary = planSummary(await planModSet(path, name));
		} catch {
			summary = '';
		}
	}

	async function bind(name: string): Promise<void> {
		choice = name;
		draft = null;
		busy = true;
		note = '';
		try {
			await bindProfileSet(path, profileId, name === '' ? null : name);
			onchange();
		} catch (cause) {
			note = cause instanceof Error ? cause.message : String(cause);
		} finally {
			busy = false;
		}
	}

	async function store(next: ModSetEntry[]): Promise<void> {
		busy = true;
		note = '';
		try {
			await saveModSet(path, current, next);
			draft = null;
			note = 'The set was saved.';
			onchange();
			await loadSummary(current);
		} catch (cause) {
			note = cause instanceof Error ? cause.message : String(cause);
		} finally {
			busy = false;
		}
	}

	function takeInstalled(): void {
		draft = entriesFromMods(mods);
		void store(draft);
	}
</script>

<div class="editor">
	<div class="row">
		<label>
			Mods for this profile
			<select
				value={current}
				disabled={busy}
				onchange={(event) => void bind(event.currentTarget.value)}
			>
				{#each options as option (option.value)}
					<option value={option.value}>{option.label}</option>
				{/each}
			</select>
		</label>
		{#if summary !== ''}
			<span class="note">Starting this profile: {summary}</span>
		{/if}
	</div>

	{#if selected === null}
		<p class="note">
			This profile is played with whatever mods are turned on at the time.
		</p>
	{:else}
		<ul class="entries">
			{#each rows as row (row.entry.name)}
				<li class="panel-row">
					<label>
						<input
							type="checkbox"
							checked={row.entry.enabled}
							disabled={busy}
							onchange={() => (draft = toggleEntry(entries, row.entry.name))}
						/>
						{row.entry.name}
					</label>
					<span class="version">
						{row.entry.version === '' ? 'any version' : row.entry.version}
						{#if row.status === 'version'}
							· installed {row.installedVersion}
						{/if}
					</span>
					<span class="badge {STATUS_CLASSES[row.status]}">{STATUS_LABELS[row.status]}</span>
				</li>
			{/each}
			{#if rows.length === 0}
				<li class="panel-row note">This set holds no mods yet.</li>
			{/if}
		</ul>
		<div class="row">
			<button type="button" class="btn-sm btn-primary" disabled={busy} onclick={() => void store(entries)}>
				Save
			</button>
			<button type="button" class="btn-sm" disabled={busy} onclick={takeInstalled}>
				Save the installed mods as this set
			</button>
		</div>
	{/if}

	{#if note !== ''}
		<p class="note">{note}</p>
	{/if}
</div>

<style>
	.editor {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	.row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-3);
	}

	label {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	.entries {
		list-style: none;
		margin: 0;
		padding: 0;
	}

	.entries li {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		padding: var(--space-2) 0;
	}

	.entries label {
		flex: 1;
		min-width: 0;
	}

	.version {
		color: var(--color-text-muted);
		font-size: var(--text-sm);
	}
</style>
