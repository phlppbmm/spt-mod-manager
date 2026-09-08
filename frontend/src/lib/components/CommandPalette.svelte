<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { listProfiles, type ProfileSummary } from '$lib/api/client';
	import { profileLabel } from '$lib/cleanup';
	import {
		visibleCommands,
		type Command,
		type CommandGroup,
		type CommandProfile,
		type CommandSources,
		type CommandMod
	} from '$lib/commands';
	import { clearFilter } from '$lib/filterstate';
	import { requestIntent } from '$lib/intents';
	import { clampIndex, opensPalette, paletteKey } from '$lib/palettekeys';
	import { subscribeCommandMods, subscribeGameDir } from '$lib/shellstate';
	import type { ResolvedTheme } from '$lib/theme';
	import { cached } from '$lib/viewcache';

	interface Props {
		pathname: string;
		theme: ResolvedTheme;
		onToggleTheme: () => void;
	}

	let { pathname, theme, onToggleTheme }: Props = $props();

	const LIMIT = 40;
	const GROUPS: CommandGroup[] = ['Go to', 'Actions', 'Mods'];

	let open = $state(false);
	let query = $state('');
	let index = $state(0);
	let gameDir: string | null = $state(null);
	let mods: readonly CommandMod[] = $state([]);
	let profiles: CommandProfile[] = $state([]);
	let input: HTMLInputElement | undefined = $state();
	let restore: HTMLElement | null = null;

	let sources = $derived<CommandSources>({ pathname, gameDir, theme, profiles, mods });
	let commands = $derived(visibleCommands(query, sources, LIMIT));
	let sections = $derived(
		GROUPS.map((group) => ({
			group,
			entries: commands
				.map((command, position) => ({ command, position }))
				.filter((entry) => entry.command.group === group)
		})).filter((section) => section.entries.length > 0)
	);

	onMount(() => {
		const unsubscribeMods = subscribeCommandMods((published) => {
			mods = published;
		});
		const unsubscribeGameDir = subscribeGameDir((path) => {
			gameDir = path;
		});
		const onKeyDown = (event: KeyboardEvent): void => {
			if (!opensPalette(event)) {
				return;
			}
			event.preventDefault();
			if (open) {
				close();
			} else {
				show();
			}
		};
		window.addEventListener('keydown', onKeyDown);
		return () => {
			window.removeEventListener('keydown', onKeyDown);
			unsubscribeMods();
			unsubscribeGameDir();
		};
	});

	$effect(() => {
		if (open) {
			input?.focus();
		}
	});

	$effect(() => {
		const count = commands.length;
		index = clampIndex(count, untrack(() => index));
	});

	$effect(() => {
		document.getElementById(`palette-option-${index}`)?.scrollIntoView({ block: 'nearest' });
	});

	function show(): void {
		restore = document.activeElement instanceof HTMLElement ? document.activeElement : null;
		query = '';
		index = 0;
		open = true;
		if (gameDir !== null) {
			void loadProfiles(gameDir);
		}
	}

	function close(): void {
		open = false;
		const target = restore;
		restore = null;
		target?.focus();
	}

	async function loadProfiles(dir: string): Promise<void> {
		const view = cached(dir, 'list_profiles', [], () => listProfiles(dir));
		if (view.value !== undefined) {
			profiles = toEntries(view.value);
			return;
		}
		try {
			profiles = toEntries(await view.refresh());
		} catch {
			profiles = [];
		}
	}

	function toEntries(summaries: ProfileSummary[]): CommandProfile[] {
		return summaries.map((profile) => ({
			id: profile.profile_id,
			label: profileLabel(profile)
		}));
	}

	function onKeydown(event: KeyboardEvent): void {
		const result = paletteKey(event.key, { count: commands.length, index });
		if (result.preventDefault) {
			event.preventDefault();
		}
		if (result.index !== null) {
			index = result.index;
		}
		if (result.run) {
			void run(commands[index]);
		}
		if (result.close) {
			event.stopPropagation();
			close();
		}
	}

	async function run(command: Command): Promise<void> {
		close();
		const action = command.action;
		if (action.kind === 'navigate') {
			await goto(action.href);
		} else if (action.kind === 'intent') {
			if (pathname !== action.route) {
				await goto(action.route);
			}
			requestIntent(action.intent);
		} else if (action.kind === 'reveal') {
			if (pathname !== '/') {
				await goto('/');
			}
			requestIntent({ kind: 'reveal-mod', modId: action.modId });
		} else if (action.kind === 'clear-filters') {
			clearFilter();
		} else {
			onToggleTheme();
		}
	}
</script>

{#if open}
	<div
		class="backdrop"
		role="presentation"
		onclick={(event) => {
			if (event.target === event.currentTarget) {
				close();
			}
		}}
	>
		<div class="palette" role="dialog" aria-modal="true" aria-label="Commands">
			<input
				class="query"
				type="text"
				aria-label="Search commands"
				placeholder="Type a command or a mod name…"
				aria-activedescendant={commands.length > 0 ? `palette-option-${index}` : undefined}
				bind:this={input}
				bind:value={query}
				oninput={() => (index = 0)}
				onkeydown={onKeydown}
			/>
			<div class="list" role="listbox" aria-label="Commands">
				{#each sections as section (section.group)}
					<div class="group">{section.group}</div>
					{#each section.entries as entry (entry.command.id)}
						<div
							class="option"
							class:is-active={entry.position === index}
							role="option"
							id="palette-option-{entry.position}"
							aria-selected={entry.position === index}
						>
							<span class="title">{entry.command.title}</span>
							{#if entry.command.subtitle !== ''}
								<span class="subtitle">{entry.command.subtitle}</span>
							{/if}
						</div>
					{/each}
				{/each}
				{#if commands.length === 0}
					<p class="empty">No matches</p>
				{/if}
			</div>
		</div>
	</div>
{/if}

<style>
	.backdrop {
		position: fixed;
		inset: 0;
		display: flex;
		align-items: flex-start;
		justify-content: center;
		padding: 12vh var(--space-4) var(--space-4);
		background: var(--color-overlay);
		z-index: var(--z-dialog);
	}

	.palette {
		display: flex;
		flex-direction: column;
		width: min(560px, 100%);
		max-height: 70vh;
		background: var(--color-elevated);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-lg);
		box-shadow: var(--shadow-lg);
		overflow: hidden;
	}

	.query {
		height: var(--control-height);
		margin: var(--space-3);
		padding: 0 var(--space-3);
		background: var(--color-bg);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-md);
		font-size: var(--text-base);
	}

	.list {
		padding: 0 var(--space-2) var(--space-2);
		overflow-y: auto;
	}

	.group {
		padding: var(--space-2) var(--space-2) var(--space-1);
		color: var(--color-text-muted);
		font-size: var(--text-xs);
		font-weight: var(--weight-semibold);
		letter-spacing: var(--tracking-caps);
		text-transform: uppercase;
	}

	.option {
		display: flex;
		align-items: baseline;
		gap: var(--space-2);
		padding: var(--space-2);
		border-radius: var(--radius-sm);
	}

	.option.is-active {
		background: var(--color-accent-soft);
		box-shadow: inset 0 0 0 1px var(--focus-ring);
	}

	.title {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.subtitle {
		color: var(--color-text-muted);
		font-size: var(--text-sm);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.empty {
		margin: 0;
		padding: var(--space-3) var(--space-2);
		color: var(--color-text-muted);
	}
</style>
