import { svelteTesting } from '@testing-library/svelte/vite';
import { defineConfig, mergeConfig } from 'vitest/config';

import viteConfig from './vite.config.ts';

// mergeConfig concatenates arrays, so the lib project's `test` block is dropped
// before merging: its include and its 100% coverage gates must not leak in here.
const { test: _libTest, ...shared } = viteConfig;

export default mergeConfig(
	shared,
	defineConfig({
		plugins: [svelteTesting()],
		test: {
			name: 'components',
			include: ['tests/**/*.test.ts'],
			environment: 'happy-dom',
			setupFiles: ['./tests/support/setup.ts'],
			coverage: {
				provider: 'v8',
				include: ['src/routes/**/*.svelte', 'src/lib/components/**/*.svelte'],
				reportsDirectory: 'coverage/components',
				reporter: ['text', 'json-summary'],
				// The measured coverage of the component suite, as a floor: a change
				// that drops below it has stopped exercising something these tests
				// reached. Raised only together with new tests, never rounded up.
				thresholds: { statements: 95.08, branches: 78.51, functions: 95.31, lines: 94.04 }
			}
		}
	})
);
