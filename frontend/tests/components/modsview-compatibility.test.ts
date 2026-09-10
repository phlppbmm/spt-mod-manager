/**
 * Component tests for the Compatibility filter of the mod browser.
 *
 * Setup: `ModsView` is mounted with a canned catalog and canned SPT releases,
 * and the pill is used the way a player uses it. Assertions read the options the
 * pill offers, the pill's own value and the `browse_catalog` payloads. Reason:
 * judging the catalog against another SPT version is not a row predicate — the
 * backend resolves the latest compatible version per entry, so the choice only
 * has an effect if it reaches the RPC, and only the mounted view shows that.
 */

import { cleanup, render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { CatalogEntry, ModInfo, SptReleaseInfo } from '$lib/api/client';
import { clearFilter, setFilter } from '$lib/filterstate';
import { setSubNav } from '$lib/shellstate';
import type { UpdateTarget } from '$lib/updateall';
import { invalidate } from '$lib/viewcache';

import ModsView from '$lib/components/ModsView.svelte';
import { apiError } from '../support/errors';
import {
	catalogEntry,
	catalogMod,
	catalogVersion,
	modInfo,
	sptReleaseList
} from '../support/factories';
import { rpc } from '../support/rpc';

vi.mock('$lib/api/transport', async (importOriginal) => {
	const { transportMock } = await import('../support/rpc');
	return transportMock(await importOriginal<typeof import('$lib/api/transport')>());
});

const GAME_DIR = '/games/SPT';
const INSTALLED_SPT = '4.1.0';

const alpha = catalogEntry({ mod: catalogMod({ id: 1, name: 'Alpha', author: 'Ada' }) });

/** A release row; only version and downloadable steer the pill. */
function release(overrides: Partial<SptReleaseInfo> = {}): SptReleaseInfo {
	return {
		version: '4.1.5',
		client_build: '39000',
		published_at: '2026-02-01T00:00:00Z',
		url: 'https://sp-tarkov.com/releases/4.1.5',
		downloadable: true,
		content_length: 1024,
		installed: false,
		newer: true,
		...overrides
	};
}

function stubView(
	releases: SptReleaseInfo[] | 'unreachable',
	catalog: CatalogEntry[] = [alpha],
	mods: ModInfo[] = []
): void {
	rpc.returns('browse_catalog', catalog);
	rpc.returns('list_mods', mods);
	rpc.returns('list_favorites', { mod_ids: [] });
	if (releases === 'unreachable') {
		rpc.rejects('list_spt_releases', apiError('The release source is unreachable.', 'NETWORK'));
	} else {
		rpc.returns('list_spt_releases', sptReleaseList({ releases }));
	}
}

interface Mounted {
	unmount: () => void;
	/** Every list of update targets the view reported, in call order. */
	updatable: UpdateTarget[][];
}

function mount(): Mounted {
	const updatable: UpdateTarget[][] = [];
	const { unmount } = render(ModsView, {
		props: {
			path: GAME_DIR,
			refreshKey: 0,
			locked: false,
			sptVersion: INSTALLED_SPT,
			onbusy: () => undefined,
			onupdatable: (targets: UpdateTarget[]) => updatable.push(targets)
		}
	});
	return { unmount, updatable };
}

/** Judges the shared filter against `version`, as a previous visit would leave it. */
function judgeAgainst(version: string): void {
	setFilter({ chips: new Set(['compatible']), category: null, search: '', sptVersion: version });
}

/** Opens the Compatibility pill and returns its popover. */
async function openCompatibility(
	user: ReturnType<typeof userEvent.setup>,
	value = `SPT ${INSTALLED_SPT}`
): Promise<HTMLElement> {
	await user.click(await screen.findByRole('button', { name: value }));
	return screen.findByRole('dialog', { name: 'Filter by Compatibility' });
}

/** The option labels the pill offers, in the order it lists them. */
function optionLabels(popover: HTMLElement): string[] {
	return within(popover)
		.getAllByRole('radio')
		.map((radio) => radio.closest('label')?.querySelector('.option-label')?.textContent ?? '');
}

/** Picks one option of an open pill and applies it. */
async function pick(
	user: ReturnType<typeof userEvent.setup>,
	popover: HTMLElement,
	label: string
): Promise<void> {
	await user.click(within(popover).getByRole('radio', { name: new RegExp(`^${label}\\b`) }));
	await user.click(within(popover).getByRole('button', { name: 'Apply' }));
}

/**
 * The quick-tab strip. `Installed` is also the label of a sort column, so the
 * tabs have to be addressed inside their own strip.
 */
function quickTabs(): HTMLElement {
	const strip = screen.getByRole('button', { name: 'Favorites' }).parentElement;
	if (strip === null) {
		throw new Error('the quick tabs are not rendered');
	}
	return strip;
}

/** The SPT version each `browse_catalog` call was judged against, in call order. */
function judgedVersions(): (string | undefined)[] {
	return rpc
		.payloads('browse_catalog')
		.map((payload) => (payload as { spt_version?: string }).spt_version);
}

afterEach(() => {
	// Unmount before the shared filter is reset: the view listens to it, and a
	// still-mounted view would re-browse into the next test's recorded calls.
	cleanup();
	rpc.reset();
	invalidate(GAME_DIR);
	clearFilter();
	setSubNav([], null);
});

describe('the versions the Compatibility pill offers', () => {
	it('lists the installed version first, then every installable release, then "All versions"', async () => {
		const user = userEvent.setup();
		stubView([
			release({ version: '4.1.5' }),
			release({ version: '4.1.3' }),
			release({ version: '4.1.0' })
		]);
		mount();

		const popover = await openCompatibility(user);

		expect(optionLabels(popover)).toEqual(['SPT 4.1.0', 'SPT 4.1.5', 'SPT 4.1.3', 'All versions']);
		expect(rpc.payloads('list_spt_releases')).toEqual([{ path: GAME_DIR }]);
	});

	it('leaves out a release the manager cannot install', async () => {
		const user = userEvent.setup();
		stubView([release({ version: '4.1.5', downloadable: false }), release({ version: '4.1.3' })]);
		mount();

		const popover = await openCompatibility(user);

		expect(optionLabels(popover)).toEqual(['SPT 4.1.0', 'SPT 4.1.3', 'All versions']);
	});

	it.each([
		{ source: 'the release source is unreachable', releases: 'unreachable' as const },
		{ source: 'nothing is published', releases: [] }
	])('keeps the two known options and the loaded catalog when $source', async ({ releases }) => {
		const user = userEvent.setup();
		stubView(releases);
		mount();

		expect(await screen.findByLabelText('Select Alpha')).toBeInTheDocument();
		const popover = await openCompatibility(user);

		expect(optionLabels(popover)).toEqual(['SPT 4.1.0', 'All versions']);
		expect(screen.queryByText('The release source is unreachable.')).toBeNull();
	});
});

describe('judging the catalog against another version', () => {
	it('re-browses the catalog for the picked version, which the first request did not name', async () => {
		const user = userEvent.setup();
		stubView([release({ version: '4.1.5' })]);
		mount();

		await screen.findByLabelText('Select Alpha');
		await pick(user, await openCompatibility(user), 'SPT 4.1.5');

		await vi.waitFor(() => {
			expect(judgedVersions()).toEqual([undefined, '4.1.5']);
		});
	});

	it('shows the picked version as the value of the Compatibility pill', async () => {
		const user = userEvent.setup();
		stubView([release({ version: '4.1.5' })]);
		mount();

		await screen.findByLabelText('Select Alpha');
		await pick(user, await openCompatibility(user), 'SPT 4.1.5');

		expect(await screen.findByRole('button', { name: 'SPT 4.1.5' })).toBeInTheDocument();
	});

	it('keeps the pill on the bar when the picked version is not the last one offered', async () => {
		const user = userEvent.setup();
		stubView([release({ version: '4.1.5' }), release({ version: '4.1.3' })]);
		mount();

		await screen.findByLabelText('Select Alpha');
		await pick(user, await openCompatibility(user), 'SPT 4.1.5');

		expect(await screen.findByRole('button', { name: 'SPT 4.1.5' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Remove Compatibility filter' })).toBeInTheDocument();
	});

	it('keeps judging against the picked version across a quick-tab switch', async () => {
		const user = userEvent.setup();
		stubView([release({ version: '4.1.5' })]);
		mount();

		await screen.findByLabelText('Select Alpha');
		await pick(user, await openCompatibility(user), 'SPT 4.1.5');
		await vi.waitFor(() => {
			expect(judgedVersions()).toEqual([undefined, '4.1.5']);
		});

		await user.click(within(quickTabs()).getByRole('button', { name: 'Installed' }));

		expect(judgedVersions()).toEqual([undefined, '4.1.5']);
	});

	it('still judges against the picked version when the view is left and entered again', async () => {
		const user = userEvent.setup();
		stubView([release({ version: '4.1.5' })]);
		const first = mount();

		await screen.findByLabelText('Select Alpha');
		await pick(user, await openCompatibility(user), 'SPT 4.1.5');
		await vi.waitFor(() => {
			expect(judgedVersions()).toEqual([undefined, '4.1.5']);
		});

		first.unmount();
		mount();
		await screen.findByLabelText('Select Alpha');

		expect(judgedVersions().at(-1)).toBe('4.1.5');
	});

	it('drops the compatibility filter entirely when "All versions" is picked', async () => {
		const user = userEvent.setup();
		stubView([release({ version: '4.1.5' })]);
		mount();

		await screen.findByLabelText('Select Alpha');
		await pick(user, await openCompatibility(user), 'All versions');

		expect(screen.queryByRole('button', { name: 'Remove Compatibility filter' })).toBeNull();
		expect(judgedVersions()).toEqual([undefined]);
	});
});

describe('what the pill shows next to its options', () => {
	it('counts the rows for the installed version only, never for a previewed one', async () => {
		const user = userEvent.setup();
		stubView([release({ version: '4.1.5' })]);
		mount();

		await screen.findByLabelText('Select Alpha');
		const popover = await openCompatibility(user);

		expect(within(popover).getByRole('radio', { name: 'SPT 4.1.0 1' })).toBeInTheDocument();
		expect(within(popover).getByRole('radio', { name: 'SPT 4.1.5' })).toBeInTheDocument();
		expect(within(popover).getByRole('radio', { name: 'All versions' })).toBeInTheDocument();
	});
});

describe('a version the game directory cannot install', () => {
	it('stops judging against a version this directory does not offer', async () => {
		judgeAgainst('4.1.9');
		stubView([release({ version: '4.1.5' })]);
		mount();

		await vi.waitFor(() => {
			expect(judgedVersions()).toEqual(['4.1.9', undefined]);
		});
		expect(await screen.findByRole('button', { name: `SPT ${INSTALLED_SPT}` })).toBeInTheDocument();
	});

	it('keeps the choice while no release list could be loaded at all', async () => {
		judgeAgainst('4.1.5');
		stubView([]);
		mount();

		await screen.findByLabelText('Select Alpha');
		await vi.waitFor(() => {
			expect(rpc.payloads('list_spt_releases')).toEqual([{ path: GAME_DIR }]);
		});

		expect(judgedVersions()).toEqual(['4.1.5']);
	});
});

describe('the update targets the view reports', () => {
	const gamma = catalogEntry({
		mod: catalogMod({ id: 3, name: 'Gamma' }),
		state: 'update_available',
		installed_version: '1.0.0',
		latest_version: catalogVersion({ mod_id: 3, version: '2.0.0' })
	});
	const installedGamma = modInfo({ name: 'gamma-trailer', forge_id: '3', state: 'installed' });

	/** The view with one updatable mod and one other installable SPT version. */
	function stubViewAndMount(): Mounted {
		stubView([release({ version: '4.1.5' })], [gamma], [installedGamma]);
		return mount();
	}

	it('names the mod with a newer version while the installed version judges the list', async () => {
		const recorded = stubViewAndMount();

		await screen.findByLabelText('Select Gamma');

		expect(recorded.updatable.at(-1)).toEqual([
			{ name: 'gamma-trailer', installedVersion: '1.0.0', newVersion: '2.0.0' }
		]);
	});

	it('names none while another version judges the list, because that list is a preview', async () => {
		const user = userEvent.setup();
		const recorded = stubViewAndMount();
		await screen.findByLabelText('Select Gamma');

		await pick(user, await openCompatibility(user), 'SPT 4.1.5');

		await vi.waitFor(() => {
			expect(recorded.updatable.at(-1)).toEqual([]);
		});
	});
});

describe('loading the release list', () => {
	it('offers the versions it already knows when the release source has gone away', async () => {
		const user = userEvent.setup();
		stubView([release({ version: '4.1.5' })]);
		const first = mount();
		await screen.findByLabelText('Select Alpha');
		await vi.waitFor(() => {
			expect(rpc.payloads('list_spt_releases')).toEqual([{ path: GAME_DIR }]);
		});

		first.unmount();
		rpc.rejects('list_spt_releases', apiError('The release source is unreachable.', 'NETWORK'));
		mount();

		const popover = await openCompatibility(user);
		expect(optionLabels(popover)).toEqual(['SPT 4.1.0', 'SPT 4.1.5', 'All versions']);
	});

	it('does not ask the release source again when the filter changes', async () => {
		const user = userEvent.setup();
		stubView([release({ version: '4.1.5' })]);
		mount();
		await screen.findByLabelText('Select Alpha');
		await vi.waitFor(() => {
			expect(rpc.payloads('list_spt_releases')).toHaveLength(1);
		});

		await user.click(within(quickTabs()).getByRole('button', { name: 'Installed' }));
		await pick(user, await openCompatibility(user), 'SPT 4.1.5');

		expect(rpc.payloads('list_spt_releases')).toHaveLength(1);
	});
});

