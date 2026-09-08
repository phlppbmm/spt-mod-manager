import type { CommandMod } from './commands';

export interface SubNavItem {
	id: string;
	label: string;
}

export interface SubNavState {
	items: SubNavItem[];
	active: string | null;
}

interface Store<T> {
	get: () => T;
	set: (value: T) => void;
	subscribe: (fn: (value: T) => void) => () => void;
}

function store<T>(initial: T): Store<T> {
	let current = initial;
	const listeners = new Set<(value: T) => void>();
	return {
		get: () => current,
		set: (value: T) => {
			current = value;
			for (const listener of [...listeners]) {
				listener(value);
			}
		},
		subscribe: (fn: (value: T) => void) => {
			listeners.add(fn);
			fn(current);
			return () => {
				listeners.delete(fn);
			};
		}
	};
}

interface Signal<A extends unknown[]> {
	emit: (...args: A) => void;
	on: (fn: (...args: A) => void) => () => void;
}

function signal<A extends unknown[]>(): Signal<A> {
	const listeners = new Set<(...args: A) => void>();
	return {
		emit: (...args: A) => {
			for (const listener of [...listeners]) {
				listener(...args);
			}
		},
		on: (fn: (...args: A) => void) => {
			listeners.add(fn);
			return () => {
				listeners.delete(fn);
			};
		}
	};
}

const subNav = store<SubNavState>({ items: [], active: null });
const searchEnabled = store(false);
const gameDir = store<string | null>(null);
const commandMods = store<readonly CommandMod[]>([]);
const subNavSelect = signal<[string]>();

function copySubNav(value: SubNavState): SubNavState {
	return { items: [...value.items], active: value.active };
}

export function setSubNav(items: SubNavItem[], active: string | null): void {
	subNav.set({ items: [...items], active });
}

export function getSubNav(): SubNavState {
	return copySubNav(subNav.get());
}

export function subscribeSubNav(fn: (value: SubNavState) => void): () => void {
	return subNav.subscribe((value) => fn(copySubNav(value)));
}

export function selectSubNav(id: string): void {
	subNavSelect.emit(id);
}

export function onSubNavSelect(fn: (id: string) => void): () => void {
	return subNavSelect.on(fn);
}

export function setSearchEnabled(on: boolean): void {
	searchEnabled.set(on);
}

export function subscribeSearchEnabled(fn: (on: boolean) => void): () => void {
	return searchEnabled.subscribe(fn);
}

export function setGameDir(path: string | null): void {
	gameDir.set(path);
}

export function subscribeGameDir(fn: (path: string | null) => void): () => void {
	return gameDir.subscribe(fn);
}

export function setCommandMods(mods: readonly CommandMod[]): void {
	commandMods.set([...mods]);
}

export function subscribeCommandMods(fn: (mods: readonly CommandMod[]) => void): () => void {
	return commandMods.subscribe(fn);
}
