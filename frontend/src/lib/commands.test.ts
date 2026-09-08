import { describe, expect, it } from 'vitest';
import { buildCommands, visibleCommands } from './commands';
import type { CommandSources } from './commands';

function makeSources(overrides: Partial<CommandSources> = {}): CommandSources {
	return {
		pathname: '/',
		gameDir: null,
		theme: 'light',
		profiles: [],
		mods: [],
		...overrides
	};
}

const NAV_TITLES = ['Mods', 'Profiles', 'History', 'Settings'];

describe('buildCommands', () => {
	it('always includes the four "Go to" navigation entries in a fixed order', () => {
		const commands = buildCommands(makeSources());

		const navCommands = commands.filter((command) => command.group === 'Go to');
		expect(navCommands.map((command) => command.title)).toEqual(NAV_TITLES);
		expect(navCommands.map((command) => command.action)).toEqual([
			{ kind: 'navigate', href: '/' },
			{ kind: 'navigate', href: '/profile' },
			{ kind: 'navigate', href: '/history' },
			{ kind: 'navigate', href: '/settings' }
		]);
	});

	it('omits sync, update all, snapshot, launch and change folder when gameDir is null', () => {
		const commands = buildCommands(
			makeSources({ gameDir: null, profiles: [{ id: 'p1', label: 'Alice' }] })
		);

		expect(commands.map((command) => command.id)).not.toContain('sync');
		expect(commands.map((command) => command.id)).not.toContain('update-all');
		expect(commands.map((command) => command.id)).not.toContain('snapshot');
		expect(commands.map((command) => command.id)).not.toContain('change-folder');
		expect(commands.map((command) => command.id)).not.toContain('launch-p1');
	});

	it('includes sync, update all, snapshot and change folder when gameDir is set', () => {
		const commands = buildCommands(makeSources({ gameDir: '/games/foo' }));

		const byId = (id: string): unknown =>
			commands.find((command) => command.id === id);

		expect(byId('sync')).toEqual({
			id: 'sync',
			title: 'Sync catalog',
			subtitle: '',
			group: 'Actions',
			action: { kind: 'intent', route: '/', intent: { kind: 'sync' } }
		});
		expect(byId('update-all')).toEqual({
			id: 'update-all',
			title: 'Update all',
			subtitle: '',
			group: 'Actions',
			action: { kind: 'intent', route: '/', intent: { kind: 'update-all' } }
		});
		expect(byId('snapshot')).toEqual({
			id: 'snapshot',
			title: 'Snapshot',
			subtitle: '',
			group: 'Actions',
			action: { kind: 'intent', route: '/profile', intent: { kind: 'snapshot' } }
		});
		expect(byId('change-folder')).toEqual({
			id: 'change-folder',
			title: 'Change folder',
			subtitle: '',
			group: 'Actions',
			action: { kind: 'intent', route: '/', intent: { kind: 'change-folder' } }
		});
	});

	it('adds one "Launch <label>" command per profile, carrying the profile id', () => {
		const commands = buildCommands(
			makeSources({
				gameDir: '/games/foo',
				profiles: [
					{ id: 'p1', label: 'Alice' },
					{ id: 'p2', label: 'Bob' }
				]
			})
		);

		const launches = commands.filter((command) => command.id.startsWith('launch-'));
		expect(launches).toEqual([
			{
				id: 'launch-p1',
				title: 'Launch Alice',
				subtitle: '',
				group: 'Actions',
				action: { kind: 'intent', route: '/profile', intent: { kind: 'launch', profileId: 'p1' } }
			},
			{
				id: 'launch-p2',
				title: 'Launch Bob',
				subtitle: '',
				group: 'Actions',
				action: { kind: 'intent', route: '/profile', intent: { kind: 'launch', profileId: 'p2' } }
			}
		]);
	});

	it('produces no launch commands when there are no profiles, even with a game dir set', () => {
		const commands = buildCommands(makeSources({ gameDir: '/games/foo', profiles: [] }));

		expect(commands.filter((command) => command.id.startsWith('launch-'))).toEqual([]);
	});

	it('always includes clear filters and toggle theme', () => {
		const commands = buildCommands(makeSources());

		expect(commands.find((command) => command.id === 'clear-filters')).toEqual({
			id: 'clear-filters',
			title: 'Clear filters',
			subtitle: '',
			group: 'Actions',
			action: { kind: 'clear-filters' }
		});
		expect(commands.find((command) => command.id === 'toggle-theme')?.action).toEqual({
			kind: 'toggle-theme'
		});
	});

	it.each<['light' | 'dark', string]>([
		['dark', 'Switch to light mode'],
		['light', 'Switch to dark mode']
	])('titles the toggle-theme command for the current theme %s', (theme, expectedTitle) => {
		const commands = buildCommands(makeSources({ theme }));

		expect(commands.find((command) => command.id === 'toggle-theme')?.title).toBe(expectedTitle);
	});

	it('adds one command per mod carrying id, name as title and author as subtitle', () => {
		const commands = buildCommands(
			makeSources({
				mods: [
					{ id: 7, name: 'Amazing Trader', author: 'Kobra' },
					{ id: 9, name: 'Better Graphics', author: 'Vex' }
				]
			})
		);

		const modCommands = commands.filter((command) => command.group === 'Mods');
		expect(modCommands).toEqual([
			{
				id: 'mod-7',
				title: 'Amazing Trader',
				subtitle: 'Kobra',
				group: 'Mods',
				action: { kind: 'reveal', modId: 7 }
			},
			{
				id: 'mod-9',
				title: 'Better Graphics',
				subtitle: 'Vex',
				group: 'Mods',
				action: { kind: 'reveal', modId: 9 }
			}
		]);
	});

	it('returns no mod commands when there are no mods', () => {
		const commands = buildCommands(makeSources({ mods: [] }));

		expect(commands.filter((command) => command.group === 'Mods')).toEqual([]);
	});
});

