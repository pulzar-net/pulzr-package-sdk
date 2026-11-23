// The pulzr-package-sdk docs: Starlight with the shared Pulzr theme
// (github.com/pulzar-net/docs-theme). Pages are in src/content/docs; the
// sidebar below orders them. Built and published by .github/workflows/docs.yml.
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import { pulzarPlugins } from '@pulzr/docs-theme';

export default defineConfig({
	site: 'https://oss.pulzr.net',
	base: '/pulzr-package-sdk',
	// Pages are page.html, as GitHub Pages serves them.
	build: { format: 'file' },
	integrations: [
		starlight({
			title: 'pulzr-package-sdk',
			description: 'Build extension modules for the Pulzr platform: gateway support packages and carrier drivers as sandboxed WebAssembly.',
			plugins: pulzarPlugins({ project: 'pulzr-package-sdk' }),
			sidebar: [
				{ label: 'Guides', items: ['gateway-package', 'carrier-driver', 'codec'] },
				{ label: 'Reference', items: ['abi'] },
			],
		}),
	],
});
