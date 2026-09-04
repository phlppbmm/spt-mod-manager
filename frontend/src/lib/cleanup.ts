import type { ProfileResidue, ProfileSummary } from './api/client';

type ResidueCategory = ProfileResidue['category'];

export interface ProfileRemovals {
	profile: string;
	file: string;
	entries: number;
	places: number;
	residues: ProfileResidue[];
}

const CATEGORY_LABELS: Record<ResidueCategory, string> = {
	item: 'inventory items',
	encyclopedia: 'encyclopedia entries',
	wishlist: 'wishlist entries',
	trader: 'trader records',
	'trader-purchase': 'trader purchases',
	quest: 'quests',
	'repeatable-quest': 'repeatable quests',
	'task-counter': 'quest counters',
	'hideout-production': 'hideout productions',
	'hideout-area': 'hideout slots',
	'unlocked-recipe': 'unlocked recipes',
	'insured-item': 'insured items',
	achievement: 'achievements',
	insurance: 'insurance entries',
	dialogue: 'trader messages',
	userbuild: 'saved builds',
	string: 'unidentified references'
};

export function categoryLabel(category: ResidueCategory): string {
	return CATEGORY_LABELS[category];
}

export function characterLabel(character: string): string {
	if (character === 'pmc') {
		return 'PMC';
	}
	if (character === 'scav') {
		return 'Scav';
	}
	return 'shared';
}

export function groupRemovalsByProfile(removals: ProfileResidue[]): ProfileRemovals[] {
	const groups = new Map<string, ProfileRemovals>();
	for (const residue of removals) {
		let group = groups.get(residue.profile);
		if (group === undefined) {
			group = {
				profile: residue.profile,
				file: `${residue.profile}.json`,
				entries: 0,
				places: 0,
				residues: []
			};
			groups.set(residue.profile, group);
		}
		group.entries += residue.count;
		group.places += 1;
		group.residues.push(residue);
	}
	return [...groups.values()];
}

export function formatProfileList(profiles: string[]): string {
	const files = profiles.map((profile) => `${profile}.json`);
	if (files.length < 2) {
		return files.join('');
	}
	return `${files.slice(0, -1).join(', ')} and ${files[files.length - 1]}`;
}

export interface ModResidues {
	mod: string;
	residues: ProfileResidue[];
}

export interface ProfileGroup {
	profile: ProfileSummary;
	mods: ModResidues[];
}

export function groupResiduesByProfile(
	profiles: ProfileSummary[],
	residues: Record<string, ProfileResidue[]>
): ProfileGroup[] {
	return profiles.map((profile) => ({
		profile,
		mods: Object.entries(residues)
			.map(([mod, found]) => ({
				mod,
				residues: found.filter((residue) => residue.profile === profile.profile_id)
			}))
			.filter((entry) => entry.residues.length > 0)
	}));
}
