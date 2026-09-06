<script lang="ts">
	import { onMount } from 'svelte';
	import GameDirMenu from '$lib/components/GameDirMenu.svelte';
	import { getFilter, setFilter, subscribeFilter } from '$lib/filterstate';
	import { loadGameDir } from '$lib/gamedir';
	import {
		requestSync,
		setGameDir,
		subscribeGameDir,
		subscribeSearchEnabled
	} from '$lib/shellstate';
	import type { ResolvedTheme } from '$lib/theme';
	import { closeWindow, minimizeWindow } from '$lib/window';

	interface Props {
		theme: ResolvedTheme;
		maximized: boolean;
		onToggleTheme: () => void;
		onToggleMaximize: () => void;
	}

	let { theme, maximized, onToggleTheme, onToggleMaximize }: Props = $props();

	let search = $state('');
	let searchEnabled = $state(false);
	let gameDir: string | null = $state(null);
	let input: HTMLInputElement | undefined = $state();
	let debounce: ReturnType<typeof setTimeout> | undefined;
	let pending: string | null = null;

	onMount(() => {
		setGameDir(loadGameDir());
		const unsubscribeFilter = subscribeFilter((filter) => {
			search = filter.search;
		});
		const unsubscribeSearch = subscribeSearchEnabled((on) => {
			searchEnabled = on;
		});
		const unsubscribeGameDir = subscribeGameDir((path) => {
			gameDir = path;
		});
		const onKeyDown = (event: KeyboardEvent): void => {
			if (
				event.key !== '/' ||
				event.target instanceof HTMLInputElement ||
				event.target instanceof HTMLTextAreaElement
			) {
				return;
			}
			event.preventDefault();
			input?.focus();
		};
		window.addEventListener('keydown', onKeyDown);
		return () => {
			flushSearch();
			window.removeEventListener('keydown', onKeyDown);
			unsubscribeFilter();
			unsubscribeSearch();
			unsubscribeGameDir();
		};
	});

	function flushSearch(): void {
		clearTimeout(debounce);
		if (pending !== null) {
			setFilter({ ...getFilter(), search: pending });
			pending = null;
		}
	}

	function onInput(event: Event & { currentTarget: HTMLInputElement }): void {
		const value = event.currentTarget.value;
		search = value;
		pending = value;
		clearTimeout(debounce);
		debounce = setTimeout(() => {
			pending = null;
			setFilter({ ...getFilter(), search: value });
		}, 250);
	}
</script>

