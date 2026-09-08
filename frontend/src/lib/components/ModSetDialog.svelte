<script lang="ts">
	import Dialog from './Dialog.svelte';

	interface Props {
		title: string;
		missing: string[];
		busy: boolean;
		onInstall: () => void;
		onIgnore: () => void;
		onclose: () => void;
	}

	let { title, missing, busy, onInstall, onIgnore, onclose }: Props = $props();
</script>

<Dialog {title} {onclose}>
	<p>This profile is set up with mods that are not installed:</p>
	<ul class="missing">
		{#each missing as name (name)}
			<li class="panel-row">{name}</li>
		{/each}
	</ul>
	<p class="note">
		Playing without them leaves the rest of the set turned on. Missing mods can change how a
		profile behaves.
	</p>

	{#snippet actions()}
		<button type="button" disabled={busy} onclick={onIgnore}>Play without them</button>
		<button type="button" class="btn-primary" disabled={busy} onclick={onInstall}>
			{busy ? 'Installing…' : 'Install the missing mods'}
		</button>
	{/snippet}
</Dialog>

<style>
	.missing {
		list-style: none;
		margin: var(--space-3) 0;
		padding: 0;
	}
</style>
