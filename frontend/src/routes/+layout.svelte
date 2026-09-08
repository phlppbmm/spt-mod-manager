<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import favicon from '$lib/assets/favicon.svg';
	import SubNav from '$lib/components/SubNav.svelte';
	import TopBar from '$lib/components/TopBar.svelte';
	import { subscribeSubNav, type SubNavState } from '$lib/shellstate';
	import {
		applyTheme,
		loadPreference,
		resolveTheme,
		savePreference,
		toggledPreference,
		watchSystemTheme,
		type ResolvedTheme
	} from '$lib/theme';
	import { checkForUpdate, installUpdate } from '$lib/updater';
	import { runCheck, startupNotice } from '$lib/updatestate';
	import { isWindowMaximized, toggleMaximizeWindow } from '$lib/window';
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
	let subnav = $state<SubNavState>({ items: [], active: null });
	let updateNotice = $state('');

	const sectionLabel = $derived(
		LINKS.find((link) => link.href === page.url.pathname)?.label ?? ''
	);

	onMount(() => {
		theme = resolveTheme(loadPreference());
		applyTheme(theme);
		void refreshMaximized();
		void lookForUpdate();
		// The settings page applies the theme too, so follow the attribute
		// rather than the local state.
		const observer = new MutationObserver(readAppliedTheme);
		observer.observe(document.documentElement, { attributeFilter: ['data-theme'] });
		const unwatch = watchSystemTheme((systemTheme) => {
			if (loadPreference() === 'system') {
				applyTheme(systemTheme);
			}
		});
		const unsubscribeSubNav = subscribeSubNav((value) => {
			subnav = value;
		});
		return () => {
			observer.disconnect();
			unwatch();
			unsubscribeSubNav();
		};
	});

	async function lookForUpdate(): Promise<void> {
		const state = await runCheck({
			check: checkForUpdate,
			install: installUpdate,
			onState: () => {}
		});
		updateNotice = startupNotice(state);
	}

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
	<TopBar {theme} {maximized} onToggleTheme={toggleTheme} onToggleMaximize={toggleMaximize} />

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

	{#if updateNotice !== ''}
		<p class="updatenote">
			{updateNotice}
			<a href="/settings#updates">Open the update settings</a>
		</p>
	{/if}

	<div class="body">
		{#if subnav.items.length > 0}
			<SubNav heading={sectionLabel} items={subnav.items} active={subnav.active} />
		{/if}
		<main class="content">{@render children()}</main>
	</div>
</div>

<style>
	.shell {
		display: flex;
		flex-direction: column;
		height: 100vh;
	}

	.mainnav {
		display: flex;
		align-items: center;
		gap: var(--space-1);
		height: var(--nav-height);
		padding: 0 var(--page-gutter, 32px);
		background: var(--color-surface);
		border-bottom: 1px solid var(--color-border);
	}

	.tab {
		display: inline-flex;
		align-items: center;
		height: 28px;
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

	.tab.is-active:hover {
		background: var(--color-accent-hover);
		color: var(--color-accent-fg);
	}

	.updatenote {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		margin: 0;
		padding: var(--space-2) var(--page-gutter, 32px);
		background: var(--color-accent-soft);
		border-bottom: 1px solid var(--color-accent-border);
		color: var(--color-text);
		font-size: var(--text-sm);
	}

	.body {
		display: flex;
		flex: 1;
		min-height: 0;
	}

	.content {
		flex: 1;
		min-width: 0;
		min-height: 0;
		overflow: auto;
	}
</style>
