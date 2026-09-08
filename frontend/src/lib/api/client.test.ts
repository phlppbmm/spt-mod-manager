import { describe, expect, it, vi } from 'vitest';
import { invoke } from '@tauri-apps/api/core';
import {
	applyModSet,
	bindProfileSet,
	browseCatalog,
	checkProfiles,
	checkUpdates,
	cleanProfile,
	clearCatalogCache,
	deleteModSet,
	disableMod,
	enableMod,
	exportMods,
	exportModSet,
	getModDetails,
	history,
	importList,
	importModSet,
	initGameDir,
	installMod,
	launchProfile,
	launchStatus,
	listDirectories,
	listFavorites,
	listMods,
	listModSets,
	listProfiles,
	locateDirectory,
	modFiles,
	planModInstall,
	planModSet,
	planProfileCleanup,
	removeMod,
	renameModSet,
	saveModSet,
	searchCatalog,
	setFavorite,
	setSptVersion,
	snapshot,
	status,
	stopGame,
	syncCatalog,
	updateMod
} from './client';
import type { ModSetEntry } from './client';

vi.mock('@tauri-apps/api/core', () => ({
	invoke: vi.fn()
}));

const invokeMock = vi.mocked(invoke);

describe('listMods', () => {
	it('calls rpc with the snake_case payload and returns the typed body', async () => {
		const mods = [{ name: 'mod-a', version: '1.0.0' }];
		invokeMock.mockResolvedValue(mods);

		const result = await listMods('/game', true);

		expect(invokeMock).toHaveBeenCalledWith('rpc', {
			name: 'list_mods',
			payload: { path: '/game', include_all: true }
		});
		expect(result).toEqual(mods);
	});

	it('defaults include_all to false when not given', async () => {
		invokeMock.mockResolvedValue([]);

		await listMods('/game');

		expect(invokeMock).toHaveBeenCalledWith('rpc', {
			name: 'list_mods',
			payload: { path: '/game', include_all: false }
		});
	});
});

describe('listFavorites', () => {
	it('calls rpc with name list_favorites and an empty payload', async () => {
		invokeMock.mockResolvedValue({ mod_ids: [7] });

		const result = await listFavorites();

		expect(invokeMock).toHaveBeenCalledWith('rpc', {
			name: 'list_favorites',
			payload: {}
		});
		expect(result).toEqual({ mod_ids: [7] });
	});
});

describe('setFavorite', () => {
	it('sends mod_id and favorite, without a favorites_path', async () => {
		invokeMock.mockResolvedValue({ mod_ids: [7] });

		const result = await setFavorite(7, true);

		expect(invokeMock).toHaveBeenCalledWith('rpc', {
			name: 'set_favorite',
			payload: { mod_id: 7, favorite: true }
		});
		expect(result).toEqual({ mod_ids: [7] });
	});
});

/**
 * Every other RPC wrapper does the same job: hand-map its camelCase
 * arguments (with their default values) onto a snake_case payload and
 * forward whatever `invoke` resolves with. One table covers the payload
 * mapping (including every default-vs-explicit-argument branch) and the
 * pass-through of the resolved value for all of them.
 */
const SENTINEL = { marker: 'sentinel-response' };

const SET_ENTRIES: ModSetEntry[] = [
	{ forge_id: '47', name: 'SAIN', version: '3.1.0', enabled: true }
];

