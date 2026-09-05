import { type User, UserManager, WebStorageStateStore } from "oidc-client-ts";
import { config, realmBase } from "./config";

/**
 * Authorization Code + PKCE. A browser app cannot hold a client secret, so the
 * GUI authenticates the human admin and reuses their token against the Admin
 * API — Keycloak's own realm-management roles then decide what they may do.
 * (Backend services use client_credentials instead; that pattern is
 * deliberately not used here.)
 */
export const userManager = new UserManager({
	authority: realmBase,
	client_id: config.clientId,
	redirect_uri: `${window.location.origin}/`,
	post_logout_redirect_uri: `${window.location.origin}/`,
	response_type: "code",
	scope: "openid profile email",
	// Keep the session in localStorage so a page reload doesn't force a new
	// round-trip to Keycloak.
	userStore: new WebStorageStateStore({ store: window.localStorage }),
	automaticSilentRenew: true,
});

/**
 * Memoised so the login round-trip runs exactly once per page load. React's
 * StrictMode invokes effects twice in development, and an authorization code is
 * single-use: the second redemption would fail with "Code not valid" even
 * though the first had already signed the user in.
 */
let initPromise: Promise<User | null> | null = null;

async function completeLogin(): Promise<User | null> {
	const params = new URLSearchParams(window.location.search);
	if (params.has("code") && params.has("state")) {
		try {
			return await userManager.signinRedirectCallback();
		} catch (error) {
			// The code may already have been redeemed (e.g. a reload replaying
			// the callback URL). A stored session means login actually worked,
			// so prefer it over surfacing a misleading error.
			const stored = await userManager.getUser();
			if (stored && !stored.expired) {
				return stored;
			}
			throw error;
		} finally {
			// Drop code/state from the address bar either way, so a reload is
			// not a replay of a spent authorization code.
			window.history.replaceState({}, "", window.location.pathname);
		}
	}

	const user = await userManager.getUser();
	if (!user || user.expired) {
		return null;
	}
	return user;
}

/**
 * Resolve the current user, completing the login round-trip when Keycloak has
 * just redirected back with a `code`. Returns null when nobody is signed in.
 */
export function initAuth(): Promise<User | null> {
	if (!initPromise) {
		initPromise = completeLogin();
	}
	return initPromise;
}

export const login = () => userManager.signinRedirect();

export const logout = () => userManager.signoutRedirect();
