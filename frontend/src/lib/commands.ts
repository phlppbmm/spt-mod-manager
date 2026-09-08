import { rank } from './fuzzy';
import type { Intent } from './intents';
import type { ResolvedTheme } from './theme';

export interface CommandProfile {
	id: string;
	label: string;
}

export interface CommandMod {
	id: number;
	name: string;
	author: string;
}

export interface CommandSources {
	pathname: string;
	gameDir: string | null;
	theme: ResolvedTheme;
	profiles: readonly CommandProfile[];
	mods: readonly CommandMod[];
}

export type CommandAction =
	| { kind: 'navigate'; href: string }
	| { kind: 'intent'; route: string; intent: Intent }
	| { kind: 'reveal'; modId: number }
	| { kind: 'clear-filters' }
	| { kind: 'toggle-theme' };

export type CommandGroup = 'Go to' | 'Actions' | 'Mods';

export interface Command {
	id: string;
	title: string;
	subtitle: string;
	group: CommandGroup;
	action: CommandAction;
}

const PAGES = [
	{ id: 'mods', title: 'Mods', href: '/' },
	{ id: 'profiles', title: 'Profiles', href: '/profile' },
	{ id: 'history', title: 'History', href: '/history' },
	{ id: 'settings', title: 'Settings', href: '/settings' }
];

function intentCommand(id: string, title: string, route: string, intent: Intent): Command {
	return { id, title, subtitle: '', group: 'Actions', action: { kind: 'intent', route, intent } };
}

export function buildCommands(sources: CommandSources): Command[] {
	const commands: Command[] = PAGES.map((page) => ({
		id: `go-${page.id}`,
		title: page.title,
		subtitle: '',
		group: 'Go to',
		action: { kind: 'navigate', href: page.href }
	}));
	if (sources.gameDir !== null) {
		commands.push(intentCommand('sync', 'Sync catalog', '/', { kind: 'sync' }));
		commands.push(intentCommand('update-all', 'Update all', '/', { kind: 'update-all' }));
		commands.push(intentCommand('snapshot', 'Snapshot', '/profile', { kind: 'snapshot' }));
		for (const profile of sources.profiles) {
			commands.push(
				intentCommand(`launch-${profile.id}`, `Launch ${profile.label}`, '/profile', {
					kind: 'launch',
					profileId: profile.id
				})
			);
		}
		commands.push(
			intentCommand('change-folder', 'Change folder', '/', { kind: 'change-folder' })
		);
	}
	commands.push({
		id: 'clear-filters',
		title: 'Clear filters',
		subtitle: '',
		group: 'Actions',
		action: { kind: 'clear-filters' }
	});
	commands.push({
		id: 'toggle-theme',
		title: sources.theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode',
		subtitle: '',
		group: 'Actions',
		action: { kind: 'toggle-theme' }
	});
	for (const mod of sources.mods) {
		commands.push({
			id: `mod-${mod.id}`,
			title: mod.name,
			subtitle: mod.author,
			group: 'Mods',
			action: { kind: 'reveal', modId: mod.id }
		});
	}
	return commands;
}

export function visibleCommands(
	query: string,
	sources: CommandSources,
	limit: number
): Command[] {
	return rank(
		query,
		buildCommands(sources),
		(command) => `${command.title} ${command.subtitle}`,
		limit
	);
}
