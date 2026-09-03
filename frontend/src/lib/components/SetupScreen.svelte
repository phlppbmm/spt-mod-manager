<script lang="ts">
	import { untrack } from 'svelte';
	import { open } from '@tauri-apps/plugin-dialog';
	import { initGameDir, type InitResult } from '$lib/api/client';
	import { ApiError } from '$lib/api/transport';

	interface Props {
		initialPath: string;
		onready: (path: string) => void;
	}

	let { initialPath, onready }: Props = $props();

	const SPT_VERSION_UNKNOWN = 'SPT_VERSION_UNKNOWN';

	let path = $state(untrack(() => initialPath));
	let busy = $state(false);
	let error = $state('');
	let candidates: string[] = $state([]);
	let adopted: string[] = $state([]);
	let picking = $state(false);
	let sptVersion = $state('');
	let needsVersion = $state(false);

	async function pickFolder(): Promise<void> {
		picking = true;
		error = '';
		try {
			const picked = await open({ directory: true, multiple: false });
			if (picked === null) {
				return;
			}
			path = picked;
			await validate();
		} catch (cause) {
			error = messageOf(cause);
		} finally {
			picking = false;
		}
	}

	async function validate(): Promise<void> {
		busy = true;
		error = '';
		try {
			const result: InitResult = await initGameDir(path.trim(), false, versionAnswer());
			needsVersion = false;
			if (result.pre_existing_mods.length === 0) {
				onready(result.game_dir);
				return;
			}
			candidates = result.pre_existing_mods;
			adopted = [...result.pre_existing_mods];
		} catch (cause) {
			if (cause instanceof ApiError && cause.code === 'ALREADY_INITIALIZED') {
				// Setup already ran for this directory — proceed straight in.
				onready(path.trim());
				return;
			} else if (cause instanceof ApiError && cause.code === SPT_VERSION_UNKNOWN) {
				needsVersion = true;
				error = 'The SPT version cannot be detected. Enter it and continue.';
			} else {
				error = messageOf(cause);
			}
		} finally {
			busy = false;
		}
	}

	async function confirmAdoption(): Promise<void> {
		busy = true;
		error = '';
		try {
			const result = await initGameDir(
				path.trim(),
				adopted.length > 0 ? adopted : false,
				versionAnswer()
			);
			onready(result.game_dir);
		} catch (cause) {
			error = messageOf(cause);
		} finally {
			busy = false;
		}
	}

	function toggle(name: string, checked: boolean): void {
		adopted = checked ? [...adopted, name] : adopted.filter((entry) => entry !== name);
	}

	function versionAnswer(): string | null {
		return sptVersion.trim() === '' ? null : sptVersion.trim();
	}

	function messageOf(cause: unknown): string {
		if (cause instanceof ApiError) {
			return cause.message;
		}
		return cause instanceof Error ? cause.message : String(cause);
	}
</script>

<section class="setup">
	<h1>Set up sptmod</h1>

	{#if candidates.length === 0}
		<p>Enter the folder your SPT installation lives in. sptmod validates it.</p>
		<form
			onsubmit={(event) => {
				event.preventDefault();
				void validate();
			}}
		>
			<input
				type="text"
				bind:value={path}
				placeholder="/home/you/SPT"
				aria-label="SPT installation folder"
				disabled={busy}
			/>
			<button type="button" disabled={busy || picking} onclick={() => void pickFolder()}>
				{picking ? 'Opening…' : 'Browse…'}
			</button>
			{#if needsVersion}
				<input
					type="text"
					bind:value={sptVersion}
					placeholder="4.1.2"
					aria-label="SPT version"
					disabled={busy}
				/>
			{/if}
			<button
				type="submit"
				disabled={busy || path.trim() === '' || (needsVersion && sptVersion.trim() === '')}
			>
				{busy ? 'Checking…' : 'Continue'}
			</button>
		</form>
	{:else}
		<p>
			These mods are already installed in the folder. Adopt them so sptmod can track, update and
			remove them.
		</p>
		<ul class="candidates">
			{#each candidates as name (name)}
				<li>
					<label>
						<input
							type="checkbox"
							checked={adopted.includes(name)}
							disabled={busy}
							onchange={(event) => toggle(name, event.currentTarget.checked)}
						/>
						{name}
					</label>
				</li>
			{/each}
		</ul>
		<div class="row">
			<button type="button" disabled={busy} onclick={() => void confirmAdoption()}>
				{busy ? 'Working…' : `Adopt ${adopted.length} of ${candidates.length} and finish`}
			</button>
			<button type="button" disabled={busy} onclick={() => (candidates = [])}>Back</button>
		</div>
	{/if}

	{#if error !== ''}
		<p class="error">{error}</p>
	{/if}
</section>

<style>
	.setup {
		max-width: 40rem;
		margin: 3rem auto;
		padding: 0 1rem;
	}

	form {
		display: flex;
		gap: 0.5rem;
	}

	input[type='text'] {
		flex: 1;
	}

	.candidates {
		list-style: none;
		padding: 0;
		margin: 0 0 1rem;
		max-height: 40vh;
		overflow: auto;
	}

	.candidates li {
		padding: 0.15rem 0;
	}

	.row {
		display: flex;
		gap: 0.5rem;
	}

	.error {
		color: #ff8f8f;
	}
</style>
