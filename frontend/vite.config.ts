import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vitest/config';

const proxy = {
	'/api': {
		target: 'http://127.0.0.1:8787',
		changeOrigin: true
	}
};

export default defineConfig({
	server: { proxy },
	preview: { proxy },
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