describe('visibleCommands', () => {
	it('returns the full registry in build order for an empty query, honouring the limit', () => {
		const sources = makeSources({
			mods: [{ id: 1, name: 'Zeta Mod', author: 'Someone' }]
		});
		const all = buildCommands(sources);

		expect(visibleCommands('', sources, 100)).toEqual(all);
	});

	it('filters out commands whose title and subtitle do not match the query', () => {
		const sources = makeSources({
			mods: [
				{ id: 1, name: 'Weapon Overhaul', author: 'Someone' },
				{ id: 2, name: 'Better Graphics', author: 'Vex' }
			]
		});

		const results = visibleCommands('weapon', sources, 10);

		expect(results.map((command) => command.id)).toEqual(['mod-1']);
	});

	it('matches against the subtitle as well as the title', () => {
		const sources = makeSources({
			mods: [{ id: 3, name: 'Unrelated Name', author: 'Kobra' }]
		});

		const results = visibleCommands('kobra', sources, 10);

		expect(results.map((command) => command.id)).toEqual(['mod-3']);
	});

	it('orders matches by score, best match first', () => {
		const sources = makeSources({
			mods: [
				{ id: 1, name: 'xProfilesx', author: '' },
				{ id: 2, name: 'Profiles Plus', author: '' }
			]
		});

		const results = visibleCommands('profiles', sources, 10);

		expect(results.map((command) => command.id)).toEqual(['go-profiles', 'mod-2', 'mod-1']);
	});

	it('truncates the result to the given limit', () => {
		const sources = makeSources({
			mods: [
				{ id: 1, name: 'Alpha', author: '' },
				{ id: 2, name: 'Alberta', author: '' },
				{ id: 3, name: 'Albino', author: '' }
			]
		});

		expect(visibleCommands('al', sources, 2)).toHaveLength(2);
	});
});
