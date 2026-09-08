<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { requestIntent } from '$lib/intents';

	interface Props {
		path: string | null;
	}

	let { path }: Props = $props();

	let open = $state(false);
	let root: HTMLDivElement | undefined = $state();
	let trigger: HTMLButtonElement | undefined = $state();

	const label = $derived(
		path === null ? 'No game folder' : (path.split(/[/\\]/).filter(Boolean).pop() ?? path)
	);

	$effect(() => {
		if (!open) {
			return;
		}
		const onPointerDown = (event: PointerEvent): void => {
			if (!(event.target instanceof Node) || root === undefined || !root.contains(event.target)) {
				open = false;
			}
		};
		const onKeyDown = (event: KeyboardEvent): void => {
			if (event.key === 'Escape') {
				close();
			}
		};
		window.addEventListener('pointerdown', onPointerDown);
		window.addEventListener('keydown', onKeyDown);
		return () => {
			window.removeEventListener('pointerdown', onPointerDown);
			window.removeEventListener('keydown', onKeyDown);
		};
	});

	function close(): void {
		open = false;
		trigger?.focus();
	}

	async function changeFolder(): Promise<void> {
		close();
		if (page.url.pathname !== '/') {
			await goto('/');
		}
		requestIntent({ kind: 'change-folder' });
	}
</script>

<div class="gamedir" bind:this={root}>
	<button
		class="trigger"
		type="button"
		aria-haspopup="dialog"
		aria-expanded={open}
		title={path ?? 'No game folder'}
		bind:this={trigger}
		onclick={() => (open = !open)}
	>
		<span class="label">{label}</span>
		<svg
			width="10"
			height="10"
			viewBox="0 0 10 10"
			fill="none"
			stroke="currentColor"
			stroke-width="1.3"
			stroke-linecap="round"
			stroke-linejoin="round"
			aria-hidden="true"
		>
			<path d="M2.5 4 5 6.5 7.5 4" />
		</svg>
	</button>

	{#if open}
		<div class="popover" role="dialog" aria-label="Game folder">
			<div class="path">{path ?? 'No game folder selected'}</div>
			<button class="btn-ghost btn-sm" type="button" onclick={() => void changeFolder()}>
				Change folder
			</button>
		</div>
	{/if}
</div>

<style>
	.gamedir {
		position: relative;
		display: flex;
		align-items: center;
	}

	.trigger {
		height: var(--control-height-sm);
		max-width: 220px;
		padding: 0 var(--space-2);
		gap: var(--space-1);
		background: transparent;
		border: 1px solid transparent;
		border-radius: var(--radius-md);
		color: var(--color-text-muted);
		font-size: var(--text-sm);
	}

	.trigger:hover:not(:disabled) {
		background: var(--color-hover);
		color: var(--color-text);
	}

	.label {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.popover {
		position: absolute;
		top: calc(100% + var(--space-2));
		left: 0;
		z-index: var(--z-popover, 60);
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: var(--space-3);
		min-width: 240px;
		max-width: 420px;
		padding: var(--space-3);
		background: var(--color-elevated);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-lg);
		box-shadow: var(--shadow-md);
	}

	.path {
		color: var(--color-text-muted);
		font-family: var(--font-mono);
		font-size: var(--text-sm);
		line-height: var(--leading-normal);
		overflow-wrap: anywhere;
	}
</style>
