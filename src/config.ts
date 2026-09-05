/**
 * Deployment-specific settings.
 *
 * Must point at a host that serves both the OIDC endpoints and the Admin REST
 * API. Keycloak deployments split these: the public hostname (KC_HOSTNAME)
 * serves /realms/*, while the admin hostname (KC_HOSTNAME_ADMIN) additionally
 * serves /admin/*. Use the admin hostname — it answers both.
 *
 * Tokens are still *issued* by the public hostname, so `iss` will not match
 * this URL. That is expected and correct.
 *
 * Resolution order:
 *   1. window.__SCOUTID_CONFIG__ — written by the container entrypoint, so one
 *      image can be deployed to any environment.
 *   2. VITE_* build-time env, for `pnpm dev` and .env.local.
 *   3. The dev-environment defaults below.
 */

interface RuntimeConfig {
	authority?: string;
	realm?: string;
	clientId?: string;
}

declare global {
	interface Window {
		__SCOUTID_CONFIG__?: RuntimeConfig;
	}
}

const runtime: RuntimeConfig =
	typeof window === "undefined" ? {} : (window.__SCOUTID_CONFIG__ ?? {});

/** Runtime value wins, but only when non-empty — the entrypoint writes "". */
const pick = (
	value: string | undefined,
	...fallbacks: (string | undefined)[]
) => [value, ...fallbacks].find((candidate) => candidate) ?? "";

export const config = {
	authority: pick(runtime.authority, import.meta.env.VITE_KC_URL),
	realm: pick(runtime.realm, import.meta.env.VITE_KC_REALM),
	/**
	 * Public client for this GUI, registered in the realm with standard flow +
	 * PKCE (S256), redirect URI and web origin pointing at this app.
	 */
	clientId: pick(
		runtime.clientId,
		import.meta.env.VITE_KC_CLIENT_ID,
		"scoutid-admin-gui",
	),
} as const;

/**
 * There is deliberately no default host or realm: this GUI has full admin rights
 * over whichever realm it is pointed at, so it must be configured explicitly
 * rather than silently defaulting to someone's environment.
 */
export const isConfigured = Boolean(config.authority && config.realm);

export const realmBase = `${config.authority}/realms/${config.realm}`;
export const adminBase = `${config.authority}/admin/realms/${config.realm}`;
