import type { SptReleaseInfo, SptReleaseList } from './api/client';

export const DISMISSED_RELEASE_KEY = 'sptmod.dismissedSptRelease';

const INSTALLER_URL = 'https://sp-mod.com/installer';
const KNOWN_DATE_YEAR = 2000;

export type ReleaseState = 'installed' | 'newer' | 'older';

export type ChangeKind = 'update' | 'downgrade' | 'undo';

export interface ApplyCheck {
	ok: boolean;
	reason: string;
}

export interface Confirmation {
	title: string;
	body: string;
}

/**
 * Order two SPT versions by their numeric parts: -1, 0 or 1. A missing or
 * unreadable part counts as 0, so `4.1` sorts before `4.1.2`.
 */
export function compareVersions(a: string, b: string): number {
	const left = numbers(a);
	const right = numbers(b);
	const length = Math.max(left.length, right.length);
	for (let index = 0; index < length; index += 1) {
		const difference = (left[index] ?? 0) - (right[index] ?? 0);
		if (difference !== 0) {
			return difference < 0 ? -1 : 1;
		}
	}
	return 0;
}

export function releaseState(release: SptReleaseInfo, current: string): ReleaseState {
	const order = compareVersions(release.version, current);
	if (order === 0) {
		return 'installed';
	}
	return order > 0 ? 'newer' : 'older';
}

/**
 * SPT keeps a download only for the newest release of every line, so most
 * older releases can be shown but not installed.
 */
export function canApply(release: SptReleaseInfo, current: string): ApplyCheck {
	if (releaseState(release, current) === 'installed') {
		return { ok: false, reason: `SPT ${release.version} is already installed.` };
	}
	if (!release.downloadable) {
		return {
			ok: false,
			reason: `SPT no longer offers a download for ${release.version}. The official installer at ${INSTALLER_URL} can still change the game to it.`
		};
	}
	return { ok: true, reason: '' };
}

export function noticeFor(list: SptReleaseList, dismissedVersion: string | null): string | null {
	if (list.latest === '' || list.current_version === '') {
		return null;
	}
	if (compareVersions(list.latest, list.current_version) <= 0) {
		return null;
	}
	if (dismissedVersion === list.latest) {
		return null;
	}
	return `SPT ${list.latest} is out. You are on ${list.current_version}.`;
}

export function confirmationFor(
	release: SptReleaseInfo | null,
	current: string,
	kind: ChangeKind
): Confirmation {
	const currentName = current === '' ? 'the version you have now' : `SPT ${current}`;
	const target = release === null ? 'another SPT version' : `SPT ${release.version}`;
	const keeps = 'Your profiles, settings and installed mods stay where they are.';
	const safety = `sptmod secures the current state of your game folder first, so you can go back to ${currentName} at any time from this page.`;
	const warning = `Mods built for ${currentName} may stop working — check the Updates tab afterwards.`;
	if (kind === 'undo') {
		return {
			title: 'Undo the last SPT change?',
			body: `Your game folder goes back to how it was before the last SPT change. ${keeps} ${safety} ${warning}`
		};
	}
	return {
		title: kind === 'update' ? `Update to ${target}?` : `Go back to ${target}?`,
		body: `${keeps} ${safety} ${warning}`
	};
}

/**
 * Format the publication date of a release. Releases whose date is unknown
 * carry a placeholder far in the past; those show a dash instead of a date.
 */
export function formatReleaseDate(published: string): string {
	const date = new Date(published);
	if (date.getFullYear() < KNOWN_DATE_YEAR) {
		return '—';
	}
	return date.toLocaleDateString();
}

export function loadDismissedRelease(): string | null {
	try {
		return localStorage.getItem(DISMISSED_RELEASE_KEY);
	} catch {
		return null;
	}
}

export function saveDismissedRelease(version: string): void {
	try {
		localStorage.setItem(DISMISSED_RELEASE_KEY, version);
	} catch {
		return;
	}
}

function numbers(version: string): number[] {
	return version.split('.').map((part) => {
		const value = Number.parseInt(part, 10);
		return Number.isNaN(value) ? 0 : value;
	});
}
