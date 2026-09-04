import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import type { DevEnvironment, Plugin } from 'vite';
import { defineConfig } from 'vitest/config';

// A component's virtual CSS module can only be served once the component itself
// has been compiled, and WebKit requests both in parallel, so compile first.
function compileSvelteBeforeCss(): Plugin {
	return {
		name: 'sptmod:compile-svelte-before-css',
		apply: 'serve',
		enforce: 'pre',
		async load(id: string): Promise<undefined> {
			if (/[?&]svelte&type=style&lang\.css$/.test(id)) {
				const env = this.environment as DevEnvironment;
				await env.transformRequest(id.slice(0, id.indexOf('?')));
			}
			return undefined;
		}
	};
}

export default defineConfig({
	clearScreen: false,
	server: { port: 5173, strictPort: true },
	envPrefix: ['VITE_', 'TAURI_ENV_*'],
	build: { target: 'es2022' },
	plugins: [
		compileSvelteBeforeCss(),
		sveltekit({
			compilerOptions: {
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},
			adapter: adapter()
		})
	],
	test: {
		include: ['src/lib/**/*.test.ts'],
		environment: 'node',
		coverage: {
			provider: 'v8',
			include: ['src/lib/**/*.ts'],
			exclude: ['src/lib/**/*.test.ts', 'src/lib/api/types.ts'],
			thresholds: { statements: 100, branches: 100, functions: 100, lines: 100 }
		}
	}
});
