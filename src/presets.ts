import type { KcClient } from "./api";

/**
 * Client presets — the reason this GUI exists rather than sending admins to the
 * generic Keycloak console. Each preset expands a single domain into the
 * endpoints and flags that kind of integration needs, the same way the old
 * ScoutID admin turned a domain into a Service Provider.
 *
 * Both protocols are offered because the existing ScoutID estate is
 * overwhelmingly SAML, while new integrations are expected to be OIDC — so both
 * must be creatable.
 */
export type PresetId =
	| "google-workspace"
	| "karwebb"
	| "saml-generic"
	| "webapp"
	| "spa"
	| "service";

/** Identity scopes every OIDC client gets: who the user is, nothing more. */
const SCOUTID_OIDC_SCOPES = ["profile", "email", "phone"];

/**
 * Organisational data: primary group, and every group membership with the roles
 * held in it (`it_manager`, `member_registrar`, …). Opt-in, for two reasons:
 *
 * - It is personal data beyond identity. A client that only needs to know *who*
 *   someone is should not receive their position in the organisation.
 * - It roughly doubles the token. The memberships and group_emails_json claims
 *   are JSON blobs that grow with the number of groups and roles a person holds.
 *
 * Kårwebbar need it — that is how a group's IT managers are recognised.
 */
export const MEMBERSHIP_SCOPE = "scoutnet-memberships";

export interface Preset {
	id: PresetId;
	label: string;
	description: string;
	protocol: "openid-connect" | "saml";
	/** Whether the form should ask for a domain and expand it. */
	needsDomain: boolean;
	/** Whether the form should ask for the kår's Scoutnet id (e.g. 766). */
	needsKarId?: boolean;
	/**
	 * Endpoint the preset derives from the domain, shown in the form so the
	 * admin can correct it before saving. Empty when the preset takes no domain.
	 */
	endpoint?(domain: string): string;
	/** Whether the membership scope is checked by default for this preset. */
	membershipsByDefault: boolean;
	build(input: {
		clientId: string;
		name: string;
		domain: string;
		endpoint: string;
		memberships: boolean;
		karId: string;
	}): Partial<KcClient>;
}

/**
 * Normalise a pasted domain, preserving a subdirectory when there is one.
 *
 * WordPress kårwebbar are commonly served from a `/wp` subdirectory: roughly
 * half the legacy SAML SPs are keyed `<host>/wp` with their ACS at
 * `/wp/wp-login.php`, the rest at the site root. Verified against a live login,
 * whose SAMLRequest carries Issuer=`<host>/wp` and
 * ACS=`https://<host>/wp/wp-login.php`.
 *
 * So a trailing path is meaningful, not noise — it becomes part of the entity
 * ID and of every derived endpoint. Only the scheme, query, fragment and
 * trailing slash are stripped.
 */
