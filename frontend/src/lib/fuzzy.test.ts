import { describe, expect, it } from 'vitest';
import { fuzzyScore, rank } from './fuzzy';

describe('fuzzyScore', () => {
	it('returns null when the query is not a subsequence of the text', () => {
		expect(fuzzyScore('xyz', 'abc')).toBeNull();
	});

	it('returns null when only a prefix of the query is found', () => {
		expect(fuzzyScore('abz', 'abc')).toBeNull();
	});

	it('returns 0 for an empty query regardless of the text', () => {
		expect(fuzzyScore('', 'anything')).toBe(0);
	});

	it('scores a prefix match higher than a mid-word match', () => {
		const prefix = fuzzyScore('a', 'abc');
		const midWord = fuzzyScore('a', 'bac');

		expect(prefix).toBe(13);
		expect(midWord).toBe(-1);
		expect(prefix as number).toBeGreaterThan(midWord as number);
	});

	it('scores a consecutive-character match higher than a scattered one', () => {
		const consecutive = fuzzyScore('ab', 'abx');
		const scattered = fuzzyScore('ab', 'axb');

		expect(consecutive).toBe(18);
		expect(scattered).toBe(12);
		expect(consecutive as number).toBeGreaterThan(scattered as number);
	});

	it('rewards a match right after a word-start separator over a mid-word match', () => {
		const afterSeparator = fuzzyScore('b', 'foo-bar');
		const midWord = fuzzyScore('b', 'foobar');

		expect(afterSeparator).toBe(2);
		expect(midWord).toBe(-3);
		expect(afterSeparator as number).toBeGreaterThan(midWord as number);
	});

	it.each(['-', '_', '.', ' '])(
		'treats %s as a word-start separator',
		(separator) => {
			const withSeparator = fuzzyScore('b', `foo${separator}bar`);
			const without = fuzzyScore('b', 'foopbar');

			expect(withSeparator).toBeGreaterThan(without!);
		}
	);

	it('is case-insensitive on both query and text', () => {
		const lower = fuzzyScore('abc', 'abc');

		expect(fuzzyScore('ABC', 'abc')).toBe(lower);
		expect(fuzzyScore('abc', 'ABC')).toBe(lower);
		expect(fuzzyScore('AbC', 'aBc')).toBe(lower);
	});
});

describe('rank', () => {
	interface Item {
		name: string;
	}

	const textOf = (item: Item): string => item.name;

	it('drops items whose text does not match the query', () => {
		const items: Item[] = [{ name: 'abc' }, { name: 'xyz' }];

		expect(rank('a', items, textOf, 10)).toEqual([{ name: 'abc' }]);
	});

	it('orders matches by descending score', () => {
		const items: Item[] = [{ name: 'xa' }, { name: 'a' }];

		expect(rank('a', items, textOf, 10)).toEqual([{ name: 'a' }, { name: 'xa' }]);
	});

	it('keeps the original relative order for items with equal scores', () => {
		const first: Item = { name: 'a' };
		const second: Item = { name: 'a' };

		expect(rank('a', [first, second], textOf, 10)).toEqual([first, second]);
	});

	it('truncates the result to the given limit', () => {
		const items: Item[] = [{ name: 'aa' }, { name: 'ab' }, { name: 'ac' }];

		expect(rank('a', items, textOf, 2)).toHaveLength(2);
	});

	it('returns an empty list for an empty item list', () => {
		expect(rank('a', [], textOf, 10)).toEqual([]);
	});
});
