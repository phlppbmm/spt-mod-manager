const WORD_STARTS = ' -_.';

const PREFIX_BONUS = 8;
const WORD_START_BONUS = 6;
const CONSECUTIVE_BONUS = 5;
const MAX_GAP_PENALTY = 10;

/**
 * Case-insensitive subsequence match. `null` means the query is not contained
 * in the text at all; a higher score means a closer match.
 */
export function fuzzyScore(query: string, text: string): number | null {
	if (query === '') {
		return 0;
	}
	const needle = query.toLowerCase();
	const haystack = text.toLowerCase();
	let score = 0;
	let previous = -1;
	for (const character of needle) {
		const at = haystack.indexOf(character, previous + 1);
		if (at === -1) {
			return null;
		}
		if (at === 0) {
			score += PREFIX_BONUS;
		} else if (WORD_STARTS.includes(haystack[at - 1])) {
			score += WORD_START_BONUS;
		}
		if (at === previous + 1) {
			score += CONSECUTIVE_BONUS;
		} else {
			score -= Math.min(at - previous - 1, MAX_GAP_PENALTY);
		}
		previous = at;
	}
	return score;
}

export function rank<T>(
	query: string,
	items: readonly T[],
	textOf: (item: T) => string,
	limit: number
): T[] {
	const scored: { item: T; score: number }[] = [];
	for (const item of items) {
		const score = fuzzyScore(query, textOf(item));
		if (score !== null) {
			scored.push({ item, score });
		}
	}
	scored.sort((a, b) => b.score - a.score);
	return scored.slice(0, limit).map((entry) => entry.item);
}
