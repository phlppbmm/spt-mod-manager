/**
 * Builders for the error shape the transport raises. Every field of
 * `ErrorDetails` is required, so a test that cares about one of them would
 * otherwise have to spell out all eleven.
 */

import { ApiError, type ErrorDetails } from '$lib/api/transport';

export function errorDetails(overrides: Partial<ErrorDetails> = {}): ErrorDetails {
	return {
		conflicts: [],
		residues: [],
		conflicting_paths: [],
		installed: [],
		name: '',
		candidates: [],
		dependency: '',
		reason: '',
		api_code: '',
		api_status: 0,
		missing: [],
		...overrides
	};
}

export function apiError(
	message: string,
	code: string,
	details?: Partial<ErrorDetails>
): ApiError {
	return new ApiError(message, code, details === undefined ? undefined : errorDetails(details));
}