export function normalizeDomain(raw: string): string {
	let domain = raw.trim();
	domain = domain.replace(/^https?:\/\//, "");
	domain = domain.replace(/[?#].*$/, "");
	// Drop a trailing wp-login.php / wp-admin path, so pasting the address bar
	// of a logged-in kårwebb yields the site root rather than a nonsense entity.
	domain = domain.replace(/\/wp-login\.php.*$/, "");
	domain = domain.replace(/\/wp-admin(\/.*)?$/, "");
	domain = domain.replace(/\/+$/, "");
	return domain;
}

/** Host part only, for web origins (which cannot carry a path). */
export function originOf(domain: string): string {
	return `https://${domain.split("/")[0]}`;
}

const oidcBase = (
	clientId: string,
	name: string,
	memberships: boolean,
): Partial<KcClient> => ({
	clientId,
	name: name || clientId,
	protocol: "openid-connect",
	enabled: true,
	defaultClientScopes: memberships
		? [...SCOUTID_OIDC_SCOPES, MEMBERSHIP_SCOPE]
		: SCOUTID_OIDC_SCOPES,
});

const samlClient = (
	clientId: string,
	name: string,
	acs: string,
): Partial<KcClient> => ({
	clientId,
	name: name || clientId,
	protocol: "saml",
	enabled: true,
	frontchannelLogout: true,
	// No client scopes are set: attribute release for SAML clients is not
	// configured by this GUI. See docs/client_config_guide.md in
	// scoutid-keycloak-provider for how clients should be set up.
	redirectUris: [acs],
	adminUrl: acs,
	attributes: {
		saml_name_id_format: "email",
		saml_assertion_consumer_url_post: acs,
		saml_assertion_consumer_url_redirect: acs,
		saml_single_logout_service_url_post: acs,
		// The legacy IdP signs assertions but does not require the SP to sign
		// its requests — the WordPress sites have no signing keys configured.
		"saml.assertion.signature": "true",
		"saml.server.signature": "true",
		"saml.client.signature": "false",
		"saml.authnstatement": "true",
		saml_force_name_id_format: "true",
	},
});

export const presets: Preset[] = [
	{
		id: "google-workspace",
		label: "Google Workspace (OIDC)",
		description:
			"SSO för en kårs Google Workspace. Följer client_config_guide.md i scoutid-keycloak-provider.",
		protocol: "openid-connect",
		needsDomain: false,
		needsKarId: true,
		membershipsByDefault: false,
		build: ({ clientId, name, karId }) => ({
			clientId,
			name: name || clientId,
			protocol: "openid-connect",
			enabled: true,
			publicClient: false,
			standardFlowEnabled: true,
			serviceAccountsEnabled: false,
			rootUrl: "https://accounts.google.com",
			// Google issues the redirect URI only once the SSO profile exists, so
			// it is pasted in afterwards on the client's page.
			redirectUris: [],
			// Deliberately no `email` scope: the built-in one would emit an `email`
			// claim from the user's personal address, colliding with the mapper
			// below that must supply the Workspace address instead.
			defaultClientScopes: ["profile"],
			protocolMappers: [
				{
					// The access restriction: group_email_<karId> exists only for
					// members of that kår (ScoutnetProfileSync sets it from the
					// group's `domain` attribute). A non-member gets no email claim,
					// so Google cannot match them to an account.
					name: "group_email_mapper",
					protocol: "openid-connect",
					protocolMapper: "oidc-usermodel-attribute-mapper",
					config: {
						"user.attribute": `group_email_${karId}`,
						"claim.name": "email",
						"jsonType.label": "String",
						"id.token.claim": "true",
						"userinfo.token.claim": "true",
						"lightweight.claim": "true",
					},
				},
			],
		}),
	},
	{
		id: "karwebb",
		label: "Kårwebb (SAML)",
		description:
			"WordPress-based kårwebb. Matches how kårwebbar are integrated today.",
		protocol: "saml",
		needsDomain: true,
		// Almost every legacy SP posts the assertion to wp-login.php. Sites that
		// serve WordPress from a /wp subdirectory use /wp/wp-login.php, so the
		// derived value is editable in the form.
		endpoint: (domain) => `https://${domain}/wp-login.php`,
		// SAML clients release the legacy attribute set, which already carries
		// group and role data; the OIDC membership scope does not apply.
		membershipsByDefault: false,
		build: ({ clientId, name, endpoint }) =>
			samlClient(clientId, name, endpoint),
	},
	{
		id: "saml-generic",
		label: "Övrig SAML-tjänst",
		description:
			"Any other SAML service provider. Supply the ACS URL from its metadata.",
		protocol: "saml",
		needsDomain: true,
		endpoint: (domain) => `https://${domain}/`,
		membershipsByDefault: false,
		build: ({ clientId, name, endpoint }) =>
			samlClient(clientId, name, endpoint),
	},
	{
		id: "webapp",
		label: "Webbapplikation (OIDC)",
		description:
			"Server-side web app that can keep a client secret (confidential).",
		protocol: "openid-connect",
		needsDomain: true,
		endpoint: (domain) => `https://${domain}/*`,
		membershipsByDefault: false,
		build: ({ clientId, name, domain, endpoint, memberships }) => ({
			...oidcBase(clientId, name, memberships),
			publicClient: false,
			standardFlowEnabled: true,
			serviceAccountsEnabled: false,
			redirectUris: [endpoint],
			// Web origins are scheme+host only; a subdirectory is not valid here.
			webOrigins: [originOf(domain)],
			attributes: { "post.logout.redirect.uris": `https://${domain}/*` },
		}),
	},
	{
		id: "spa",
		label: "SPA / frontend (OIDC)",
		description:
			"Browser app using Authorization Code + PKCE. No client secret.",
		protocol: "openid-connect",
		needsDomain: true,
		endpoint: (domain) => `https://${domain}/*`,
		membershipsByDefault: false,
		build: ({ clientId, name, domain, endpoint, memberships }) => ({
			...oidcBase(clientId, name, memberships),
			publicClient: true,
			standardFlowEnabled: true,
			serviceAccountsEnabled: false,
			redirectUris: [endpoint],
			// Web origins are scheme+host only; a subdirectory is not valid here.
			webOrigins: [originOf(domain)],
			attributes: {
				"pkce.code.challenge.method": "S256",
				"post.logout.redirect.uris": `https://${domain}/*`,
			},
		}),
	},
	{
		id: "service",
		label: "Backend-tjänst (OIDC)",
		description:
			"Machine-to-machine client using client credentials. No user login.",
		protocol: "openid-connect",
		needsDomain: false,
		membershipsByDefault: false,
		build: ({ clientId, name, memberships }) => ({
			...oidcBase(clientId, name, memberships),
			publicClient: false,
			standardFlowEnabled: false,
			serviceAccountsEnabled: true,
			redirectUris: [],
			webOrigins: [],
		}),
	},
];

export const presetById = (id: PresetId) =>
	presets.find((p) => p.id === id) as Preset;

/** Label for an existing client, for the list view's type column. */
export function describeClient(client: KcClient): string {
	if (client.protocol === "saml") {
		return "saml";
	}
	if (client.serviceAccountsEnabled && !client.standardFlowEnabled) {
		return "service";
	}
	return client.publicClient ? "public" : "confidential";
}

/** True when the client has a secret an admin may need to copy. */
export function hasSecret(client: KcClient): boolean {
	return client.protocol !== "saml" && !client.publicClient;
}
