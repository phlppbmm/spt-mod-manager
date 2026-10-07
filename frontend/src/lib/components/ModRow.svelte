<script lang="ts">
	import { untrack } from 'svelte';
	import Dialog from './Dialog.svelte';
	import StatusBadge from './StatusBadge.svelte';
	import {
		disableMod,
		enableMod,
		getModDetails,
		installMod,
		modFiles,
		planModInstall,
		planProfileCleanup,
		removeMod,
		setFavorite,
		updateMod,
		type CatalogEntry,
		type CleanupPlan,
		type Conflict,
		type InstallPlan,
		type ModDetails,
		type ModFile,
		type ModInfo,
		type ProfileResidue
	} from '$lib/api/client';
	import { ApiError, subscribe, type ProgressEvent } from '$lib/api/transport';
	import { groupRemovalsByProfile } from '$lib/cleanup';
	import type { RowState } from '$lib/modfilter';
	import { rowKey } from '$lib/rownav';
	import { loadThumbnail } from '$lib/thumbnails';

	interface Props {
		path: string;
		index: number;
		entry: CatalogEntry;
		modState: RowState;
		installed: ModInfo | null;
		locked: boolean;
		favorite: boolean;
		selected: boolean;
		ids: readonly number[];
		focusedId: number | null;
		tabStopId: number | null;
		onfocusrow: (modId: number | null) => void;
		onselect: () => void;
		onchanged: () => void;
		onfavorite: (modIds: number[]) => void;
		onbusy: (modId: number, busy: boolean) => void;
		showInstalledColumns: boolean;
	}

	let {
		path,
		index,
		entry,
		modState,
		installed,
		locked,
		favorite,
		selected,
		ids,
		focusedId,
		tabStopId,
		onfocusrow,
		onselect,
		onchanged,
		onfavorite,
		onbusy,
		showInstalledColumns
	}: Props = $props();

	const EAGER_ROWS = 60;
	const LOOKAHEAD = '200px';

	const FORCEABLE_CODES = [
		'CONFLICT',
		'DEPENDENCY',
		'UNRESOLVABLE_DEPENDENCY',
		'PROFILE_RESIDUE'
	];

	let rowElement: HTMLElement | null = $state(null);
	let onScreen = $state(false);
	let thumbnail: string | null = $state(null);
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
	let cleanupPlan: CleanupPlan | null = $state(null);
	let planningCleanup = $state(false);
	let cleanupPlanFailed = $state(false);
	let cleanProfiles = $state(false);

	let cleanupGroups = $derived.by(() => groupRemovalsByProfile(cleanupPlan?.removals ?? []));

	// A mod the player installed themselves has no catalog row: no Forge page,
	// no published version, and the SPT range git recorded instead of one.
	let isLocal = $derived(entry.local);
	let sptLabel = $derived(
		entry.latest_version?.spt_version_constraint || installed?.spt_range || '—'
	);

	// A row is watched only until it has been on screen once, and its position decides only whether
	// it starts out on screen: a sort hands nearly every row a new index, and reacting to that rebuilt
	// an observer per row on each sort -- close to two seconds for a full catalog.
	$effect(() => {
		const element = rowElement;
		if (onScreen) {
			return;
		}
		if (untrack(() => index) <= EAGER_ROWS || typeof IntersectionObserver === 'undefined') {
			onScreen = true;
			return;
		}
		if (element === null) {
			return;
		}
		const observer = new IntersectionObserver(
			(seen) => {
				if (seen.some((observed) => observed.isIntersecting)) {
					onScreen = true;
				}
			},
			{ rootMargin: LOOKAHEAD }
		);
		observer.observe(element);
		return () => observer.disconnect();
	});

	$effect(() => {
		const modId = entry.mod.id;
		const source = entry.mod.thumbnail;
		if (!source || !onScreen) {
			thumbnail = null;
			return;
		}
		let current = true;
		void loadThumbnail(modId, source).then((dataUrl) => {
			if (current) {
				thumbnail = dataUrl;
			}
		});
		return () => {
			current = false;
		};
	});

	let cleanupPlanGeneration = 0;

	async function openRemove(): Promise<void> {
		const generation = ++cleanupPlanGeneration;
		confirmation = 'remove';
		cleanupPlan = null;
		cleanupPlanFailed = false;
		cleanProfiles = false;
		planningCleanup = true;
		try {
			const planned = await planProfileCleanup(path, installedName());
			if (generation === cleanupPlanGeneration) {
				cleanupPlan = planned;
			}
		} catch {
			if (generation === cleanupPlanGeneration) {
				cleanupPlanFailed = true;
			}
		} finally {
			if (generation === cleanupPlanGeneration) {
				planningCleanup = false;
			}
		}
	}

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
		if (isLocal) {
			return;
		}
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

	let tabStop = $derived(tabStopId === entry.mod.id);

	function onRowKeydown(event: KeyboardEvent & { currentTarget: HTMLDivElement }): void {
		if (event.target !== event.currentTarget) {
			return;
		}
		const result = rowKey(event.key, { ids, focusId: focusedId });
		if (!result.handled) {
			return;
		}
		if (result.preventDefault) {
			event.preventDefault();
		}
		onfocusrow(result.focusId);
		if (result.action === 'open') {
			void showDetails();
		} else if (result.action === 'toggle-select') {
			onselect();
		} else if (result.action === 'toggle-favorite') {
			void toggleFavorite();
		} else if (result.action === 'blur') {
			event.currentTarget.blur();
		}
	}

	function size(bytes: number | null): string {
		if (bytes === null) {
			return 'unknown size';
		}
		return `${(bytes / 1_048_576).toFixed(1)} MB`;
	}
