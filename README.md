<div>
  <h1>ScoutID Keycloak Admin</h1>
</div>

> [!TIP]
> This repo is part of a family:
> - [scoutid-keycloak](https://github.com/Scouterna/scoutid-keycloak)
> - [scoutid-keycloak-provider](https://github.com/Scouterna/scoutid-keycloak-provider)
> - [scoutid-keycloak-theme](https://github.com/Scouterna/scoutid-keycloak-theme)
> - [scoutid-keycloak-helm](https://github.com/Scouterna/scoutid-keycloak-helm)

A simplified admin GUI for registering and maintaining OAuth clients in the
ScoutID Keycloak realm — a focused replacement for the handful of tasks admins
actually perform, rather than the full generic Keycloak admin console.

It is the successor to the `/admin` GUI in the old SimpleSAMLphp ScoutID
application, where admins registered SAML Service Providers by domain.

**Status: demo.** Read/create/edit of clients only.

## SAML and OIDC

The existing ScoutID estate is overwhelmingly SAML: the great majority of
integrations are SAML service providers — nearly all of them kårwebbar — against
a handful of OIDC clients.

So the GUI creates both protocols. The Kårwebb preset produces a **SAML** client,
because that is what kårwebbar are today; the OIDC presets exist for new
integrations.

Two details from the legacy data drive the presets:

- **ACS URL is `https://<domain>/wp-login.php`** for almost every kårwebb.
  Sites serving WordPress from a `/wp` subdirectory use `/wp/wp-login.php`, so
  the field is derived from the domain but stays editable.
- **Each site has two entities** (`<domain>` and `<domain>/wp`), covering both
  layouts — roughly half the estate uses the subdirectory form, so a migration
  that assumes one client per site will miss a large share of it. The
  subdirectory is part of the SAML entity ID, so the domain field keeps it:
  pasting `www.example.se/wp/wp-admin/` yields entity ID `www.example.se/wp` and
  ACS `https://www.example.se/wp/wp-login.php`.

Confirmed against a live kårwebb login (HAR capture): WordPress uses the
**WP SAML Auth** plugin (`wp-login.php?action=wp-saml-auth`), the SAMLRequest
carries `Issuer=<host>/wp`, and the IdP POSTs the `SAMLResponse` back to
`https://<host>/wp/wp-login.php`. No OAuth is involved anywhere in that flow.

The released attribute set is identical across essentially the whole estate
(`uid, email, firstname, lastname, firstlast, displayName, dob, group_name,
group_no, group_id, above_15, roles`), so it belongs on a shared client scope
(`scoutid-saml-attributes`) rather than being duplicated onto every client.

## Bulk migration

[`scripts/migrate-saml-sps.py`](scripts/migrate-saml-sps.py) maps the legacy
`saml20_sp_remote` table onto Keycloak SAML clients. **It is a dry run by
default and never writes to Keycloak or to the legacy database.**

It takes a TSV export of the legacy `saml20_sp_remote` table, two columns —
`entity_id` and `entity_data` — with tabs and newlines stripped from the data
column. Producing that export is left to whoever has read access to the legacy
database; the script itself needs no credentials.

```sh
./scripts/migrate-saml-sps.py sp_all.tsv --report
./scripts/migrate-saml-sps.py sp_all.tsv --out clients.json
```

Against a full export, all but a handful of rows map cleanly with no duplicate
client IDs. The remainder are legacy junk rather than mapping failures — test
entries, a wildcard entity, and rows whose `entity_data` is PHP-serialised
rather than JSON. Each needs a human decision, so the script reports them
instead of guessing.

## Why not just the Keycloak console?

Registering a client in the generic console means knowing which of ~40 fields
matter and getting redirect URIs and scopes right by hand. This GUI asks for a
*type* and a *domain*, then expands them into the correct redirect URIs, web
origins and flow flags — the same idea as the old ScoutID admin, which turned a
domain into a Service Provider. Presets live in
[`src/presets.ts`](src/presets.ts) and the create form shows the exact JSON that
will be sent before you submit.

## The `scoutnet-memberships` scope

OIDC clients get `profile`, `email` and `phone` by default — enough to know
*who* the user is. The `scoutnet-memberships` scope is **opt-in**, offered as a
checkbox when creating a client and toggleable afterwards from the client's
page.

It adds the user's primary group and every group membership with the roles held
in it (`it_manager`, `member_registrar`, …). Kårwebbar rely on it — that is how
a group's IT managers are recognised — but many services only need the user's
identity.

Two reasons it is not on by default:

- **It is personal data beyond identity.** A client that only needs to know who
  someone is should not learn their position in the organisation.
- **It roughly doubles the token.** Measured against the provider's own example
  token, the four added claims take it from ~1 000 to ~2 000 bytes, and the
  `memberships` and `group_emails_json` claims grow with the number of groups
  and roles a person holds.

SAML clients are unaffected: they release the legacy attribute set, which
carries group and role data of its own.

## Architecture

A static SPA with no backend of its own.

- The admin signs in with **Authorization Code + PKCE** against the ScoutID realm.
- The GUI calls the **Keycloak Admin REST API** directly from the browser with
  that user's token.
- Authorization is entirely Keycloak's: the realm-management roles on the
  signed-in account decide what the API permits. The GUI does not implement its
  own permission model.

Backend services typically reach the same API with a `client_credentials`
service account instead. That pattern is deliberately *not* used here — a
browser app cannot hold a client secret.

## Configuration

The host and realm must be set explicitly — there is no default, since this GUI
holds full admin rights over whichever realm it is pointed at. For local
development, create a `.env.local`:

```sh
VITE_KC_URL=https://<keycloak-admin-host>
VITE_KC_REALM=<realm>
VITE_KC_CLIENT_ID=scoutid-admin-gui
```

`VITE_KC_URL` must be a host that serves **both** the OIDC endpoints and
`/admin/realms/*`. Keycloak splits these across two hostnames — `KC_HOSTNAME`
serves `/realms/*` and `KC_HOSTNAME_ADMIN` serves those *plus* `/admin/*` — so
always use the admin hostname. Tokens are still issued by the public hostname,
so the `iss` claim will not match `VITE_KC_URL`; that is expected.

### CORS

The Admin REST API derives `Access-Control-Allow-Origin` from the **Web origins
of the client that issued the token** (the `azp` claim), not from the endpoint
being requested. A request from an origin missing on *this GUI's* client is
rejected by the browser with an opaque `NetworkError`, even when the API itself
returns 200.

Two consequences worth knowing when debugging:

- Testing the API with `curl` and a token from `admin-cli` will *never* show
  CORS headers, because `admin-cli` ships with empty Web origins. That is not
  evidence of a broken deployment.
- Preflight (`OPTIONS`) succeeds regardless, so a passing preflight says nothing
  about whether the real request will be allowed.

### Required Keycloak client

This GUI needs a public client in the realm. Create it once in the Keycloak
admin console:

| Setting | Value |
|---|---|
| Client ID | `scoutid-admin-gui` |
| Client authentication | Off (public) |
| Standard flow | Enabled |
| PKCE method | `S256` |
| Valid redirect URIs | `http://localhost:5173/*` |
| Web origins | `http://localhost:5173` |

Add the deployed origin to both lists when hosting it somewhere other than a
local dev server.

Admins using the GUI need the realm-management roles for managing clients.
Assign them per user under **Users → Role mapping → Assign role**, switching the
filter to **Filter by clients** (the roles live on the `realm-management`
client, so they are hidden under the default realm-roles filter):

| Role | Grants |
|---|---|
| `manage-clients` | Create, edit and delete clients — what this GUI needs |
| `view-clients` | Read-only; the list and view screens work, create/edit 403s |

For a Scoutnet-backed realm the user record is named `scoutnet|<memberNo>`, and
is created on that person's first login — so they must log in once before a
role can be assigned. Role mappings are stored in Keycloak and are not touched
by the ScoutID provider on subsequent logins, so they persist.

## Container image

Built and pushed to `ghcr.io/scouterna/scoutid-keycloak-admin` by
[.github/workflows/build-image.yml](.github/workflows/build-image.yml) on every
push (`type=sha` tags, plus `latest` on the default branch).

The image is a static nginx build with **no baked-in environment**. Vite inlines
`import.meta.env` at build time, so instead the app reads
`window.__SCOUTID_CONFIG__` from `/config.js`, which
[docker/entrypoint.sh](docker/entrypoint.sh) writes at container startup. One
image therefore serves any environment:

```sh
podman run -p 8080:8080 \
  -e KC_URL=https://<keycloak-admin-host> \
  -e KC_REALM=<realm> \
  -e KC_CLIENT_ID=scoutid-admin-gui \
  ghcr.io/scouterna/scoutid-keycloak-admin:latest
```

It runs unprivileged (uid 101) and listens on 8080. `/healthz` returns 200 for
probes.

## Deploying

The image is a self-contained static site, so deployment is whatever your
environment already uses — the only requirements are to serve it over HTTPS and
set the three `KC_*` environment variables described above.

Deployment manifests are intentionally not part of this repository.

> [!IMPORTANT]
> Whatever hostname you deploy on must also be added to the `scoutid-admin-gui`
> Keycloak client as a valid redirect URI (`https://<host>/*`) **and** a web
> origin (`https://<host>`). Without the web origin the Admin API calls fail
> with an opaque `NetworkError` — see [CORS](#cors) above.

## Development

```sh
pnpm install
pnpm dev      # http://localhost:5173
pnpm build
pnpm lint
pnpm format
```

The dev server is pinned to port 5173 (`strictPort`) because the port is part of
the redirect URI registered on the Keycloak client.

## Known gaps

- **No migrated SAML client has completed a real login.** The client shape is
  derived from the legacy data and accepted by Keycloak, but signing behaviour
  and NameID handling are only proven by an end-to-end test against a real
  service provider.
- **No delete.** Clients can be created and edited, not removed; use the
  Keycloak console.
- **No import / export**, as the old admin GUI had.
- Client secret **regeneration** (the current secret can be displayed).
