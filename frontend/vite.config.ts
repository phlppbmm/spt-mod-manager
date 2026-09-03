import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vitest/config';

export default defineConfig({
	clearScreen: false,
	server: { port: 5173, strictPort: true },
	envPrefix: ['VITE_', 'TAURI_ENV_*'],
	build: { target: 'es2022' },
	plugins: [
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
		environment: 'node'
	}
});
