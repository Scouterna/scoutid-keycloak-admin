import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// https://vitejs.dev/config/
export default defineConfig({
	plugins: [react(), tailwindcss()],
	server: {
		// Must match the redirect URI / web origin registered on the Keycloak
		// client, so keep it pinned rather than letting Vite pick a free port.
		port: 5173,
		strictPort: true,
	},
});
