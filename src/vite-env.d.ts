/// <reference types="vite/client" />

interface ImportMetaEnv {
	readonly VITE_KC_URL?: string;
	readonly VITE_KC_REALM?: string;
	readonly VITE_KC_CLIENT_ID?: string;
}

interface ImportMeta {
	readonly env: ImportMetaEnv;
}
