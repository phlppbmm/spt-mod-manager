import type { ProgressEvent } from '$lib/api/transport';

type Transport = typeof import('$lib/api/transport');
type Responder = (payload: unknown) => unknown;
type ProgressHandler = (event: ProgressEvent) => void;

// The responder map can only hold `unknown` results, while `call` has to honour
// the caller's `T`. The overload hands that conversion to a single named place.
function asResult<T>(value: unknown): T;
function asResult(value: unknown): unknown {
	return value;
}

const responders = new Map<string, Responder>();
const invocations: { name: string; payload: unknown }[] = [];
const subscriptions = new Map<string, Set<ProgressHandler>>();

function subscribe(operationId: string, handler: ProgressHandler): Promise<() => void> {
	const handlers = subscriptions.get(operationId) ?? new Set<ProgressHandler>();
	handlers.add(handler);
	subscriptions.set(operationId, handlers);
	return Promise.resolve(() => {
		handlers.delete(handler);
		if (handlers.size === 0) {
			subscriptions.delete(operationId);
		}
	});
}

export const rpc = {
	returns(name: string, result: unknown): void {
		responders.set(name, () => result);
	},

	rejects(name: string, error: unknown): void {
		responders.set(name, () => {
			throw error;
		});
	},

	handle(name: string, responder: Responder): void {
		responders.set(name, responder);
	},

	/** The payloads of every call to `name`, in call order. */
	payloads(name: string): unknown[] {
		return invocations.filter((entry) => entry.name === name).map((entry) => entry.payload);
	},

	/** The names of every call so far, in call order. */
	names(): string[] {
		return invocations.map((entry) => entry.name);
	},

	/** Deliver a progress event to the handlers subscribed to its operation. */
	progress(event: ProgressEvent): void {
		for (const handler of [...(subscriptions.get(event.operation_id) ?? [])]) {
			handler(event);
		}
	},

	subscribed(operationId: string): boolean {
		return subscriptions.has(operationId);
	},

	reset(): void {
		responders.clear();
		invocations.length = 0;
		subscriptions.clear();
	}
};

/**
 * The mocked transport module: `ApiError` stays the real class, the two I/O
 * functions do not. `call` closes over it instead of importing it, because a
 * top-level import of the module under mock deadlocks the mock factory.
 */
export function transportMock(original: Transport): Transport {
	async function call<T>(name: string, payload?: unknown): Promise<T> {
		invocations.push({ name, payload });
		const responder = responders.get(name);
		if (responder === undefined) {
			throw new original.ApiError(`no canned result for RPC "${name}"`, 'test_missing_stub');
		}
		return asResult(await responder(payload));
	}

	return { ...original, call, subscribe };
}
