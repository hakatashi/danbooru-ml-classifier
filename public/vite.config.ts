import tailwindcss from '@tailwindcss/vite';
import vue from '@vitejs/plugin-vue';
import {defineConfig} from 'vite';

// https://vite.dev/config/
export default defineConfig({
	plugins: [vue(), tailwindcss()],
	server: {
		// Listen on all interfaces so devices on the local network (e.g.
		// http://192.168.10.112:5173) can reach the dev server directly.
		host: true,
	},
});