<header class="topbar" data-tauri-drag-region>
	<span class="brand" data-tauri-drag-region>sptmod</span>
	<GameDirMenu path={gameDir} />
	<div class="topbar-spacer" data-tauri-drag-region></div>

	<div class="search">
		<svg
			class="search-icon"
			width="14"
			height="14"
			viewBox="0 0 14 14"
			fill="none"
			stroke="currentColor"
			stroke-width="1.4"
			stroke-linecap="round"
			aria-hidden="true"
		>
			<circle cx="6.2" cy="6.2" r="4" />
			<path d="M9.2 9.2 12.5 12.5" />
		</svg>
		<input
			type="text"
			aria-label="Search mods"
			placeholder="Search mods…"
			disabled={!searchEnabled}
			title={searchEnabled ? undefined : 'Search works on the Mods view'}
			bind:this={input}
			value={search}
			oninput={onInput}
			onkeydown={(event) => {
				if (event.key === 'Escape') {
					event.currentTarget.blur();
				}
			}}
		/>
	</div>

	<div class="topbar-spacer" data-tauri-drag-region></div>

	<button
		class="btn-icon"
		disabled={!searchEnabled}
		aria-label={searchEnabled ? 'Sync catalog' : 'Sync catalog (open Mods first)'}
		title={searchEnabled ? 'Sync catalog' : 'Sync catalog (open Mods first)'}
		onclick={requestSync}
	>
		<svg
			width="16"
			height="16"
			viewBox="0 0 16 16"
			fill="none"
			stroke="currentColor"
			stroke-width="1.4"
			stroke-linecap="round"
			stroke-linejoin="round"
			aria-hidden="true"
		>
			<path d="M13.4 7.1a5.5 5.5 0 0 0-9.5-2.6L2.2 6.1" />
			<path d="M2.6 8.9a5.5 5.5 0 0 0 9.5 2.6l1.7-1.6" />
			<path d="M2.2 2.9v3.2h3.2" />
			<path d="M13.8 13.1V9.9h-3.2" />
		</svg>
	</button>

	<button
		class="btn-icon"
		aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
		title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
		onclick={onToggleTheme}
	>
		{#if theme === 'dark'}
			<svg
				width="16"
				height="16"
				viewBox="0 0 16 16"
				fill="none"
				stroke="currentColor"
				stroke-width="1.4"
				stroke-linecap="round"
				aria-hidden="true"
			>
				<circle cx="8" cy="8" r="3.2" />
				<path
					d="M8 1.2v1.6M8 13.2v1.6M1.2 8h1.6M13.2 8h1.6M3.2 3.2l1.1 1.1M11.7 11.7l1.1 1.1M12.8 3.2l-1.1 1.1M4.3 11.7l-1.1 1.1"
				/>
			</svg>
		{:else}
			<svg
				width="16"
				height="16"
				viewBox="0 0 16 16"
				fill="none"
				stroke="currentColor"
				stroke-width="1.4"
				stroke-linejoin="round"
				aria-hidden="true"
			>
				<path d="M13.5 9.6A5.8 5.8 0 0 1 6.4 2.5a5.8 5.8 0 1 0 7.1 7.1Z" />
			</svg>
		{/if}
	</button>

	<div class="window-controls">
		<button class="win" aria-label="Minimize" title="Minimize" onclick={minimizeWindow}>
			<svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
				<path d="M0 5h10" stroke="currentColor" stroke-width="1" />
			</svg>
		</button>
		<button
			class="win"
			aria-label={maximized ? 'Restore' : 'Maximize'}
			title={maximized ? 'Restore' : 'Maximize'}
			onclick={onToggleMaximize}
		>
			{#if maximized}
				<svg
					width="10"
					height="10"
					viewBox="0 0 10 10"
					fill="none"
					stroke="currentColor"
					stroke-width="1"
					aria-hidden="true"
				>
					<path d="M2.5 2.5h5v5h-5z" />
					<path d="M4 2.5V1h4.5v4.5H7" />
				</svg>
			{:else}
				<svg
					width="10"
					height="10"
					viewBox="0 0 10 10"
					fill="none"
					stroke="currentColor"
					stroke-width="1"
					aria-hidden="true"
				>
					<path d="M0.5 0.5h9v9h-9z" />
				</svg>
			{/if}
		</button>
		<button class="win win-close" aria-label="Close" title="Close" onclick={closeWindow}>
			<svg
				width="10"
				height="10"
				viewBox="0 0 10 10"
				stroke="currentColor"
				stroke-width="1"
				aria-hidden="true"
			>
				<path d="M0.5 0.5l9 9M9.5 0.5l-9 9" />
			</svg>
		</button>
	</div>
</header>

<style>
	.topbar {
		position: relative;
		display: flex;
		align-items: center;
		gap: var(--space-1);
		height: var(--topbar-height);
		padding: 0 var(--space-2) 0 var(--space-4);
		background: var(--color-surface);
		border-bottom: 1px solid var(--color-border);
		user-select: none;
		z-index: var(--z-header);
	}

	.brand {
		margin-right: var(--space-2);
		color: var(--color-text);
		font-size: var(--text-sm);
		font-weight: var(--weight-semibold);
		letter-spacing: var(--tracking-caps);
	}

	.topbar-spacer {
		flex: 1;
		align-self: stretch;
	}

	.search {
		position: absolute;
		left: 50%;
		transform: translateX(-50%);
		display: flex;
		align-items: center;
		width: min(520px, 42vw);
	}

	.search-icon {
		position: absolute;
		left: var(--space-3);
		color: var(--color-text-subtle);
		pointer-events: none;
	}

	.search input {
		width: 100%;
		height: var(--control-height-sm);
		padding: 0 var(--space-3) 0 var(--space-8);
		background: var(--color-bg);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-pill);
		font-size: var(--text-sm);
	}

	.search input:disabled {
		background: var(--color-control-disabled-bg);
		color: var(--color-text-subtle);
		cursor: default;
	}

	.window-controls {
		display: flex;
		gap: var(--space-1);
		margin-left: var(--space-2);
	}

	.win {
		width: var(--control-height);
		height: var(--control-height);
		padding: 0;
		background: transparent;
		border: 1px solid transparent;
		border-radius: var(--radius-sm);
		color: var(--color-text-muted);
	}

	.win:hover {
		background: var(--color-hover);
		color: var(--color-text);
	}

	.win-close:hover {
		background: var(--color-danger);
		color: var(--color-text-inverse);
	}
</style>
