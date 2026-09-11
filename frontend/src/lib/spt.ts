import type { SptCompatibilityReport, SptReleaseInfo, SptReleaseList } from './api/client';

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

export type ModCheck =
	| { kind: 'pending' }
	| { kind: 'failed' }
	| { kind: 'ready'; report: SptCompatibilityReport };

export interface ModCheckView {
	tone: 'note' | 'warn';
	message: string;
	incompatible: string[];
	skippedNote: string;
	needsOverride: boolean;
	pending: boolean;
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
 * A version change only has installed mods to judge when it goes to a known
 * release; undoing the last change has no target version to check against.
 */
export function needsModCheck(
	release: SptReleaseInfo | null,
	kind: ChangeKind
): release is SptReleaseInfo {
	return kind !== 'undo' && release !== null;
}

export function modCheckView(check: ModCheck): ModCheckView {
	if (check.kind === 'pending') {
		const note = view('note', 'Checking your installed mods against this version…');
		return { ...note, pending: true };
	}
	if (check.kind === 'failed') {
		return view(
			'note',
			'Your installed mods could not be checked against this version. The change can still go ahead.'
		);
	}
	return verdict(check.report);
}

/**
 * A verdict that is still being fetched holds the change back until it arrives,
 * and mods without a release for the target version hold it back until the
 * player says to go ahead anyway; nothing else gates it.
 */
export function confirmBlocked(check: ModCheckView | null, allowed: boolean): boolean {
	if (check === null) {
		return false;
	}
	return check.pending || (check.needsOverride && !allowed);
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

function view(
	tone: 'note' | 'warn',
	message: string,
	incompatible: string[] = [],
	skippedNote = '',
	needsOverride = false
): ModCheckView {
	return { tone, message, incompatible, skippedNote, needsOverride, pending: false };
}

function verdict(report: SptCompatibilityReport): ModCheckView {
	const target = `SPT ${report.spt_version}`;
	if (!report.checked) {
		const notice =
			report.notice === ''
				? 'Your installed mods could not be checked against this version.'
				: report.notice;
		return view('note', `${notice} The change can still go ahead.`);
	}
	const skippedNote = skippedSentence(report.skipped);
	if (report.incompatible.length > 0) {
		const message = `${modsPhrase(report.incompatible.length)} no release for ${target}:`;
		return view('warn', message, report.incompatible, skippedNote, true);
	}
	if (report.compatible.length === 0) {
		return view('note', `No installed mods to check against ${target}.`, [], skippedNote);
	}
	const message = `${modsPhrase(report.compatible.length)} a release for ${target}.`;
	return view('note', message, [], skippedNote);
}

function modsPhrase(count: number): string {
	return count === 1 ? '1 installed mod has' : `${count} installed mods have`;
}

function skippedSentence(skipped: string[]): string {
	if (skipped.length === 0) {
		return '';
	}
	const count = skipped.length === 1 ? '1 installed mod' : `${skipped.length} installed mods`;
	return `${count} could not be checked: ${skipped.join(', ')}.`;
}

function numbers(version: string): number[] {
	return version.split('.').map((part) => {
		const value = Number.parseInt(part, 10);
		return Number.isNaN(value) ? 0 : value;
	});
}
