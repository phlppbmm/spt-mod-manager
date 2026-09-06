<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import favicon from '$lib/assets/favicon.svg';
	import {
		applyTheme,
		loadPreference,
		resolveTheme,
		savePreference,
		toggledPreference,
		watchSystemTheme,
		type ResolvedTheme
	} from '$lib/theme';
	import {
		closeWindow,
		isWindowMaximized,
		minimizeWindow,
		toggleMaximizeWindow
	} from '$lib/window';
	import '$lib/styles/tokens.css';
	import '$lib/styles/base.css';

	const LINKS = [
		{ href: '/', label: 'Mods' },
		{ href: '/profile', label: 'Profiles' },
		{ href: '/history', label: 'History' },
		{ href: '/settings', label: 'Settings' }
	];

	let { children } = $props();

	let theme = $state<ResolvedTheme>('light');
	let maximized = $state(false);

	onMount(() => {
		theme = resolveTheme(loadPreference());
		applyTheme(theme);
		void refreshMaximized();
		// The settings page applies the theme too, so follow the attribute
		// rather than the local state.
		const observer = new MutationObserver(readAppliedTheme);
		observer.observe(document.documentElement, { attributeFilter: ['data-theme'] });
		const unwatch = watchSystemTheme((systemTheme) => {
			if (loadPreference() === 'system') {
				applyTheme(systemTheme);
			}
		});
		return () => {
			observer.disconnect();
			unwatch();
		};
	});

	function readAppliedTheme(): void {
		theme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
	}

	async function refreshMaximized(): Promise<void> {
		maximized = await isWindowMaximized();
	}

	function toggleTheme(): void {
		const preference = toggledPreference(resolveTheme(loadPreference()));
		savePreference(preference);
		applyTheme(resolveTheme(preference));
	}

	async function toggleMaximize(): Promise<void> {
		await toggleMaximizeWindow();
		await refreshMaximized();
	}
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

<div class="shell">
	<header class="topbar" data-tauri-drag-region>
		<span class="brand" data-tauri-drag-region>sptmod</span>
		<div class="topbar-spacer" data-tauri-drag-region></div>
		<button
			class="btn-icon"
			aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
			title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
			onclick={toggleTheme}
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
				onclick={toggleMaximize}
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
			<button
				class="win win-close"
				aria-label="Close"
				title="Close"
				onclick={closeWindow}
			>
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

	<nav class="mainnav">
		{#each LINKS as link (link.href)}
			<a
				class="tab"
				class:is-active={page.url.pathname === link.href}
				href={link.href}
				aria-current={page.url.pathname === link.href ? 'page' : undefined}
			>
				{link.label}
			</a>
		{/each}
	</nav>

	<main class="content">{@render children()}</main>
</div>

<style>
	.shell {
		display: flex;
		flex-direction: column;
		height: 100vh;
	}

	.topbar {
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
		color: var(--color-text);
		font-size: var(--text-sm);
		font-weight: var(--weight-semibold);
		letter-spacing: var(--tracking-caps);
	}

	.topbar-spacer {
		flex: 1;
		align-self: stretch;
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

	.mainnav {
		display: flex;
		align-items: center;
		gap: var(--space-1);
		height: var(--nav-height);
		padding: 0 var(--space-3);
		background: var(--color-surface);
		border-bottom: 1px solid var(--color-border);
	}

	.tab {
		display: inline-flex;
		align-items: center;
		height: 26px;
		padding: 0 var(--space-3);
		border-radius: var(--radius-pill);
		color: var(--color-text-muted);
		font-size: var(--text-base);
		font-weight: var(--weight-medium);
		text-decoration: none;
	}

	.tab:hover {
		background: var(--color-hover);
		color: var(--color-text);
		text-decoration: none;
	}

	.tab.is-active {
		background: var(--color-accent);
		color: var(--color-accent-fg);
	}

	.content {
		flex: 1;
		min-height: 0;
		overflow: auto;
	}
</style>
