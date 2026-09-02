<script lang="ts">
	import { untrack } from 'svelte';
	import Dialog from './Dialog.svelte';
	import {
		initGameDir,
		listDirectories,
		locateDirectory,
		type DirectoryListing,
		type InitResult,
		type LocatedDirectory
	} from '$lib/api/client';
	import { ApiError } from '$lib/api/transport';

	interface Props {
		initialPath: string;
		onready: (path: string) => void;
	}

	let { initialPath, onready }: Props = $props();

	const FINGERPRINT_LIMIT = 5;
	const SPT_VERSION_UNKNOWN = 'SPT_VERSION_UNKNOWN';

	let path = $state(untrack(() => initialPath));
	let busy = $state(false);
	let error = $state('');
	let candidates: string[] = $state([]);
	let adopted: string[] = $state([]);
	let picker: HTMLInputElement | undefined = $state();
	let locating = $state(false);
	let matches: LocatedDirectory[] = $state([]);
	let sptVersion = $state('');
	let needsVersion = $state(false);
	let listing: DirectoryListing | null = $state(null);

	/**
	 * Turn the browser's folder selection into the folder name plus a few of
	 * its first-level entry names. The FileList of a game folder holds tens of
	 * thousands of entries; only their `webkitRelativePath` metadata is read
	 * here, never their content, so nothing is loaded or uploaded.
	 */
	function fingerprintOf(files: FileList): { name: string; fingerprint: string[] } {
		let name = '';
		const entries: string[] = [];
		for (const file of files) {
			const segments = file.webkitRelativePath.split('/');
			if (segments.length < 2) {
				continue;
			}
			name = segments[0];
			if (!entries.includes(segments[1])) {
				entries.push(segments[1]);
			}
			if (entries.length >= FINGERPRINT_LIMIT) {
				break;
			}
		}
		return { name, fingerprint: entries };
	}

	async function onFolderPicked(input: HTMLInputElement): Promise<void> {
		const files = input.files;
		input.value = '';
		if (files === null || files.length === 0) {
			return;
		}
		const { name, fingerprint } = fingerprintOf(files);
		if (name === '') {
			return;
		}
		locating = true;
		error = '';
		try {
			const found = await locateDirectory(name, fingerprint);
			if (found.length === 1) {
				path = found[0].path;
			} else if (found.length > 1) {
				matches = found;
			} else {
				await openBrowser(null);
			}
		} catch (cause) {
			error = messageOf(cause);
		} finally {
			locating = false;
		}
	}

	async function openBrowser(start: string | null): Promise<void> {
		try {
			listing = await listDirectories(start);
		} catch (cause) {
			error = messageOf(cause);
		}
	}

	function chooseMatch(candidate: LocatedDirectory): void {
		path = candidate.path;
		matches = [];
	}

	function selectBrowsed(): void {
		if (listing !== null) {
			path = listing.path;
			listing = null;
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
		<p>Enter the folder your SPT installation lives in. The server validates it.</p>
		<form
			onsubmit={(event) => {
				event.preventDefault();
				void validate();
			}}
		>
			<!-- The browser hands over folder names only, never absolute paths, so
			     the picked folder is resolved server-side by name and fingerprint;
			     the manual browser below is the fallback when that finds nothing.
			     A Tauri build can replace both with `open({ directory: true })`. -->
			<input
				type="text"
				bind:value={path}
				placeholder="/home/you/SPT"
				aria-label="SPT installation folder"
				disabled={busy}
			/>
			<input
				class="hidden-picker"
				type="file"
				webkitdirectory
				tabindex="-1"
				aria-hidden="true"
				bind:this={picker}
				onchange={(event) => void onFolderPicked(event.currentTarget)}
			/>
			<button type="button" disabled={busy || locating} onclick={() => picker?.click()}>
				{locating ? 'Locating…' : 'Browse…'}
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
		<p class="hint">
			<button type="button" class="link" onclick={() => void openBrowser(null)}>
				browse manually
			</button>
			if the folder cannot be found automatically.
		</p>
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

{#if matches.length > 0}
	<Dialog title="Which folder did you pick?" onclose={() => (matches = [])}>
		<ul class="rows">
			{#each matches as candidate (candidate.path)}
				<li>
					<button type="button" onclick={() => chooseMatch(candidate)}>
						{candidate.path}
						{#if candidate.is_game_dir}<span class="marker">SPT</span>{/if}
					</button>
				</li>
			{/each}
		</ul>
	</Dialog>
{/if}

{#if listing !== null}
	{@const current = listing}
	<Dialog title="Choose the SPT folder" onclose={() => (listing = null)}>
		{#snippet actions()}
			<button type="button" onclick={selectBrowsed}>Select this folder</button>
		{/snippet}
		<p class="current">{current.path}</p>
		<ul class="rows">
			{#if current.parent !== null && current.parent !== undefined}
				{@const parent = current.parent}
				<li>
					<button type="button" onclick={() => void openBrowser(parent)}>..</button>
				</li>
			{/if}
			{#each current.entries as entry (entry.path)}
				<li>
					<button type="button" onclick={() => void openBrowser(entry.path)}>
						{entry.name}
						{#if entry.is_game_dir}<span class="marker">SPT</span>{/if}
					</button>
				</li>
			{/each}
		</ul>
	</Dialog>
{/if}

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

	.hidden-picker {
		display: none;
	}

	.hint {
		font-size: 0.85rem;
		opacity: 0.8;
	}

	.link {
		background: none;
		border: none;
		padding: 0;
		color: inherit;
		text-decoration: underline;
		cursor: pointer;
		font: inherit;
	}

	.current {
		margin: 0 0 0.6rem;
		font-family: monospace;
		word-break: break-all;
	}

	.rows {
		list-style: none;
		padding: 0;
		margin: 0;
		max-height: 45vh;
		overflow: auto;
	}

	.rows button {
		display: flex;
		gap: 0.5rem;
		width: 100%;
		text-align: left;
		background: none;
		border: none;
		color: inherit;
		font: inherit;
		padding: 0.25rem 0.3rem;
		cursor: pointer;
	}

	.rows button:hover {
		background: #2a2f36;
	}

	.marker {
		font-size: 0.75rem;
		border: 1px solid #3a4048;
		border-radius: 3px;
		padding: 0 0.3rem;
	}
</style>
