import { describe, expect, it } from 'vitest';
import { GIT_DOWNLOAD_URL, GIT_MISSING_CODE, gitMissingHint } from './githint';

/**
 * `githint.ts` is the pure mapping from an RPC error code to the download link
 * the setup screen offers next to the backend's message. The tests pin that
 * only `GIT_MISSING` produces a hint, so no unrelated failure ever tells a user
 * to install Git.
 */
describe('gitMissingHint', () => {
	it('returns the git-scm download link for the GIT_MISSING code', () => {
		expect(gitMissingHint(GIT_MISSING_CODE)).toEqual({
			url: GIT_DOWNLOAD_URL,
			label: 'Download Git'
		});
	});

	it('points at the official downloads page', () => {
		expect(GIT_DOWNLOAD_URL).toBe('https://git-scm.com/downloads');
	});

	it.each([
		['NOT_A_GAME_DIR', 'a different typed error'],
		['GIT_ERROR', 'a failing git call, which needs no install hint'],
		['', 'an empty code'],
		['git_missing', 'the code in the wrong case']
	])('returns null for %s (%s)', (code) => {
		expect(gitMissingHint(code)).toBeNull();
	});
});
