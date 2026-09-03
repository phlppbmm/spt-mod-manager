/**
 * The only module allowed to talk to the Tauri backend. Every other module
 * imports `call` and `subscribe` from here and stays transport-agnostic.
 */

import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';

import type { components } from './types';

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
	try {
		return await invoke<T>('rpc', { name, payload: payload ?? {} });
	} catch (cause) {
		if (isErrorResponse(cause)) {
			throw new ApiError(cause.error, cause.code, cause.details);
		}
		throw new ApiError(String(cause), 'unreachable');
	}
}

/**
 * Route the progress events of `operationId` to `handler` until the resolved
 * unsubscribe function is called.
 */
export function subscribe(
	operationId: string,
	handler: (event: ProgressEvent) => void
): Promise<() => void> {
	return listen<ProgressEvent>('progress', (message) => {
		if (message.payload.operation_id === operationId) {
			handler(message.payload);
		}
	});
}

function isErrorResponse(body: unknown): body is ErrorResponse {
	if (typeof body !== 'object' || body === null) {
		return false;
	}
	const candidate = body as Partial<ErrorResponse>;
	return typeof candidate.error === 'string' && typeof candidate.code === 'string';
}
