import { beforeEach, describe, expect, it, vi } from 'vitest';
import { onIntents, requestIntent, takeIntents } from './intents';
import type { IntentKind } from './intents';

const ALL_KINDS: IntentKind[] = [
	'sync',
	'update-all',
	'change-folder',
	'snapshot',
	'launch',
	'reveal-mod'
];

beforeEach(() => {
	takeIntents(ALL_KINDS);
});

describe('requestIntent / takeIntents', () => {
	it('a requested intent is returned once by takeIntents and then gone', () => {
		requestIntent({ kind: 'sync' });

		expect(takeIntents(['sync'])).toEqual([{ kind: 'sync' }]);
		expect(takeIntents(['sync'])).toEqual([]);
	});

	it('a newer request of the same kind replaces the older one', () => {
		requestIntent({ kind: 'launch', profileId: 'alice' });
		requestIntent({ kind: 'launch', profileId: 'bob' });

		expect(takeIntents(['launch'])).toEqual([{ kind: 'launch', profileId: 'bob' }]);
	});

	it('leaves pending intents of unrelated kinds untouched', () => {
		requestIntent({ kind: 'sync' });
		requestIntent({ kind: 'snapshot' });

		expect(takeIntents(['sync'])).toEqual([{ kind: 'sync' }]);
		expect(takeIntents(['snapshot'])).toEqual([{ kind: 'snapshot' }]);
	});

	it('takeIntents can drain several requested kinds in one call, in the requested order', () => {
		requestIntent({ kind: 'update-all' });
		requestIntent({ kind: 'change-folder' });

		expect(takeIntents(['change-folder', 'update-all'])).toEqual([
			{ kind: 'change-folder' },
			{ kind: 'update-all' }
		]);
	});

	it('returns an empty list when none of the requested kinds are pending', () => {
		expect(takeIntents(['sync', 'snapshot'])).toEqual([]);
	});
});

describe('onIntents', () => {
	it('notifies a subscriber once per requestIntent call', () => {
		const listener = vi.fn();
		onIntents(listener);

		requestIntent({ kind: 'sync' });
		requestIntent({ kind: 'snapshot' });

		expect(listener).toHaveBeenCalledTimes(2);
	});

	it('notifies every registered subscriber', () => {
		const first = vi.fn();
		const second = vi.fn();
		onIntents(first);
		onIntents(second);

		requestIntent({ kind: 'sync' });

		expect(first).toHaveBeenCalledTimes(1);
		expect(second).toHaveBeenCalledTimes(1);
	});

	it('stops notifying a subscriber after it unsubscribes', () => {
		const listener = vi.fn();
		const unsubscribe = onIntents(listener);

		unsubscribe();
		requestIntent({ kind: 'sync' });

		expect(listener).not.toHaveBeenCalled();
	});
});
