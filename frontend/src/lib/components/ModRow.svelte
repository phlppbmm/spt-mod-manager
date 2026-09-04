<script lang="ts">
	import Dialog from './Dialog.svelte';
	import {
		disableMod,
		enableMod,
		getModDetails,
		installMod,
		modFiles,
		planModInstall,
		removeMod,
		setFavorite,
		updateMod,
		type CatalogEntry,
		type Conflict,
		type InstallPlan,
		type ModDetails,
		type ModFile,
		type ModInfo,
		type ProfileResidue
	} from '$lib/api/client';
	import { ApiError, subscribe, type ProgressEvent } from '$lib/api/transport';
	import type { RowState } from '$lib/modfilter';

	interface Props {
		path: string;
		index: number;
		entry: CatalogEntry;
		modState: RowState;
		installed: ModInfo | null;
		locked: boolean;
		favorite: boolean;
		onchanged: () => void;
		onfavorite: (modIds: number[]) => void;
		onbusy: (modId: number, busy: boolean) => void;
	}

	let {
		path,
		index,
		entry,
		modState,
		installed,
		locked,
		favorite,
		onchanged,
		onfavorite,
		onbusy
	}: Props = $props();

	const FORCEABLE_CODES = [
		'CONFLICT',
		'DEPENDENCY',
		'UNRESOLVABLE_DEPENDENCY',
		'PROFILE_RESIDUE'
	];

	const STATE_LABELS: Record<RowState, string> = {
		active: 'active',
		disabled: 'disabled',
		update_available: 'update available',
		incompatible: 'incompatible',
		available: 'not installed'
	};

	let busy = $state(false);
	let progress: ProgressEvent | null = $state(null);
	let plan: InstallPlan | null = $state(null);
	let planning = $state(false);
	let confirmation: 'disable' | 'remove' | null = $state(null);
	let files: ModFile[] | null = $state(null);
	let errorMessage = $state('');
	let errorConflicts: Conflict[] = $state([]);
	let errorResidues: ProfileResidue[] = $state([]);
	let errorInstalled: string[] = $state([]);
	let retryLabel = $state('');
	let retry: (() => void) | null = $state(null);

	async function perform(
		action: (force: boolean) => Promise<unknown>,
		force: boolean,
		forceLabel: string | null
	): Promise<void> {
		busy = true;
		onbusy(entry.mod.id, true);
		errorMessage = '';
		errorConflicts = [];
		errorResidues = [];
		errorInstalled = [];
		retry = null;
		try {
			await action(force);
			onchanged();
		} catch (cause) {
			if (cause instanceof ApiError) {
				errorMessage = cause.message;
				errorConflicts = cause.details?.conflicts ?? [];
				errorResidues = cause.details?.residues ?? [];
				errorInstalled = cause.details?.installed ?? [];
				if (!force && forceLabel !== null && FORCEABLE_CODES.includes(cause.code)) {
					retryLabel = forceLabel;
					retry = () => void perform(action, true, forceLabel);
				}
			} else {
				errorMessage = cause instanceof Error ? cause.message : String(cause);
			}
		} finally {
			busy = false;
			onbusy(entry.mod.id, false);
			progress = null;
		}
	}

	async function openPlan(): Promise<void> {
		planning = true;
		onbusy(entry.mod.id, true);
		errorMessage = '';
		try {
			plan = await planModInstall(path, entry.mod.id);
		} catch (cause) {
			errorMessage = cause instanceof Error ? cause.message : String(cause);
		} finally {
			planning = false;
			onbusy(entry.mod.id, false);
		}
	}

	async function withProgress(action: (operationId: string) => Promise<unknown>): Promise<void> {
		const operationId = crypto.randomUUID();
		const unsubscribe = await subscribe(operationId, (event) => {
			progress = event;
		});
		try {
			await action(operationId);
		} finally {
			unsubscribe();
		}
	}

	function install(force: boolean): Promise<void> {
		return withProgress((operationId) =>
			installMod(path, entry.mod.id, null, force, operationId)
		);
	}

	function update(force: boolean): Promise<void> {
		return withProgress((operationId) =>
			updateMod(path, installed?.name ?? entry.mod.name, force, operationId)
		);
	}

	function installedName(): string {
		return installed?.name ?? entry.mod.name;
	}

	let markingFavorite = $state(false);

	async function toggleFavorite(): Promise<void> {
		markingFavorite = true;
		errorMessage = '';
		try {
			const response = await setFavorite(entry.mod.id, !favorite);
			onfavorite(response.mod_ids);
		} catch (cause) {
			errorMessage = cause instanceof Error ? cause.message : String(cause);
		} finally {
			markingFavorite = false;
		}
	}

	let details: ModDetails | null = $state(null);

	async function showDetails(): Promise<void> {
		busy = true;
		errorMessage = '';
		try {
			details = await getModDetails(entry.mod.id);
		} catch (cause) {
			errorMessage = cause instanceof Error ? cause.message : String(cause);
		} finally {
			busy = false;
		}
	}

	async function showFiles(): Promise<void> {
		busy = true;
		errorMessage = '';
		try {
			files = await modFiles(path, installedName());
		} catch (cause) {
			errorMessage = cause instanceof Error ? cause.message : String(cause);
		} finally {
			busy = false;
		}
	}

	function size(bytes: number | null): string {
		if (bytes === null) {
			return 'unknown size';
		}
		return `${(bytes / 1_048_576).toFixed(1)} MB`;
	}