const wrapperCases: Array<{
	id: string;
	call: () => Promise<unknown>;
	command: string;
	payload: Record<string, unknown>;
}> = [
	{
		id: 'listDirectories omits path by default',
		call: () => listDirectories(),
		command: 'list_directories',
		payload: { path: null }
	},
	{
		id: 'listDirectories forwards an explicit path',
		call: () => listDirectories('/game'),
		command: 'list_directories',
		payload: { path: '/game' }
	},
	{
		id: 'locateDirectory defaults fingerprint to an empty array',
		call: () => locateDirectory('SPT'),
		command: 'locate_directory',
		payload: { name: 'SPT', fingerprint: [] }
	},
	{
		id: 'locateDirectory forwards an explicit fingerprint',
		call: () => locateDirectory('SPT', ['a.dll']),
		command: 'locate_directory',
		payload: { name: 'SPT', fingerprint: ['a.dll'] }
	},
	{
		id: 'initGameDir defaults adopt to false and sptVersion to null',
		call: () => initGameDir('/game'),
		command: 'init_game_dir',
		payload: { path: '/game', adopt: false, spt_version: null }
	},
	{
		id: 'initGameDir forwards an explicit adopt list and sptVersion',
		call: () => initGameDir('/game', ['mod-a'], '3.9.0'),
		command: 'init_game_dir',
		payload: { path: '/game', adopt: ['mod-a'], spt_version: '3.9.0' }
	},
	{
		id: 'setSptVersion forwards path and version',
		call: () => setSptVersion('/game', '3.9.0'),
		command: 'set_spt_version',
		payload: { path: '/game', version: '3.9.0' }
	},
	{
		id: 'modFiles forwards path and name',
		call: () => modFiles('/game', 'mod-a'),
		command: 'mod_files',
		payload: { path: '/game', name: 'mod-a' }
	},
	{
		id: 'removeMod defaults force and cleanProfiles to false',
		call: () => removeMod('/game', 'mod-a'),
		command: 'remove_mod',
		payload: { path: '/game', name: 'mod-a', force: false, clean_profiles: false }
	},
	{
		id: 'removeMod forwards an explicit force',
		call: () => removeMod('/game', 'mod-a', true),
		command: 'remove_mod',
		payload: { path: '/game', name: 'mod-a', force: true, clean_profiles: false }
	},
	{
		id: 'removeMod forwards an explicit cleanProfiles',
		call: () => removeMod('/game', 'mod-a', false, true),
		command: 'remove_mod',
		payload: { path: '/game', name: 'mod-a', force: false, clean_profiles: true }
	},
	{
		id: 'disableMod forwards path and name',
		call: () => disableMod('/game', 'mod-a'),
		command: 'disable_mod',
		payload: { path: '/game', name: 'mod-a' }
	},
	{
		id: 'enableMod forwards path and name',
		call: () => enableMod('/game', 'mod-a'),
		command: 'enable_mod',
		payload: { path: '/game', name: 'mod-a' }
	},
	{
		id: 'listProfiles forwards path',
		call: () => listProfiles('/game'),
		command: 'list_profiles',
		payload: { path: '/game' }
	},
	{
		id: 'checkProfiles forwards path',
		call: () => checkProfiles('/game'),
		command: 'check_profiles',
		payload: { path: '/game' }
	},
	{
		id: 'planProfileCleanup forwards path and modName',
		call: () => planProfileCleanup('/game', 'mod-a'),
		command: 'plan_profile_cleanup',
		payload: { path: '/game', mod_name: 'mod-a' }
	},
	{
		id: 'cleanProfile defaults profiles to null',
		call: () => cleanProfile('/game', 'mod-a'),
		command: 'clean_profile',
		payload: { path: '/game', mod_name: 'mod-a', profiles: null }
	},
	{
		id: 'cleanProfile forwards an explicit profiles list',
		call: () => cleanProfile('/game', 'mod-a', ['abc', 'def']),
		command: 'clean_profile',
		payload: { path: '/game', mod_name: 'mod-a', profiles: ['abc', 'def'] }
	},
	{
		id: 'history defaults name to null',
		call: () => history('/game'),
		command: 'history',
		payload: { path: '/game', name: null }
	},
	{
		id: 'history forwards an explicit name',
		call: () => history('/game', 'mod-a'),
		command: 'history',
		payload: { path: '/game', name: 'mod-a' }
	},
	{
		id: 'status forwards path',
		call: () => status('/game'),
		command: 'status',
		payload: { path: '/game' }
	},
	{
		id: 'snapshot defaults message to null',
		call: () => snapshot('/game'),
		command: 'snapshot',
		payload: { path: '/game', message: null }
	},
	{
		id: 'snapshot forwards an explicit message',
		call: () => snapshot('/game', 'checkpoint'),
		command: 'snapshot',
		payload: { path: '/game', message: 'checkpoint' }
	},
	{
		id: 'syncCatalog defaults full to false and operationId to null',
		call: () => syncCatalog('/game'),
		command: 'sync_catalog',
		payload: { path: '/game', full: false, operation_id: null }
	},
	{
		id: 'syncCatalog forwards explicit full and operationId',
		call: () => syncCatalog('/game', true, 'op-1'),
		command: 'sync_catalog',
		payload: { path: '/game', full: true, operation_id: 'op-1' }
	},
	{
		id: 'searchCatalog defaults compatibleOnly to false',
		call: () => searchCatalog('/game', 'realistic'),
		command: 'search_catalog',
		payload: { path: '/game', query: 'realistic', compatible_only: false }
	},
	{
		id: 'searchCatalog forwards an explicit compatibleOnly',
		call: () => searchCatalog('/game', 'realistic', true),
		command: 'search_catalog',
		payload: { path: '/game', query: 'realistic', compatible_only: true }
	},
	{
		id: 'browseCatalog defaults filter to "all" and compatibleOnly to false',
		call: () => browseCatalog('/game'),
		command: 'browse_catalog',
		payload: { path: '/game', filter: 'all', compatible_only: false }
	},
	{
		id: 'browseCatalog forwards an explicit filter and compatibleOnly',
		call: () => browseCatalog('/game', 'installed', true),
		command: 'browse_catalog',
		payload: { path: '/game', filter: 'installed', compatible_only: true }
	},
	{
		id: 'exportMods defaults format to "text" and clientOnly to false',
		call: () => exportMods('/game'),
		command: 'export_mods',
		payload: { path: '/game', format: 'text', client_only: false }
	},
	{
		id: 'exportMods forwards an explicit format and clientOnly',
		call: () => exportMods('/game', 'json', true),
		command: 'export_mods',
		payload: { path: '/game', format: 'json', client_only: true }
	},
	{
		id: 'planModInstall defaults version to null',
		call: () => planModInstall('/game', 47),
		command: 'plan_mod_install',
		payload: { path: '/game', mod_id_or_name: 47, version: null }
	},
	{
		id: 'planModInstall forwards an explicit version and mod name',
		call: () => planModInstall('/game', 'mod-a', '2.0.0'),
		command: 'plan_mod_install',
		payload: { path: '/game', mod_id_or_name: 'mod-a', version: '2.0.0' }
	},
	{
		id: 'installMod defaults version, force and operationId',
		call: () => installMod('/game', 47),
		command: 'install_mod',
		payload: {
			path: '/game',
			mod_id_or_name: 47,
			version: null,
			force: false,
			operation_id: null
		}
	},
	{
		id: 'installMod forwards explicit version, force and operationId',
		call: () => installMod('/game', 47, '2.0.0', true, 'op-2'),
		command: 'install_mod',
		payload: {
			path: '/game',
			mod_id_or_name: 47,
			version: '2.0.0',
			force: true,
			operation_id: 'op-2'
		}
	},
	{
		id: 'updateMod defaults force and operationId',
		call: () => updateMod('/game', 'mod-a'),
		command: 'update_mod',
		payload: { path: '/game', name: 'mod-a', force: false, operation_id: null }
	},
	{
		id: 'updateMod forwards explicit force and operationId',
		call: () => updateMod('/game', 'mod-a', true, 'op-3'),
		command: 'update_mod',
		payload: { path: '/game', name: 'mod-a', force: true, operation_id: 'op-3' }
	},
	{
		id: 'checkUpdates maps an empty names list to null',
		call: () => checkUpdates('/game'),
		command: 'check_updates',
		payload: { path: '/game', names: null }
	},
	{
		id: 'checkUpdates forwards a non-empty names list as-is',
		call: () => checkUpdates('/game', ['mod-a', 'mod-b']),
		command: 'check_updates',
		payload: { path: '/game', names: ['mod-a', 'mod-b'] }
	},
	{
		id: 'getModDetails forwards mod_id',
		call: () => getModDetails(47),
		command: 'get_mod_details',
		payload: { mod_id: 47 }
	},
	{
		id: 'clearCatalogCache defaults downloads to false with no request',
		call: () => clearCatalogCache(),
		command: 'clear_catalog_cache',
		payload: { downloads: false }
	},
	{
		id: 'clearCatalogCache forwards an explicit downloads flag',
		call: () => clearCatalogCache({ downloads: true }),
		command: 'clear_catalog_cache',
		payload: { downloads: true }
	},
	{
		id: 'importList maps an empty source and defaults apply to false',
		call: () => importList('/game', {}),
		command: 'import_list',
		payload: { path: '/game', list_file: null, content: null, apply: false }
	},
	{
		id: 'importList forwards listFile, content and an explicit apply',
		call: () => importList('/game', { listFile: 'list.txt', content: 'data' }, true),
		command: 'import_list',
		payload: { path: '/game', list_file: 'list.txt', content: 'data', apply: true }
	},
	{
		id: 'launchProfile defaults stopServerOnExit to true and operationId to null',
		call: () => launchProfile('/game', 'profile-1'),
		command: 'launch_profile',
		payload: {
			path: '/game',
			profile_id: 'profile-1',
			stop_server_on_exit: true,
			mod_set: 'apply',
			operation_id: null
		}
	},
	{
		id: 'launchProfile forwards an explicit stopServerOnExit and operationId',
		call: () => launchProfile('/game', 'profile-1', false, 'op-4'),
		command: 'launch_profile',
		payload: {
			path: '/game',
			profile_id: 'profile-1',
			stop_server_on_exit: false,
			mod_set: 'apply',
			operation_id: 'op-4'
		}
	},
	{
		id: 'launchStatus forwards path',
		call: () => launchStatus('/game'),
		command: 'launch_status',
		payload: { path: '/game' }
	},
	{
		id: 'stopGame forwards path',
		call: () => stopGame('/game'),
		command: 'stop_game',
		payload: { path: '/game' }
	},
	{
		id: 'listModSets forwards path',
		call: () => listModSets('/game'),
		command: 'list_mod_sets',
		payload: { path: '/game' }
	},
	{
		id: 'saveModSet sends a null entries list when none is given',
		call: () => saveModSet('/game', 'Hardcore'),
		command: 'save_mod_set',
		payload: { path: '/game', name: 'Hardcore', entries: null }
	},
	{
		id: 'saveModSet forwards an explicit entries list unchanged',
		call: () => saveModSet('/game', 'Hardcore', SET_ENTRIES),
		command: 'save_mod_set',
		payload: { path: '/game', name: 'Hardcore', entries: SET_ENTRIES }
	},
	{
		id: 'deleteModSet forwards path and name',
		call: () => deleteModSet('/game', 'Hardcore'),
		command: 'delete_mod_set',
		payload: { path: '/game', name: 'Hardcore' }
	},
	{
		id: 'renameModSet maps newName onto new_name',
		call: () => renameModSet('/game', 'Hardcore', 'Ironman'),
		command: 'rename_mod_set',
		payload: { path: '/game', name: 'Hardcore', new_name: 'Ironman' }
	},
	{
		id: 'exportModSet forwards path and name',
		call: () => exportModSet('/game', 'Hardcore'),
		command: 'export_mod_set',
		payload: { path: '/game', name: 'Hardcore' }
	},
	{
		id: 'importModSet maps an empty source and defaults the name to null',
		call: () => importModSet('/game', {}),
		command: 'import_mod_set',
		payload: { path: '/game', set_file: null, content: null, name: null }
	},
	{
		id: 'importModSet forwards setFile, content and an explicit name',
		call: () => importModSet('/game', { setFile: 'set.json', content: 'data' }, 'Ironman'),
		command: 'import_mod_set',
		payload: { path: '/game', set_file: 'set.json', content: 'data', name: 'Ironman' }
	},
	{
		id: 'bindProfileSet unbinds with a null set_name by default',
		call: () => bindProfileSet('/game', 'profile-1'),
		command: 'bind_profile_set',
		payload: { path: '/game', profile_id: 'profile-1', set_name: null }
	},
	{
		id: 'bindProfileSet forwards an explicit set name',
		call: () => bindProfileSet('/game', 'profile-1', 'Hardcore'),
		command: 'bind_profile_set',
		payload: { path: '/game', profile_id: 'profile-1', set_name: 'Hardcore' }
	},
	{
		id: 'planModSet forwards path and name',
		call: () => planModSet('/game', 'Hardcore'),
		command: 'plan_mod_set',
		payload: { path: '/game', name: 'Hardcore' }
	},
	{
		id: 'applyModSet defaults ignoreMissing to false and operationId to null',
		call: () => applyModSet('/game', 'Hardcore'),
		command: 'apply_mod_set',
		payload: { path: '/game', name: 'Hardcore', ignore_missing: false, operation_id: null }
	},
	{
		id: 'applyModSet forwards an explicit ignoreMissing and operationId',
		call: () => applyModSet('/game', 'Hardcore', true, 'op-9'),
		command: 'apply_mod_set',
		payload: { path: '/game', name: 'Hardcore', ignore_missing: true, operation_id: 'op-9' }
	},
	{
		id: 'launchProfile forwards an explicit mod-set mode',
		call: () => launchProfile('/game', 'profile-1', true, 'op-7', 'skip'),
		command: 'launch_profile',
		payload: {
			path: '/game',
			profile_id: 'profile-1',
			stop_server_on_exit: true,
			mod_set: 'skip',
			operation_id: 'op-7'
		}
	}
];

describe.each(wrapperCases)('$id', ({ call, command, payload }) => {
	it('sends the mapped payload to rpc and returns the resolved value', async () => {
		invokeMock.mockResolvedValue(SENTINEL);

		const result = await call();

		expect(invokeMock).toHaveBeenCalledWith('rpc', { name: command, payload });
		expect(result).toBe(SENTINEL);
	});
});
