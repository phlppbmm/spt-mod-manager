const STORAGE_KEY = 'sptmod.game_dir';

export function loadGameDir(): string | null {
	return localStorage.getItem(STORAGE_KEY);
}

export function saveGameDir(path: string): void {
	localStorage.setItem(STORAGE_KEY, path);
}

export function forgetGameDir(): void {
	localStorage.removeItem(STORAGE_KEY);
}
