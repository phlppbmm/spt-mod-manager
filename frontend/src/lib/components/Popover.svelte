<script lang="ts">
	import type { Snippet } from 'svelte';
	import { popoverKey } from '$lib/popover';

	interface Props {
		anchor: HTMLElement | null;
		label: string;
		onclose: () => void;
		children: Snippet;
	}

	let { anchor, label, onclose, children }: Props = $props();

	let panel: HTMLDivElement | undefined = $state();

	const FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

	function focusables(): HTMLElement[] {
		if (!panel) {
			return [];
		}
		return [...panel.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
			(element) => !element.hasAttribute('disabled')
		);
	}

	let box = $derived(anchor?.getBoundingClientRect() ?? null);

	$effect(() => {
		focusables()[0]?.focus();
	});

	$effect(() => {
		function onPointerDown(event: PointerEvent): void {
			const target = event.target as Node;
			if (panel?.contains(target) || anchor?.contains(target)) {
				return;
			}
			onclose();
		}
		document.addEventListener('pointerdown', onPointerDown, true);
		return () => {
			document.removeEventListener('pointerdown', onPointerDown, true);
			const active = document.activeElement;
			if (active === null || active === document.body || panel?.contains(active)) {
				anchor?.focus();
			}
		};
	});

	function onKeydown(event: KeyboardEvent): void {
		const items = focusables();
		const result = popoverKey(event.key, {
			count: items.length,
			index: items.indexOf(document.activeElement as HTMLElement),
			shift: event.shiftKey
		});
		if (result.preventDefault) {
			event.preventDefault();
		}
		if (result.focusIndex !== null) {
			items[result.focusIndex]?.focus();
		}
		if (result.close) {
			event.stopPropagation();
			onclose();
		}
	}
</script>

<div
	class="popover"
	role="dialog"
	aria-label={label}
	tabindex="-1"
	bind:this={panel}
	onkeydown={onKeydown}
	style:top={`${(box?.bottom ?? 0) + 6}px`}
	style:left={`${box?.left ?? 0}px`}
>
	{@render children()}
</div>

<style>
	.popover {
		position: fixed;
		z-index: var(--z-popover, 60);
		min-width: 260px;
		padding: var(--space-3);
		background: var(--color-elevated);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-lg);
		box-shadow: var(--shadow-md);
	}
</style>
