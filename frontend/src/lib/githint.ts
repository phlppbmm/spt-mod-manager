export const GIT_MISSING_CODE = 'GIT_MISSING';
export const GIT_DOWNLOAD_URL = 'https://git-scm.com/downloads';

export interface GitMissingHint {
	url: string;
	label: string;
}

/**
 * The download link to offer beside the error message of `code`, or `null`
 * when the failure has nothing to do with a missing Git.
 */
export function gitMissingHint(code: string): GitMissingHint | null {
	if (code !== GIT_MISSING_CODE) {
		return null;
	}
	return { url: GIT_DOWNLOAD_URL, label: 'Download Git' };
}
