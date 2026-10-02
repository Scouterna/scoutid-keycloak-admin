import { type User, UserManager, WebStorageStateStore } from "oidc-client-ts";

/**
 * Authorization Code + PKCE against ScoutID, in the browser. The access token
 * goes to this app's own /api, never to Keycloak's Admin API: the backend
 * checks the Scoutnet permissions in it and forwards it from inside the
 * cluster, where Keycloak's realm-management roles then apply as well.
 *
 * Where to log in comes from /api/config, so one image serves any environment.
 */
let managerPromise: Promise<UserManager> | null = null;

function getManager(): Promise<UserManager> {
	if (!managerPromise) {
		managerPromise = fetch("/api/config")
			.then((response) => {
				if (!response.ok) {
					throw new Error(
						`Kunde inte läsa konfigurationen (${response.status})`,
					);
				}
				return response.json() as Promise<{
					authority: string;
					clientId: string;
				}>;
			})
			.then(
				({ authority, clientId }) =>
					new UserManager({
						authority,
						client_id: clientId,
						redirect_uri: `${window.location.origin}/`,
						post_logout_redirect_uri: `${window.location.origin}/`,
						response_type: "code",
						// scoutnet-memberships carries the kår roles the backend
						// derives permissions from.
						scope: "openid profile scoutnet-memberships",
						// Keep the session in localStorage so a page reload doesn't
						// force a new round-trip to Keycloak.
						userStore: new WebStorageStateStore({ store: window.localStorage }),
						automaticSilentRenew: true,
					}),
			);
	}
	return managerPromise;
}

/**
 * Memoised so the login round-trip runs exactly once per page load. React's
 * StrictMode invokes effects twice in development, and an authorization code is
 * single-use: the second redemption would fail with "Code not valid" even
 * though the first had already signed the user in.
 */
let initPromise: Promise<User | null> | null = null;

async function completeLogin(): Promise<User | null> {
	const manager = await getManager();
	const params = new URLSearchParams(window.location.search);
	if (params.has("code") && params.has("state")) {
		try {
			return await manager.signinRedirectCallback();
		} catch (error) {
			// The code may already have been redeemed (e.g. a reload replaying
			// the callback URL). A stored session means login actually worked,
			// so prefer it over surfacing a misleading error.
			const stored = await manager.getUser();
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

	const user = await manager.getUser();
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

/** The current access token, or null when signed out or expired. */
export async function accessToken(): Promise<string | null> {
	const user = await (await getManager()).getUser();
	return user && !user.expired ? user.access_token : null;
}

export const login = async () => (await getManager()).signinRedirect();

export const logout = async () => (await getManager()).signoutRedirect();
