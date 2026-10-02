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

**Status: demo.** Create, edit and delete clients; deployed to ScoutID staging.

> [!IMPORTANT]
> **How ScoutID clients should be configured is documented in
> [`docs/client_config_guide.md`](https://github.com/Scouterna/scoutid-keycloak-provider/blob/main/docs/client_config_guide.md)
> in `scoutid-keycloak-provider`.** That guide is the authority — it covers
> Google Workspace, Microsoft 365, Joomla, Photoprism and a standard OIDC
> recipe, plus the available ScoutID scopes and claims. This README documents
> *this application*, not how to integrate services with ScoutID. Where the two
> disagree, the guide is right.

## Presets

The GUI's value over the generic Keycloak console is that it asks for a couple
of fields and expands them into a complete client, instead of requiring the
right answers in ~40 console fields. Presets live in
[`src/app/presets.py`](src/app/presets.py) on the server, and the create form
shows the exact JSON that will be sent before you submit.

- **Google Workspace (OIDC)** — follows the provider guide's Google Workspace
  section: confidential client named `<kårid>-google-workspace`, root URL
  `https://accounts.google.com`, and a mapper from `group_email_<kårid>` to the
  `email` claim. Two steps stay manual and the form says so: setting the
  `domain` attribute on the kår's Keycloak group, and pasting back the redirect
  URI that Google issues once the SSO profile exists.
- **OIDC web app / SPA / backend service** — the generic shapes from the
  guide's *Standard OIDC* section.
- **SAML (kårwebb and generic)** — for the legacy integration shape. The
  existing estate is SAML today, but new integrations are expected to be OIDC;
  see the guide. These presets set endpoints and flow flags only — they do not
  configure attribute release.

## Legacy migration helper

[`scripts/migrate-saml-sps.py`](scripts/migrate-saml-sps.py) reads a TSV export
of the old SimpleSAMLphp `saml20_sp_remote` table and reports what a
like-for-like mapping to Keycloak SAML clients would produce. **It is a dry run
by default, writes nothing, and needs no credentials.**

It is an analysis aid, not a migration plan: it reproduces the *legacy* shape,
and how those services should actually be configured in the new ScoutID is
decided in the provider guide.

```sh
./scripts/migrate-saml-sps.py sp_all.tsv --report
```

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

The scope's exact claims are listed in the provider guide's *Tillgängliga
ScoutID-attribut* table.

## Architecture

One container: a FastAPI backend that serves the built SPA and proxies the
Keycloak Admin REST API, forwarding the user's own token.

```
browser ──> Keycloak (public host)   login: Authorization Code + PKCE, public client
        ──> /                         SPA (Vite build)
        ──> /api/*  + user's token    permission-checked proxy ──> Admin REST API (internal)
```

- **Login** happens in the browser with
  [oidc-client-ts](https://github.com/authts/oidc-client-ts), against the
  public client `scoutid-admin-gui`. Where to log in comes from `/api/config`.
- **Every `/api` call carries the user's access token.** The backend verifies
  it ([`src/app/auth.py`](src/app/auth.py): signature, issuer, `azp`, `typ`,
  expiry), derives the Scoutnet permissions from its claims, and forwards the
  call **with the same token** to the Admin API over the in-cluster Keycloak
  Service ([`src/app/keycloak.py`](src/app/keycloak.py)).
- **The browser never calls the Admin API**, so the admin host can stay behind
  its IP allowlist. The app holds no secret and no service account; Keycloak's
  admin events show the real user.
- **The browser never sends a Keycloak representation.** It names a preset and
  fills in a few fields, or edits a fixed set of fields; the server builds what
  Keycloak receives. Anything else in a request body is ignored.

## Permissions

Two gates must both allow a call:

1. **Keycloak roles, assigned by hand.** The user needs `manage-clients` (or
   `view-clients` for read-only) on the `realm-management` client, because the
   Admin API sees their own token. Without it the GUI says the account lacks
   the role.
2. **Scoutnet roles, enforced by the backend**, which decide *which* clients.
   They come from the `memberships` claim of the `scoutnet-memberships` scope
   ([`src/app/authz.py`](src/app/authz.py)):

| Who | Claim | May |
|---|---|---|
| Admin | a role with id `235` in `memberships.organisations["692"]` | manage every client, and assign clients to kårer |
| Kår IT manager | a role with id `136` (`it_manager`) in `memberships.groups[<kår>]` | create and manage clients owned by that kår |

- **Ownership** is the client attribute `scoutid.owner.group=<kår>`. That
  attribute, not the name, decides access.
- **OIDC clients owned by a kår are named `<kår>-<name>`**, e.g.
  `784-photoprism`, so kårer cannot collide. SAML clients keep their entity ID,
  which the service dictates.
- **Clients without an owner** (migrated SAML SPs, older clients) are visible to
  admins only. An admin hands one over by setting its kår.
- **Keycloak's built-in clients and this GUI's own** are read-only here.
- IT managers are limited to concrete `https://` redirect URIs and web origins
  (no wildcard hosts); admins are not.
- A client the user may not see answers 404, not 403.
- Changes to Scoutnet roles take effect when the token is next refreshed.

> [!WARNING]
> The provider truncates the `memberships` attribute at 2048 characters, and
> the truncated form drops `organisations`. Someone with very many roles can
> therefore lose admin rights. The GUI says so when it happens.

The ids are configurable (`ADMIN_ORG_ID`, `ADMIN_ROLE_ID`,
`GROUP_MANAGER_ROLE_ID`). `ADMIN_CLIENT_ROLE` is a hook for also granting admin
through a client role on `scoutid-admin-gui`; it is empty, so unused, by
default.

All writes are logged with the acting user (`AUDIT …` lines), and appear under
that user in Keycloak's admin events too.

## Configuration

Environment variables, read by [`src/app/config.py`](src/app/config.py). See
[`.env.example`](.env.example).

| Variable | Example | Purpose |
|---|---|---|
| `KC_PUBLIC_URL` | `https://id.scouterna.se` | Keycloak as the browser sees it; base of the expected issuer |
| `KC_INTERNAL_URL` | `http://scoutid-keycloak:8080` | Keycloak as the backend sees it: JWKS and Admin API |
| `KC_REALM` | `scoutid` | |
| `KC_CLIENT_ID` | `scoutid-admin-gui` | The SPA's client; tokens must have been issued to it |
| `ADMIN_ORG_ID`, `ADMIN_ROLE_ID`, `GROUP_MANAGER_ROLE_ID` | `692`, `235`, `136` | See [Permissions](#permissions) |
| `ADMIN_CLIENT_ROLE` | *(empty)* | |

### Required Keycloak client

A public client with PKCE. In the ScoutID deployment it is declared in
`scoutid-keycloak-infra` (keycloak-config-cli):

```yaml
- clientId: scoutid-admin-gui
  publicClient: true
  standardFlowEnabled: true
  directAccessGrantsEnabled: false
  redirectUris: [https://<host>/*]
  webOrigins: [https://<host>]      # the SPA calls Keycloak's token endpoint
  attributes:
    pkce.code.challenge.method: S256
    post.logout.redirect.uris: https://<host>/*
  optionalClientScopes: [scoutnet-memberships]
```

Users of the GUI need `manage-clients` on `realm-management`, assigned under
**Users → Role mapping → Assign role → Filter by clients**. The user record
(`<memberNo>@scoutnet`) exists only after the person's first login.

## Container image

Built and pushed to `ghcr.io/scouterna/scoutid-keycloak-admin` by
[.github/workflows/build-image.yml](.github/workflows/build-image.yml) on every
push (`type=sha` tags, plus `latest` on the default branch), after the SPA lint
and build and the backend's `ruff` and `pytest` have passed.

The image runs as uid 10001, listens on 8080, holds no secrets, writes nothing
at runtime (a read-only root filesystem works), and answers `/healthz` for
probes.

## Development

Backend (Python 3.14, [uv](https://docs.astral.sh/uv/)):

```sh
uv sync
cp .env.example src/.env
kubectl -n proj-scoutid-staging port-forward svc/scoutid-keycloak 8081:8080   # KC_INTERNAL_URL
cd src && uv run python start.py               # http://127.0.0.1:8080
uv run pytest
uv run ruff check && uv run ruff format --check
```

Frontend:

```sh
pnpm install
pnpm dev      # http://localhost:5173, proxies /api to the backend
pnpm build
pnpm lint
```

Logging in through `pnpm dev` needs `http://localhost:5173/*` among the
client's redirect URIs and `http://localhost:5173` among its web origins.

## Known gaps

- **No migrated SAML client has completed a real login.** The client shape is
  derived from the legacy data and accepted by Keycloak, but signing behaviour
  and NameID handling are only proven by an end-to-end test against a real
  service provider.
- **No import / export**, as the old admin GUI had.
- Client secret **regeneration** (the current secret can be displayed).
- Editing a SAML client's redirect URIs does not update its ACS attributes.
