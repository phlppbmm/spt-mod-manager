import { describe, expect, it } from 'vitest';
import {
	categoryLabel,
	characterLabel,
	formatProfileList,
	groupRemovalsByProfile,
	groupResiduesByProfile,
	profileLabel
} from './cleanup';
import type { ProfileResidue, ProfileSummary } from './api/client';

type ResidueCategory = ProfileResidue['category'];

function makeResidue(overrides: Partial<ProfileResidue> = {}): ProfileResidue {
	return {
		profile: 'bob',
		category: 'item',
		ids: [],
		count: 0,
		character: '',
		path: '',
		...overrides
	};
}

describe('categoryLabel', () => {
	it.each<[ResidueCategory, string]>([
		['item', 'inventory items'],
		['encyclopedia', 'encyclopedia entries'],
		['wishlist', 'wishlist entries'],
		['trader', 'trader records'],
		['trader-purchase', 'trader purchases'],
		['quest', 'quests'],
		['repeatable-quest', 'repeatable quests'],
		['task-counter', 'quest counters'],
		['hideout-production', 'hideout productions'],
		['hideout-area', 'hideout slots'],
		['unlocked-recipe', 'unlocked recipes'],
		['insured-item', 'insured items'],
		['achievement', 'achievements'],
		['insurance', 'insurance entries'],
		['dialogue', 'trader messages'],
		['userbuild', 'saved builds'],
		['string', 'unidentified references']
	])('maps %s to a readable label', (category, expected) => {
		expect(categoryLabel(category)).toBe(expected);
	});

	it('has no label for a category outside the known set', () => {
		expect(categoryLabel('mystery-category' as ResidueCategory)).toBeUndefined();
	});
});

describe('characterLabel', () => {
	it('labels pmc as PMC', () => {
		expect(characterLabel('pmc')).toBe('PMC');
	});

	it('labels scav as Scav', () => {
		expect(characterLabel('scav')).toBe('Scav');
	});

	it('falls back to shared for an empty character', () => {
		expect(characterLabel('')).toBe('shared');
	});

	it('falls back to shared for any other character value', () => {
		expect(characterLabel('boss')).toBe('shared');
	});
});

describe('groupRemovalsByProfile', () => {
	it('returns an empty list for no removals', () => {
		expect(groupRemovalsByProfile([])).toEqual([]);
	});

	it('groups by first-seen profile order, summing counts and counting places', () => {
		const bobItems = makeResidue({ profile: 'bob', category: 'item', count: 2 });
		const aliceQuest = makeResidue({ profile: 'alice', category: 'quest', count: 1 });
		const bobWishlist = makeResidue({ profile: 'bob', category: 'wishlist', count: 3 });

		const groups = groupRemovalsByProfile([bobItems, aliceQuest, bobWishlist]);

		expect(groups.map((group) => group.profile)).toEqual(['bob', 'alice']);

		const [bob, alice] = groups;
		expect(bob.file).toBe('bob.json');
		expect(bob.entries).toBe(5);
		expect(bob.places).toBe(2);
		expect(bob.residues).toEqual([bobItems, bobWishlist]);

		expect(alice.file).toBe('alice.json');
		expect(alice.entries).toBe(1);
		expect(alice.places).toBe(1);
		expect(alice.residues).toEqual([aliceQuest]);
	});

	it('counts each residue entry as a separate place even within the same category', () => {
		const first = makeResidue({ profile: 'carol', category: 'item', count: 4 });
		const second = makeResidue({ profile: 'carol', category: 'item', count: 6 });

		const [carol] = groupRemovalsByProfile([first, second]);

		expect(carol.entries).toBe(10);
		expect(carol.places).toBe(2);
	});
});

describe('formatProfileList', () => {
	it('returns an empty string for no profiles', () => {
		expect(formatProfileList([])).toBe('');
	});

	it('returns the bare file name for a single profile', () => {
		expect(formatProfileList(['bob'])).toBe('bob.json');
	});

	it('joins two profiles with "and"', () => {
		expect(formatProfileList(['bob', 'alice'])).toBe('bob.json and alice.json');
	});

	it('joins three or more profiles with commas and a trailing "and"', () => {
		expect(formatProfileList(['bob', 'alice', 'carol'])).toBe(
			'bob.json, alice.json and carol.json'
		);
	});
});

function makeSummary(overrides: Partial<ProfileSummary> = {}): ProfileSummary {
	return {
		file: 'bob.json',
		profile_id: 'bob',
		nickname: 'Bob',
		level: 5,
		side: 'Usec',
		modified: '2026-01-02T03:04:05+00:00',
		readable: true,
		error: '',
		...overrides
	};
}

describe('profileLabel', () => {
	it('returns the nickname when one is set', () => {
		expect(profileLabel(makeSummary({ nickname: 'Bob', file: 'bob.json' }))).toBe('Bob');
	});

	it('falls back to the file name when the nickname is empty', () => {
		expect(profileLabel(makeSummary({ nickname: '', file: 'bob.json' }))).toBe('bob.json');
	});
});

describe('groupResiduesByProfile', () => {
	it('returns one group per profile, in the order the profiles come in', () => {
		const profiles = [
			makeSummary({ file: 'bob.json', profile_id: 'bob' }),
			makeSummary({ file: 'alice.json', profile_id: 'alice' })
		];

		const groups = groupResiduesByProfile(profiles, {});

		expect(groups.map((group) => group.profile.profile_id)).toEqual(['bob', 'alice']);
		expect(groups.map((group) => group.mods)).toEqual([[], []]);
	});

	it('keeps only the residues of the profile the group belongs to', () => {
		const profiles = [
			makeSummary({ file: 'bob.json', profile_id: 'bob' }),
			makeSummary({ file: 'alice.json', profile_id: 'alice' })
		];
		const residues = {
			SomeMod: [
				makeResidue({ profile: 'bob', category: 'item' }),
				makeResidue({ profile: 'alice', category: 'quest' })
			]
		};

		const groups = groupResiduesByProfile(profiles, residues);

		expect(groups[0].mods).toEqual([
			{ mod: 'SomeMod', residues: [makeResidue({ profile: 'bob', category: 'item' })] }
		]);
		expect(groups[1].mods).toEqual([
			{ mod: 'SomeMod', residues: [makeResidue({ profile: 'alice', category: 'quest' })] }
		]);
	});

	it('drops mods that left nothing in the profile', () => {
		const profiles = [makeSummary({ file: 'bob.json', profile_id: 'bob' })];
		const residues = {
			Touching: [makeResidue({ profile: 'bob' })],
			Elsewhere: [makeResidue({ profile: 'alice' })]
		};

		const groups = groupResiduesByProfile(profiles, residues);

		expect(groups[0].mods.map((entry) => entry.mod)).toEqual(['Touching']);
	});

	it('returns no groups when there are no profiles', () => {
		expect(groupResiduesByProfile([], { SomeMod: [makeResidue()] })).toEqual([]);
	});
});
