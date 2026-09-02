<script lang="ts">
	import { page } from '$app/state';
	import favicon from '$lib/assets/favicon.svg';

	const LINKS = [
		{ href: '/', label: 'Mods' },
		{ href: '/history', label: 'History' },
		{ href: '/profile', label: 'Profiles' },
		{ href: '/settings', label: 'Settings' }
	];

	let { children } = $props();
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

<nav>
	{#each LINKS as link (link.href)}
		<a href={link.href} aria-current={page.url.pathname === link.href ? 'page' : undefined}>
			{link.label}
		</a>
	{/each}
</nav>

{@render children()}

<style>
	nav {
		display: flex;
		gap: 1rem;
		padding: 0.5rem 1rem;
		border-bottom: 1px solid #2c3138;
	}

	nav a {
		text-decoration: none;
		color: #98a2ad;
	}

	nav a[aria-current='page'] {
		color: #e6eaef;
	}

	:global(body) {
		margin: 0;
		background: #15181d;
		color: #e6eaef;
		font-family:
			system-ui,
			-apple-system,
			'Segoe UI',
			sans-serif;
		font-size: 15px;
	}

	:global(button) {
		font: inherit;
		background: #2c3138;
		color: inherit;
		border: 1px solid #3a4048;
		border-radius: 4px;
		padding: 0.3rem 0.7rem;
		cursor: pointer;
	}

	:global(button:disabled) {
		opacity: 0.5;
		cursor: default;
	}

	:global(input[type='text']),
	:global(input[type='search']) {
		font: inherit;
		background: #1e2228;
		color: inherit;
		border: 1px solid #3a4048;
		border-radius: 4px;
		padding: 0.35rem 0.5rem;
	}

	:global(a) {
		color: #8fc7ff;
	}

	:global(code) {
		color: #b6bec8;
	}
</style>
