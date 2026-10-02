import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// https://vitejs.dev/config/
export default defineConfig({
	plugins: [react(), tailwindcss()],
	server: {
		// The SPA's redirect URI (origin + "/") is registered on the Keycloak
		// client, so keep the port pinned rather than letting Vite pick one.
		port: 5173,
		strictPort: true,
		// The backend (src/start.py) answers /api.
		proxy: {
			"/api": "http://127.0.0.1:8080",
		},
	},
});
