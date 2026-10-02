import { accessToken, login } from "./auth";

/**
 * The backend's /api. The browser never calls Keycloak's Admin API: it sends
 * the user's access token here, the backend checks the Scoutnet permissions in
 * it and forwards the call, with the same token, from inside the cluster.
 */

export interface Me {
	name: string;
	username: string;
	isAdmin: boolean;
	/** Scoutnet group id → name, for every kår the user is IT manager of. */
	groups: Record<string, string>;
	hasAccess: boolean;
	/** Set when Scoutnet data had to be truncated, which can drop admin rights. */
	membershipsError: string | null;
}

export interface Preset {
	id: string;
	label: string;
	description: string;
	protocol: "openid-connect" | "saml";
	needsDomain: boolean;
	needsKarId: boolean;
	hasEndpoint: boolean;
	membershipsByDefault: boolean;
}

export type ClientType = "saml" | "service" | "public" | "confidential";

export interface ClientSummary {
	id: string;
	clientId: string;
	name: string;
	enabled: boolean;
	type: ClientType;
	/** The owning kår, or null for clients only admins manage. */
	owner: string | null;
	/** Keycloak's own clients and this GUI's: read-only here. */
	protected: boolean;
	endpoints: string[];
}

export interface ClientDetail extends ClientSummary {
	protocol: string;
	hasSecret: boolean;
	redirectUris: string[];
	postLogoutRedirectUris: string[];
	webOrigins: string[];
	defaultClientScopes: string[];
	memberships: boolean;
	acsUrl: string | null;
}

export interface CreateRequest {
	preset: string;
	owner: string | null;
	/** Empty lets the server derive it (SAML entity ID, Google Workspace name). */
	clientId: string;
	name: string;
	domain: string;
	/** Empty lets the server derive it from the domain. */
	endpoint: string;
	memberships: boolean;
	karId: string;
}

export interface Preview {
	payload: Record<string, unknown>;
	clientId: string;
	domain: string;
	endpoint: string;
}

export interface UpdateRequest {
	name?: string;
	enabled?: boolean;
	redirectUris?: string[];
	postLogoutRedirectUris?: string[];
	webOrigins?: string[];
	/** Admins only. Empty string removes the owner. */
	owner?: string;
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
	const token = await accessToken();
	if (!token) {
		login();
		throw new ApiError(401, "Inte inloggad.");
	}

	let response: Response;
	try {
		response = await fetch(`/api${path}`, {
			...init,
			headers: {
				...init?.headers,
				Authorization: `Bearer ${token}`,
				...(init?.body ? { "Content-Type": "application/json" } : {}),
			},
		});
	} catch (cause) {
		throw new ApiError(
			0,
			`Kunde inte nå servern (${(cause as Error).message}).`,
		);
	}

	if (response.status === 401) {
		// The token was refused (expired, revoked). Logging in again is silent
		// while the Keycloak SSO session lives.
		login();
	}

	if (!response.ok) {
		let detail = response.statusText;
		try {
			const body = await response.json();
			// FastAPI reports validation errors as a list of {msg}.
			detail = Array.isArray(body.detail)
				? body.detail.map((d: { msg: string }) => d.msg).join("; ")
				: (body.detail ?? detail);
		} catch {
			// Non-JSON error body — keep the status text.
		}
		throw new ApiError(response.status, detail);
	}

	if (response.status === 204) {
		return undefined as T;
	}
	return (await response.json()) as T;
}

const json = (method: string, body: unknown): RequestInit => ({
	method,
	body: JSON.stringify(body),
});

export const getMe = () => request<Me>("/me");

export const listPresets = () => request<Preset[]>("/presets");

export const previewClient = (req: CreateRequest) =>
	request<Preview>("/clients/preview", json("POST", req));

export const createClient = (req: CreateRequest) =>
	request<{ id: string; clientId: string; secret: string | null }>(
		"/clients",
		json("POST", req),
	);

export const listClients = () => request<ClientSummary[]>("/clients");

export const getClient = (id: string) =>
	request<ClientDetail>(`/clients/${id}`);

export const updateClient = (id: string, req: UpdateRequest) =>
	request<ClientDetail>(`/clients/${id}`, json("PUT", req));

export const deleteClient = (id: string) =>
	request<void>(`/clients/${id}`, { method: "DELETE" });

/**
 * Client secret for a confidential client. Users need it to configure the far
 * end, and having to leave for the Keycloak console would defeat the point of
 * this GUI.
 */
export const getClientSecret = (id: string) =>
	request<{ value: string }>(`/clients/${id}/secret`);

export const setMemberships = (id: string, on: boolean) =>
	request<void>(`/clients/${id}/memberships-scope`, {
		method: on ? "PUT" : "DELETE",
	});

export const MEMBERSHIP_SCOPE = "scoutnet-memberships";