</script>

<div
	bind:this={rowElement}
	class="row panel-row"
	class:busy
	id="modrow-{entry.mod.id}"
	role="row"
	aria-rowindex={index + 1}
	tabindex={tabStop ? 0 : -1}
	onfocus={() => onfocusrow(entry.mod.id)}
	onkeydown={onRowKeydown}
>
	<div class="pick">
		<input
			type="checkbox"
			aria-label={`Select ${entry.mod.name}`}
			checked={selected}
			disabled={locked}
			onchange={onselect}
		/>
	</div>
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
		{#if thumbnail}
			<img class="thumb" src={thumbnail} alt="" />
		{:else}
			<div class="thumb placeholder" aria-hidden="true">{entry.mod.name.slice(0, 1)}</div>
		{/if}
		<div class="name-text">
			{#if isLocal}
				<span class="name-plain">
					<strong>{entry.mod.name}</strong>
					<span class="local-tag">Local</span>
				</span>
			{:else}
				<button class="name-link" onclick={() => void showDetails()}>
					<strong>{entry.mod.name}</strong>
				</button>
			{/if}
			<span class="author">{entry.mod.author}</span>
			<!-- Always a line, empty when there is no teaser: the list renders only the rows in view and
			     needs every row to be the same height. -->
			<span class="teaser" title={entry.mod.teaser || undefined}>{entry.mod.teaser || '\u00a0'}</span>
		</div>
	</div>
	{#if showInstalledColumns}
		<div>{entry.installed_version ?? '—'}</div>
	{/if}
	<div>{entry.latest_version?.version ?? '—'}</div>
	<div>{sptLabel}</div>
	<div class="downloads">{entry.mod.downloads.toLocaleString()}</div>
	<div class="updated">{entry.mod.updated_at ? entry.mod.updated_at.slice(0, 10) : '—'}</div>
	{#if showInstalledColumns}
		<div class="type">{installed?.mod_type ?? '—'}</div>
	{/if}
	<div><StatusBadge state={modState} /></div>
	<div class="actions">
		{#if progress !== null}
			<span class="progress" title={progress.message}>
				{progress.step} {Math.round(progress.percent)}%
			</span>
		{:else if modState === 'available'}
			<button
				type="button"
				class="btn-primary btn-sm"
				disabled={locked || busy || planning}
				onclick={() => void openPlan()}
			>
				{planning ? 'Planning…' : 'Install'}
			</button>
		{:else if modState === 'incompatible'}
			<button
				type="button"
				class="btn-primary btn-sm"
				disabled
				title="No version compatible with your SPT version"
			>
				Install
			</button>
		{:else if modState === 'update_available'}
			<button
				type="button"
				class="btn-primary btn-sm"
				disabled={locked || busy}
				onclick={() => void perform(update, false, 'Update anyway')}
			>
				Update
			</button>
		{:else if modState === 'disabled'}
			<button
				type="button"
				class="btn-sm"
				disabled={locked || busy}
				onclick={() => void perform(() => enableMod(path, installedName()), false, null)}
			>
				Enable
			</button>
		{:else}
			<button
				type="button"
				class="btn-sm"
				disabled={locked || busy}
				onclick={() => (confirmation = 'disable')}
			>
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
						<button
							type="button"
							disabled={locked || busy || planningCleanup}
							onclick={() => void openRemove()}
						>
							Remove
						</button>
					</li>
					<li><button type="button" onclick={() => void showFiles()}>Files</button></li>
				{/if}
				{#if !isLocal}
					<li>
						<button type="button" onclick={() => void showDetails()}>Details</button>
					</li>
					<li>
						<a href={entry.mod.detail_url} target="_blank" rel="noreferrer">Open Forge page</a>
					</li>
				{/if}
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
				class="btn-primary"
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
				? 'The mod files leave the game directory; the history keeps them.'
				: 'The mod files leave the game directory until you enable the mod again.'}
		</p>
		{#if confirmation === 'remove'}
			<label class="clean">
				<input
					type="checkbox"
					bind:checked={cleanProfiles}
					disabled={planningCleanup || cleanupGroups.length === 0}
				/>
				Also clean the player profiles
			</label>
			{#if planningCleanup}
				<p class="hint">Looking through the player profiles…</p>
			{:else if cleanupPlanFailed}
				<p class="hint">sptmod could not look through the player profiles.</p>
			{:else if cleanupGroups.length === 0}
				<p class="hint">The player profiles hold nothing of this mod.</p>
			{:else}
				<ul class="findings">
					{#each cleanupGroups as group (group.profile)}
						<li>
							<span class="kind">{group.file}</span>
							{group.entries}
							{group.entries === 1 ? 'entry' : 'entries'} in {group.places}
							{group.places === 1 ? 'place' : 'places'}
						</li>
					{/each}
				</ul>
				{#if (cleanupPlan?.untouched.length ?? 0) > 0}
					<p class="hint">
						{cleanupPlan?.untouched.length}
						{(cleanupPlan?.untouched.length ?? 0) === 1 ? 'reference' : 'references'} sptmod cannot
						identify stay untouched.
					</p>
				{/if}
				<p class="hint">Quest and achievement progress tied to this mod is removed as well.</p>
				<p class="hint">
					A copy of each profile is saved in the game's own backups folder first, and the change
					shows up in History as a snapshot.
				</p>
			{/if}
		{/if}
		{#snippet actions()}
			<button
				type="button"
				class="btn-primary"
				onclick={() => {
					const kind = confirmation;
					const clean = cleanProfiles;
					confirmation = null;
					if (kind === 'remove') {
						void perform(
							(force) => removeMod(path, installedName(), force, clean),
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
			{#if thumbnail}
				<img class="detail-thumb" src={thumbnail} alt="" />
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
						{#if details.mod.category}
							<span class="badge badge-neutral">{details.mod.category}</span>
						{/if}
						{#if details.mod.featured}<span class="badge badge-neutral">featured</span>{/if}
						{#if details.mod.fika_compatibility}
							<span class="badge badge-neutral">Fika</span>
						{/if}
						{#if details.mod.contains_ads}
							<span class="badge badge-warn">contains ads</span>
						{/if}
						{#if details.mod.contains_ai_content}
							<span class="badge badge-warn">AI content</span>
						{/if}
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
					<tr><th>Version</th><th>SPT</th><th>Fika</th><th>Downloads</th><th>Released</th></tr>
				</thead>
				<tbody>
					{#each details.versions as version (version.id)}
						<tr>
							<td>{version.version}</td>
							<td>{version.spt_version_constraint || '—'}</td>
							<td>
								{#if version.fika_compatibility === 'compatible'}
									<span class="badge badge-neutral">Fika</span>
								{:else if version.fika_compatibility === 'incompatible'}
									<span class="badge badge-warn">No Fika</span>
								{/if}
							</td>
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
						<span
							class="badge severity {conflict.severity === 'error'
								? 'badge-danger'
								: 'badge-warn'}">{conflict.severity}</span
						>
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
					class="btn-primary"
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
		gap: var(--space-2);
		min-height: var(--row-height, 44px);
		padding: var(--space-2) var(--space-3);
		font-size: var(--text-base);
	}

	.row.busy {
		opacity: 0.6;
	}

	/* The global ring only covers :focus-visible, which a revealed row is not. */
	.row:focus {
		outline: none;
		box-shadow: inset 0 0 0 2px var(--focus-ring);
	}

	@container mods (max-width: 1060px) {
		.row > .downloads,
		.row > .updated,
		.row > .type {
			display: none;
		}
	}

	.pick {
		display: flex;
		align-items: center;
	}

	.index {
		text-align: right;
		color: var(--color-text-subtle);
		font-size: var(--text-sm);
		font-variant-numeric: tabular-nums;
	}

	.name {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		min-width: 0;
	}

	.favorite {
		height: auto;
		padding: 0;
		background: none;
		border: none;
		color: var(--color-text-subtle);
		font-size: var(--text-lg);
		line-height: 1;
		flex-shrink: 0;
	}

	.favorite:hover:not(:disabled),
	.favorite:disabled {
		background: none;
	}

	.favorite.marked {
		color: var(--color-warn);
	}

	.thumb {
		width: var(--thumb-size);
		height: var(--thumb-size);
		object-fit: cover;
		border-radius: var(--radius-md);
		flex-shrink: 0;
	}

	.thumb.placeholder {
		display: flex;
		align-items: center;
		justify-content: center;
		background: var(--color-elevated);
		border: 1px solid var(--color-border);
		color: var(--color-text-muted);
		font-size: var(--text-lg);
		text-transform: uppercase;
	}

	.name-text {
		display: flex;
		flex-direction: column;
		min-width: 0;
	}

	.name strong {
		display: block;
	}

	.name-plain {
		display: flex;
		align-items: baseline;
		gap: var(--space-2);
		min-width: 0;
	}

	.name-plain strong {
		display: inline;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.local-tag {
		flex: none;
		padding: 0 var(--space-1);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-pill);
		color: var(--color-text-muted);
		font-size: var(--text-xs);
	}

	.name-link {
		display: block;
		height: auto;
		padding: 0;
		background: none;
		border: none;
		font: inherit;
		color: inherit;
		text-align: left;
		cursor: pointer;
	}

	.name-link:hover {
		background: none;
	}

	.name-link:hover strong {
		text-decoration: underline;
	}

	.author {
		color: var(--color-text-muted);
		font-size: var(--text-sm);
	}

	.teaser {
		color: var(--color-text-subtle);
		font-size: var(--text-sm);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.actions {
		display: flex;
		gap: var(--space-1);
		align-items: center;
		justify-content: flex-end;
	}

	.progress {
		font-size: var(--text-sm);
		color: var(--color-success);
	}

	.menu {
		position: relative;
	}

	.menu summary {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		height: var(--control-height-sm);
		padding: 0 var(--space-2);
		border: 1px solid var(--color-control-border);
		border-radius: var(--radius-md);
		color: var(--color-text-muted);
		cursor: pointer;
		list-style: none;
	}

	.menu summary::-webkit-details-marker {
		display: none;
	}

	.menu summary:hover {
		background: var(--color-hover);
		color: var(--color-text);
	}

	.menu ul {
		position: absolute;
		right: 0;
		z-index: var(--z-menu);
		margin: var(--space-1) 0 0;
		padding: var(--space-1);
		min-width: 11rem;
		list-style: none;
		background: var(--color-elevated);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-md);
		box-shadow: var(--shadow-md);
	}

	.menu ul button,
	.menu ul a {
		display: block;
		width: 100%;
		height: auto;
		padding: var(--space-1) var(--space-2);
		background: none;
		border: none;
		border-radius: var(--radius-sm);
		color: var(--color-text);
		font: inherit;
		text-align: left;
		text-decoration: none;
		cursor: pointer;
	}

	.menu ul button:hover:not(:disabled),
	.menu ul a:hover {
		background: var(--color-hover);
		text-decoration: none;
	}

	.detail-head {
		display: flex;
		gap: var(--space-4);
		margin-bottom: var(--space-3);
	}

	.detail-thumb {
		max-width: 12rem;
		border-radius: var(--radius-md);
		margin-bottom: var(--space-3);
	}

	.detail-meta {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		font-size: var(--text-sm);
	}

	.detail-stats,
	.detail-links {
		display: flex;
		gap: var(--space-3);
		flex-wrap: wrap;
	}

	.detail-tags {
		display: flex;
		gap: var(--space-2);
		flex-wrap: wrap;
	}

	.detail-dates {
		color: var(--color-text-muted);
		font-size: var(--text-sm);
	}

	.description {
		white-space: pre-line;
		max-height: 24rem;
		overflow-y: auto;
	}

	.versions {
		width: 100%;
		border-collapse: collapse;
		font-size: var(--text-base);
	}

	.versions th,
	.versions td {
		text-align: left;
		padding: var(--space-1) var(--space-3) var(--space-1) 0;
		border-bottom: 1px solid var(--color-divider);
	}

	.versions th {
		color: var(--color-text-muted);
		font-size: var(--text-xs);
		font-weight: var(--weight-semibold);
		letter-spacing: var(--tracking-caps);
		text-transform: uppercase;
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
		padding: var(--space-1) 0;
	}

	.severity {
		margin-right: var(--space-2);
		letter-spacing: var(--tracking-caps);
		text-transform: uppercase;
	}

	.kind {
		color: var(--color-text-muted);
		margin-right: var(--space-2);
	}

	.hint {
		color: var(--color-text-muted);
		font-size: var(--text-sm);
	}

	.clean {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		margin: var(--space-3) 0 var(--space-1);
	}
</style>