</script>

<div class="row" class:busy>
	<div class="index">{index}</div>
	<div class="name">
		<button
			type="button"
			class="favorite"
			class:marked={favorite}
			aria-label={favorite ? 'Remove from favorites' : 'Add to favorites'}
			aria-pressed={favorite}
			disabled={markingFavorite}
			onclick={() => void toggleFavorite()}
		>
			{favorite ? '★' : '☆'}
		</button>
		{#if entry.mod.thumbnail}
			<img class="thumb" src={entry.mod.thumbnail} alt="" loading="lazy" />
		{:else}
			<div class="thumb placeholder" aria-hidden="true">{entry.mod.name.slice(0, 1)}</div>
		{/if}
		<div class="name-text">
			<button class="name-link" onclick={() => void showDetails()}>
				<strong>{entry.mod.name}</strong>
			</button>
			<span class="author">{entry.mod.author}</span>
			{#if entry.mod.teaser}
				<span class="teaser" title={entry.mod.teaser}>{entry.mod.teaser}</span>
			{/if}
		</div>
	</div>
	<div>{entry.installed_version ?? '—'}</div>
	<div>{entry.latest_version?.version ?? '—'}</div>
	<div>{entry.latest_version?.spt_version_constraint || '—'}</div>
	<div>{entry.mod.downloads.toLocaleString()}</div>
	<div>{entry.mod.updated_at ? entry.mod.updated_at.slice(0, 10) : '—'}</div>
	<div>{installed?.mod_type ?? '—'}</div>
	<div><span class="chip {modState}">{STATE_LABELS[modState]}</span></div>
	<div class="actions">
		{#if progress !== null}
			<span class="progress" title={progress.message}>
				{progress.step} {Math.round(progress.percent)}%
			</span>
		{:else if modState === 'available'}
			<button type="button" disabled={locked || busy || planning} onclick={() => void openPlan()}>
				{planning ? 'Planning…' : 'Install'}
			</button>
		{:else if modState === 'incompatible'}
			<button type="button" disabled title="No version compatible with your SPT version">
				Install
			</button>
		{:else if modState === 'update_available'}
			<button
				type="button"
				disabled={locked || busy}
				onclick={() => void perform(update, false, 'Update anyway')}
			>
				Update
			</button>
		{:else if modState === 'disabled'}
			<button
				type="button"
				disabled={locked || busy}
				onclick={() => void perform(() => enableMod(path, installedName()), false, null)}
			>
				Enable
			</button>
		{:else}
			<button type="button" disabled={locked || busy} onclick={() => (confirmation = 'disable')}>
				Disable
			</button>
		{/if}

		<details class="menu">
			<summary aria-label="More actions">⋯</summary>
			<ul>
				{#if modState === 'active' || modState === 'update_available'}
					<li>
						<button
							type="button"
							disabled={locked || busy}
							onclick={() => void perform(() => disableMod(path, installedName()), false, null)}
						>
							Disable
						</button>
					</li>
				{/if}
				{#if modState === 'disabled'}
					<li>
						<button
							type="button"
							disabled={locked || busy}
							onclick={() => void perform(() => enableMod(path, installedName()), false, null)}
						>
							Enable
						</button>
					</li>
				{/if}
				{#if modState !== 'available' && modState !== 'incompatible'}
					<li>
						<button type="button" disabled={locked || busy} onclick={() => (confirmation = 'remove')}>
							Remove
						</button>
					</li>
					<li><button type="button" onclick={() => void showFiles()}>Files</button></li>
				{/if}
				<li>
					<button type="button" onclick={() => void showDetails()}>Details</button>
				</li>
				<li>
					<a href={entry.mod.detail_url} target="_blank" rel="noreferrer">Open Forge page</a>
				</li>
			</ul>
		</details>
	</div>
</div>

{#if plan !== null}
	<Dialog title={`Install ${entry.mod.name}`} onclose={() => (plan = null)}>
		<ul class="plan">
			{#each plan.entries as planned (planned.version_id)}
				<li>
					<span class="name">{planned.name} {planned.version}</span>
					{#if planned.is_dependency}<span class="kind">dependency</span>{/if}
					<span class="kind">{size(planned.content_length)}</span>
				</li>
			{/each}
		</ul>
		<p class="hint">Resolved for SPT {plan.spt_version}.</p>
		{#snippet actions()}
			<button
				type="button"
				onclick={() => {
					plan = null;
					void perform(install, false, 'Install anyway');
				}}
			>
				Install
			</button>
		{/snippet}
	</Dialog>
{/if}

{#if confirmation !== null}
	<Dialog
		title={confirmation === 'remove'
			? `Remove ${entry.mod.name}?`
			: `Disable ${entry.mod.name}?`}
		onclose={() => (confirmation = null)}
	>
		<p>
			{confirmation === 'remove'
				? 'The mod files are reverted; the history keeps them.'
				: 'The mod files leave the game directory until you enable the mod again.'}
		</p>
		{#snippet actions()}
			<button
				type="button"
				onclick={() => {
					const kind = confirmation;
					confirmation = null;
					if (kind === 'remove') {
						void perform(
							(force) => removeMod(path, installedName(), force),
							false,
							'Remove anyway'
						);
					} else {
						void perform(() => disableMod(path, installedName()), false, null);
					}
				}}
			>
				{confirmation === 'remove' ? 'Remove' : 'Disable'}
			</button>
		{/snippet}
	</Dialog>
{/if}

{#if files !== null}
	<Dialog title={`Files of ${entry.mod.name}`} onclose={() => (files = null)}>
		<ul class="files">
			{#each files as file (file.path)}
				<li><code>{file.change}</code> {file.path}</li>
			{/each}
		</ul>
	</Dialog>
{/if}

{#if details !== null}
	<Dialog title={entry.mod.name} onclose={() => (details = null)}>
		<div class="detail-head">
			{#if entry.mod.thumbnail}
				<img class="detail-thumb" src={entry.mod.thumbnail} alt="" />
			{/if}
			<div class="detail-meta">
				<div>by {entry.mod.author}</div>
				{#if details.mod}
					<div class="detail-stats">
						<span>{details.mod.downloads.toLocaleString()} downloads</span>
						<span>{details.mod.favourites_count.toLocaleString()} favourites</span>
						{#if details.mod.endorsements_count > 0}
							<span>{details.mod.endorsements_count.toLocaleString()} endorsements</span>
						{/if}
					</div>
					<div class="detail-tags">
						{#if details.mod.category}<span class="tag">{details.mod.category}</span>{/if}
						{#if details.mod.featured}<span class="tag">featured</span>{/if}
						{#if details.mod.fika_compatibility}<span class="tag">Fika</span>{/if}
						{#if details.mod.contains_ads}<span class="tag warn">contains ads</span>{/if}
						{#if details.mod.contains_ai_content}<span class="tag warn">AI content</span>{/if}
					</div>
					<div class="detail-links">
						{#if details.mod.license_link}
							<a href={details.mod.license_link} target="_blank" rel="noreferrer">
								{details.mod.license_name ?? 'License'}
							</a>
						{:else if details.mod.license_name}
							<span>{details.mod.license_name}</span>
						{/if}
						{#each details.mod.source_links ?? [] as link (link.url)}
							<a href={link.url} target="_blank" rel="noreferrer">{link.label || 'Source'}</a>
						{/each}
						<a href={entry.mod.detail_url} target="_blank" rel="noreferrer">Forge page</a>
					</div>
					{#if details.mod.updated_at}
						<div class="detail-dates">last updated {details.mod.updated_at.slice(0, 10)}</div>
					{/if}
				{/if}
			</div>
		</div>
		<p class="description">{details.description || 'No description available.'}</p>
		{#if details.versions.length > 0}
			<h3>Versions</h3>
			<table class="versions">
				<thead>
					<tr><th>Version</th><th>SPT</th><th>Downloads</th><th>Released</th></tr>
				</thead>
				<tbody>
					{#each details.versions as version (version.id)}
						<tr>
							<td>{version.version}</td>
							<td>{version.spt_version_constraint || '—'}</td>
							<td>{version.downloads.toLocaleString()}</td>
							<td>{version.published_at ? version.published_at.slice(0, 10) : '—'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</Dialog>
{/if}

{#if errorMessage !== ''}
	<Dialog
		title={`${entry.mod.name} failed`}
		onclose={() => {
			errorMessage = '';
			errorConflicts = [];
			errorResidues = [];
			errorInstalled = [];
			retry = null;
		}}
	>
		<p>{errorMessage}</p>
		{#if errorConflicts.length > 0}
			<ul class="findings">
				{#each errorConflicts as conflict, index (index)}
					<li>
						<span class="severity {conflict.severity}">{conflict.severity}</span>
						<span class="kind">{conflict.kind}</span>
						{conflict.message}
						{#if conflict.path}<code>{conflict.path}</code>{/if}
					</li>
				{/each}
			</ul>
		{/if}
		{#if errorInstalled.length > 0}
			<p class="hint">Already installed before the failure:</p>
			<ul class="findings">
				{#each errorInstalled as name (name)}
					<li>{name}</li>
				{/each}
			</ul>
		{/if}
		{#if errorResidues.length > 0}
			<ul class="findings">
				{#each errorResidues as residue, index (index)}
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
			{#if retry !== null}
				<button
					type="button"
					onclick={() => {
						const again = retry;
						errorMessage = '';
						errorConflicts = [];
						errorResidues = [];
						errorInstalled = [];
						retry = null;
						again?.();
					}}
				>
					{retryLabel}
				</button>
			{/if}
		{/snippet}
	</Dialog>
{/if}

<style>
	.row {
		display: grid;
		grid-template-columns: var(--mods-grid);
		align-items: center;
		gap: 0.6rem;
		padding: 0.45rem 0.6rem;
		border-bottom: 1px solid #2c3138;
	}

	.row.busy {
		opacity: 0.6;
	}

	@media (max-width: 1000px) {
		.row > :nth-child(n + 6):nth-child(-n + 8) {
			display: none;
		}
	}

	.index {
		text-align: right;
		color: #98a2ad;
		font-variant-numeric: tabular-nums;
	}

	.name {
		display: flex;
		align-items: center;
		gap: 0.55rem;
		min-width: 0;
	}

	.favorite {
		background: none;
		border: none;
		padding: 0;
		font-size: 1.1rem;
		line-height: 1;
		color: #98a2ad;
		cursor: pointer;
		flex-shrink: 0;
	}

	.favorite.marked {
		color: #f0cf7d;
	}

	.thumb {
		width: 2.4rem;
		height: 2.4rem;
		object-fit: cover;
		border-radius: 4px;
		flex-shrink: 0;
	}

	.thumb.placeholder {
		display: flex;
		align-items: center;
		justify-content: center;
		background: #2c333b;
		color: #98a2ad;
		font-size: 1.1rem;
		text-transform: uppercase;
	}

	.name-text {
		display: flex;
		flex-direction: column;
		min-width: 0;
	}

	.teaser {
		color: #98a2ad;
		font-size: 0.78rem;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.name-link {
		background: none;
		border: none;
		padding: 0;
		font: inherit;
		color: inherit;
		text-align: left;
		cursor: pointer;
	}

	.name-link:hover strong {
		text-decoration: underline;
	}

	.detail-head {
		display: flex;
		gap: 0.8rem;
		margin-bottom: 0.6rem;
	}

	.detail-meta {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		font-size: 0.85rem;
	}

	.detail-stats,
	.detail-links {
		display: flex;
		gap: 0.7rem;
		flex-wrap: wrap;
	}

	.detail-tags {
		display: flex;
		gap: 0.4rem;
		flex-wrap: wrap;
	}

	.tag {
		background: #2c333b;
		border-radius: 999px;
		padding: 0.05rem 0.55rem;
		font-size: 0.75rem;
	}

	.tag.warn {
		background: #4a3a25;
	}

	.detail-dates {
		color: #98a2ad;
		font-size: 0.78rem;
	}

	.versions {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.85rem;
	}

	.versions th,
	.versions td {
		text-align: left;
		padding: 0.25rem 0.6rem 0.25rem 0;
		border-bottom: 1px solid #3a4048;
	}

	.detail-thumb {
		max-width: 12rem;
		border-radius: 6px;
		margin-bottom: 0.6rem;
	}

	.description {
		white-space: pre-line;
		max-height: 24rem;
		overflow-y: auto;
	}

	.name strong {
		display: block;
	}

	.author {
		color: #98a2ad;
		font-size: 0.85em;
	}

	.chip {
		display: inline-block;
		padding: 0.1rem 0.5rem;
		border-radius: 999px;
		font-size: 0.8em;
		border: 1px solid #4a525c;
	}

	.chip.active {
		border-color: #3f8f52;
		color: #9ce0ac;
	}

	.chip.disabled {
		border-color: #6b7280;
		color: #b6bec8;
	}

	.chip.update_available {
		border-color: #b58a2b;
		color: #f0cf7d;
	}

	.chip.incompatible {
		border-color: #a3453f;
		color: #f2a19c;
	}

	.actions {
		display: flex;
		gap: 0.4rem;
		align-items: center;
		justify-content: flex-end;
	}

	.progress {
		font-size: 0.85em;
		color: #9ce0ac;
	}

	.menu {
		position: relative;
	}

	.menu summary {
		cursor: pointer;
		list-style: none;
		padding: 0.2rem 0.5rem;
		border: 1px solid #3a4048;
		border-radius: 4px;
	}

	.menu ul {
		position: absolute;
		right: 0;
		z-index: 5;
		margin: 0.2rem 0 0;
		padding: 0.3rem;
		list-style: none;
		background: #1e2228;
		border: 1px solid #3a4048;
		border-radius: 4px;
		min-width: 11rem;
	}

	.menu ul button,
	.menu ul a {
		display: block;
		width: 100%;
		text-align: left;
		background: none;
		border: none;
		color: inherit;
		padding: 0.3rem 0.4rem;
		cursor: pointer;
		text-decoration: none;
	}

	.files,
	.plan,
	.findings {
		list-style: none;
		padding: 0;
		margin: 0;
	}

	.files li,
	.plan li,
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

	.hint {
		color: #98a2ad;
		font-size: 0.9em;
	}
</style>
