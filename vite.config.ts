import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// https://vitejs.dev/config/
export default defineConfig({
	plugins: [react(), tailwindcss()],
	server: {
		// PUBLIC_URL in the backend's .env points here, and the callback URL
		// built from it is registered on the Keycloak client, so keep it pinned
		// rather than letting Vite pick a free port.
		port: 5173,
		strictPort: true,
		// The backend (src/start.py) answers /api and /auth. changeOrigin is
		// left off so the Origin check sees the browser's own origin.
		proxy: {
			"/api": "http://127.0.0.1:8080",
			"/auth": "http://127.0.0.1:8080",
		},
	},
});
