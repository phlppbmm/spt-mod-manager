<script lang="ts">
	import { untrack } from 'svelte';
	import { initGameDir, type InitResult } from '$lib/api/client';
	import { ApiError } from '$lib/api/transport';

	interface Props {
		initialPath: string;
		onready: (path: string) => void;
	}

	let { initialPath, onready }: Props = $props();

	let path = $state(untrack(() => initialPath));
	let busy = $state(false);
	let error = $state('');
	let candidates: string[] = $state([]);
	let adopted: string[] = $state([]);

	async function validate(): Promise<void> {
		busy = true;
		error = '';
		try {
			const result: InitResult = await initGameDir(path.trim());
			if (result.pre_existing_mods.length === 0) {
				onready(result.game_dir);
				return;
			}
			candidates = result.pre_existing_mods;
			adopted = [...result.pre_existing_mods];
		} catch (cause) {
			error = messageOf(cause);
		} finally {
			busy = false;
		}
	}

	async function confirmAdoption(): Promise<void> {
		busy = true;
		error = '';
		try {
			const result = await initGameDir(path.trim(), adopted.length > 0 ? adopted : false);
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
		<p>Enter the folder your SPT installation lives in. The server validates it.</p>
		<form
			onsubmit={(event) => {
				event.preventDefault();
				void validate();
			}}
		>
			<!-- Tauri seam: replace this text input with the native folder picker
			     (@tauri-apps/plugin-dialog `open({ directory: true })`) and keep the
			     server-side validation below unchanged. -->
			<input
				type="text"
				bind:value={path}
				placeholder="/home/you/SPT"
				aria-label="SPT installation folder"
				disabled={busy}
			/>
			<button type="submit" disabled={busy || path.trim() === ''}>
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
