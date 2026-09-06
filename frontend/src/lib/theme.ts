export type ThemePreference = 'system' | 'light' | 'dark';
export type ResolvedTheme = 'light' | 'dark';

/** Paired with the literal in the pre-paint script of `src/app.html`. */
export const THEME_KEY = 'sptmod.theme';

const DARK_QUERY = '(prefers-color-scheme: dark)';

export function loadPreference(): ThemePreference {
	const stored = localStorage.getItem(THEME_KEY);
	if (stored === 'light' || stored === 'dark' || stored === 'system') {
		return stored;
	}
	return 'system';
}

export function savePreference(preference: ThemePreference): void {
	localStorage.setItem(THEME_KEY, preference);
}

export function systemTheme(): ResolvedTheme {
	return matchMedia(DARK_QUERY).matches ? 'dark' : 'light';
}

export function resolveTheme(preference: ThemePreference): ResolvedTheme {
	if (preference === 'system') {
		return systemTheme();
	}
	return preference;
}

export function applyTheme(theme: ResolvedTheme): void {
	document.documentElement.setAttribute('data-theme', theme);
}

export function toggledPreference(current: ResolvedTheme): ThemePreference {
	return current === 'dark' ? 'light' : 'dark';
}

export function watchSystemTheme(handler: (theme: ResolvedTheme) => void): () => void {
	const query = matchMedia(DARK_QUERY);
	const onChange = (event: MediaQueryListEvent): void => {
		handler(event.matches ? 'dark' : 'light');
	};
	query.addEventListener('change', onChange);
	return () => {
		query.removeEventListener('change', onChange);
	};
}
