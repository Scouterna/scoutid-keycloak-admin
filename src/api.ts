import { userManager } from "./auth";
import { adminBase, config } from "./config";

/** The subset of Keycloak's client representation this GUI works with. */
export interface KcClient {
	id: string;
	clientId: string;
	name?: string;
	description?: string;
	/** "openid-connect" (default) or "saml". */
	protocol?: string;
	enabled: boolean;
	publicClient: boolean;
	standardFlowEnabled: boolean;
	serviceAccountsEnabled: boolean;
	frontchannelLogout?: boolean;
	redirectUris?: string[];
	webOrigins?: string[];
	adminUrl?: string;
	rootUrl?: string;
	defaultClientScopes?: string[];
	attributes?: Record<string, string>;
	protocolMappers?: KcProtocolMapper[];
}

/** A claim mapper created together with the client, on its dedicated scope. */
export interface KcProtocolMapper {
	name: string;
	protocol: string;
	protocolMapper: string;
	config: Record<string, string>;
}

export class ApiError extends Error {
	constructor(
		readonly status: number,
		message: string,
	) {
		super(message);
	}
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
	const user = await userManager.getUser();
	if (!user || user.expired) {
		throw new ApiError(401, "Not signed in");
	}

	const url = `${adminBase}${path}`;
	let response: Response;
	try {
		response = await fetch(url, {
			...init,
			headers: {
				...init?.headers,
				Authorization: `Bearer ${user.access_token}`,
				...(init?.body ? { "Content-Type": "application/json" } : {}),
			},
		});
	} catch (cause) {
		// fetch() rejects (rather than returning a non-ok response) when the
		// request never completed: CORS rejection, DNS/TLS failure, or the
		// request being blocked. The browser deliberately hides which.
		//
		// The usual cause is CORS: Keycloak's Admin API derives
		// Access-Control-Allow-Origin from the Web origins of the client that
		// *issued the token* (the `azp` claim) — not from the URL being called.
		// So the origin must be listed on this GUI's own client.
		throw new ApiError(
			0,
			`Kunde inte nå ${url} — kontrollera att Web origins på klienten ` +
				`${config.clientId} innehåller ${window.location.origin} ` +
				`(${(cause as Error).message})`,
		);
	}

	if (!response.ok) {
		// Keycloak reports failures as {error|errorMessage}; fall back to status.
		let detail = response.statusText;
		try {
			const body = await response.json();
			detail = body.errorMessage ?? body.error ?? detail;
		} catch {
			// Non-JSON error body — keep the status text.
		}
		throw new ApiError(response.status, detail);
	}

	// Writes answer with an empty body: 204 for PUT/DELETE, but 201 with
	// content-length 0 (and a Location header) for a successful POST /clients.
	// Parsing that as JSON throws "unexpected end of data", which would report a
	// successful create as a failure — so key off the body, not the status.
	const text = await response.text();
	if (!text) {
		return undefined as T;
	}
	return JSON.parse(text) as T;
}

export const listClients = () => request<KcClient[]>("/clients");

export const getClient = (id: string) => request<KcClient>(`/clients/${id}`);

export interface KcClientScope {
	id: string;
	name: string;
	description?: string;
	protocol?: string;
}

/** All client scopes defined in the realm. */
export const listClientScopes = () =>
	request<KcClientScope[]>("/client-scopes");

/** Scopes always applied to this client (as opposed to optional ones). */
export const getDefaultClientScopes = (id: string) =>
	request<KcClientScope[]>(`/clients/${id}/default-client-scopes`);

export const addDefaultClientScope = (id: string, scopeId: string) =>
	request<void>(`/clients/${id}/default-client-scopes/${scopeId}`, {
		method: "PUT",
	});

export const removeDefaultClientScope = (id: string, scopeId: string) =>
	request<void>(`/clients/${id}/default-client-scopes/${scopeId}`, {
		method: "DELETE",
	});

/** Look a client up by its clientId (Keycloak's REST id differs from it). */
export const findClient = async (clientId: string) => {
	const matches = await request<KcClient[]>(
		`/clients?clientId=${encodeURIComponent(clientId)}`,
	);
	return matches[0] ?? null;
};

/**
 * Client secret for a confidential client. Admins need this to configure the
 * far end, and having to leave for the Keycloak console would defeat the point
 * of this GUI.
 */
export const getClientSecret = (id: string) =>
	request<{ type: string; value: string }>(`/clients/${id}/client-secret`);

export const createClient = (client: Partial<KcClient>) =>
	request<void>("/clients", { method: "POST", body: JSON.stringify(client) });

export const updateClient = (id: string, client: Partial<KcClient>) =>
	request<void>(`/clients/${id}`, {
		method: "PUT",
		body: JSON.stringify(client),
	});
