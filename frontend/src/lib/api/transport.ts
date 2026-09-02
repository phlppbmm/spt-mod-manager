/**
 * The only module allowed to use `fetch` or `EventSource`. Under Tauri the
 * bodies of `call` and `subscribe` become `invoke` and Tauri events; every
 * other module keeps importing them unchanged.
 */

import type { components } from './types';

export const API_BASE_URL = 'http://127.0.0.1:8787';

export type ProgressEvent = components['schemas']['ProgressEvent'];
export type ErrorDetails = components['schemas']['ErrorDetails'];
type ErrorResponse = components['schemas']['ErrorResponse'];

export class ApiError extends Error {
	readonly code: string;
	readonly details: ErrorDetails | undefined;

	constructor(message: string, code: string, details?: ErrorDetails) {
		super(message);
		this.name = 'ApiError';
		this.code = code;
		this.details = details;
	}
}

export async function call<T>(name: string, payload?: unknown): Promise<T> {
	let response: Response;
	try {
		response = await fetch(`${API_BASE_URL}/api/${name}`, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify(payload ?? {})
		});
	} catch (cause) {
		const reason = cause instanceof Error ? cause.message : String(cause);
		throw new ApiError(
			`Cannot reach the sptmod server at ${API_BASE_URL}: ${reason}`,
			'unreachable'
		);
	}
	if (!response.ok) {
		throw await errorOf(response);
	}
	return (await response.json()) as T;
}

/**
 * Route the progress events of `operationId` to `handler` until the resolved
 * unsubscribe function is called. The promise is awaited by callers because
 * the Tauri `listen()` of stage 2 is async; the SSE implementation resolves
 * it immediately, once the source is subscribed.
 */
export function subscribe(
	operationId: string,
	handler: (event: ProgressEvent) => void
): Promise<() => void> {
	const source = new EventSource(`${API_BASE_URL}/api/events`);
	const listener = (message: MessageEvent<string>): void => {
		const event = JSON.parse(message.data) as ProgressEvent;
		if (event.operation_id === operationId) {
			handler(event);
		}
	};
	source.addEventListener('message', listener);
	return Promise.resolve(() => {
		source.removeEventListener('message', listener);
		source.close();
	});
}

async function errorOf(response: Response): Promise<ApiError> {
	let body: unknown;
	try {
		body = await response.json();
	} catch {
		return new ApiError(
			`The sptmod server answered ${response.status} without an error body.`,
			`http_${response.status}`
		);
	}
	if (!isErrorResponse(body)) {
		return new ApiError(
			`The sptmod server answered ${response.status} with an unexpected body.`,
			`http_${response.status}`
		);
	}
	return new ApiError(body.error, body.code, body.details);
}

function isErrorResponse(body: unknown): body is ErrorResponse {
	if (typeof body !== 'object' || body === null) {
		return false;
	}
	const candidate = body as Partial<ErrorResponse>;
	return typeof candidate.error === 'string' && typeof candidate.code === 'string';
}
