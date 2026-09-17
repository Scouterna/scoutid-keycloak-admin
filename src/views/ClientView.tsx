import { useEffect, useState } from "react";
import {
	addDefaultClientScope,
	getClient,
	getClientSecret,
	getDefaultClientScopes,
	type KcClient,
	listClientScopes,
	removeDefaultClientScope,
	updateClient,
} from "../api";
import { describeClient, hasSecret, MEMBERSHIP_SCOPE } from "../presets";

/** Keycloak stores post-logout redirect URIs as one "##"-separated attribute. */
const POST_LOGOUT_ATTR = "post.logout.redirect.uris";

export function ClientView({ id }: { id: string }) {
	const [client, setClient] = useState<KcClient | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [saving, setSaving] = useState(false);
	const [saved, setSaved] = useState(false);
	const [secret, setSecret] = useState<string | null>(null);

	// Editable fields, kept separate so Cancel is just a re-fetch.
	const [name, setName] = useState("");
	const [redirectUris, setRedirectUris] = useState("");
	const [postLogoutUris, setPostLogoutUris] = useState("");
	const [webOrigins, setWebOrigins] = useState("");
	const [enabled, setEnabled] = useState(true);

	/**
	 * The membership scope is assigned through its own endpoint rather than the
	 * client representation, so it is tracked separately: its realm-wide id, and
	 * whether this client currently has it.
	 */
	const [membershipScopeId, setMembershipScopeId] = useState<string | null>(
		null,
	);
	const [hasMemberships, setHasMemberships] = useState(false);
	const [scopeBusy, setScopeBusy] = useState(false);

	useEffect(() => {
		getClient(id)
			.then((c) => {
				setClient(c);
				setName(c.name ?? "");
				setRedirectUris((c.redirectUris ?? []).join("\n"));
				setPostLogoutUris(
					(c.attributes?.[POST_LOGOUT_ATTR] ?? "").split("##").join("\n"),
				);
				setWebOrigins((c.webOrigins ?? []).join("\n"));
				setEnabled(c.enabled);
			})
			.catch((e: Error) => setError(e.message));
	}, [id]);

	useEffect(() => {
		Promise.all([listClientScopes(), getDefaultClientScopes(id)])
			.then(([all, assigned]) => {
				setMembershipScopeId(
					all.find((s) => s.name === MEMBERSHIP_SCOPE)?.id ?? null,
				);
				setHasMemberships(assigned.some((s) => s.name === MEMBERSHIP_SCOPE));
			})
			.catch((e: Error) => setError(e.message));
	}, [id]);

	const toggleMemberships = async () => {
		if (!membershipScopeId) {
			return;
		}
		setScopeBusy(true);
		setError(null);
		try {
			if (hasMemberships) {
				await removeDefaultClientScope(id, membershipScopeId);
				setHasMemberships(false);
			} else {
				await addDefaultClientScope(id, membershipScopeId);
				setHasMemberships(true);
			}
		} catch (e) {
			setError((e as Error).message);
		} finally {
			setScopeBusy(false);
		}
	};

	const save = async () => {
		setSaving(true);
		setError(null);
		try {
			const lines = (value: string) =>
				value
					.split("\n")
					.map((line) => line.trim())
					.filter(Boolean);
			await updateClient(id, {
				name,
				enabled,
				redirectUris: lines(redirectUris),
				webOrigins: lines(webOrigins),
				// Keycloak merges attributes on update, and an empty value removes
				// the key, so sending only this one leaves the others untouched.
				...(client?.protocol !== "saml"
					? {
							attributes: {
								[POST_LOGOUT_ATTR]: lines(postLogoutUris).join("##"),
							},
						}
					: {}),
			});
			setSaved(true);
		} catch (e) {
			setError((e as Error).message);
		} finally {
			setSaving(false);
		}
	};

	if (error && !client) {
		return (
			<p className="rounded border border-red-300 bg-red-50 p-3 text-red-800">
				{error}
			</p>
		);
	}
	if (!client) {
		return <p className="text-slate-500">Laddar…</p>;
	}

	return (
		<div>
			<p className="mb-2">
				<a href="#/clients" className="text-blue-700 underline">
					← Lista klienter
				</a>
			</p>
			<h2 className="mb-4 text-xl font-semibold text-slate-900">
				{client.clientId}
			</h2>

			<dl className="mb-6 grid grid-cols-[10rem_1fr] gap-x-4 gap-y-1 text-sm">
				<dt className="text-slate-500">Typ</dt>
				<dd>{describeClient(client)}</dd>
				{client.attributes?.saml_assertion_consumer_url_post ? (
					<>
						<dt className="text-slate-500">ACS-URL</dt>
						<dd className="break-all font-mono text-xs">
							{client.attributes.saml_assertion_consumer_url_post}
						</dd>
					</>
				) : null}
				<dt className="text-slate-500">Client scopes</dt>
				<dd>{(client.defaultClientScopes ?? []).join(", ") || "—"}</dd>
				<dt className="text-slate-500">Keycloak-ID</dt>
				<dd className="font-mono text-xs">{client.id}</dd>
			</dl>

			{client.protocol !== "saml" ? (
				<fieldset className="mb-6 rounded border border-slate-300 p-3">
					<legend className="px-1 font-medium text-slate-800">
						Organisationsdata
					</legend>
					<label className="flex items-start gap-3">
						<input
							type="checkbox"
							className="mt-1"
							checked={hasMemberships}
							disabled={scopeBusy || !membershipScopeId}
							onChange={toggleMemberships}
						/>
						<span>
							<span className="block font-medium text-slate-900">
								<code>{MEMBERSHIP_SCOPE}</code>
								{scopeBusy ? " — sparar…" : null}
							</span>
							<span className="block text-sm text-slate-600">
								Ger klienten användarens kår och roller i kåren (t.ex.{" "}
								<code>it_manager</code>). Ändringen sparas direkt.
							</span>
							{!membershipScopeId ? (
								<span className="mt-1 block text-sm text-amber-700">
									Scopet finns inte i den här realmen.
								</span>
							) : null}
						</span>
					</label>
				</fieldset>
			) : null}

			{hasSecret(client) ? (
				<div className="mb-6">
					{secret ? (
						<pre className="overflow-x-auto rounded border border-amber-300 bg-amber-50 p-3 font-mono text-sm">
							{secret}
						</pre>
					) : (
						<button
							type="button"
							className="rounded border border-slate-300 px-3 py-1 text-sm"
							onClick={async () => {
								try {
									setSecret((await getClientSecret(client.id)).value);
								} catch (e) {
									setError((e as Error).message);
								}
							}}
						>
							Visa client secret
						</button>
					)}
				</div>
			) : null}

			<div className="grid gap-4">
				<label className="grid gap-1">
					<span className="font-medium text-slate-800">Namn</span>
					<input
						className="rounded border border-slate-300 px-2 py-1"
						value={name}
						onChange={(e) => setName(e.target.value)}
					/>
				</label>

				<label className="grid gap-1">
					<span className="font-medium text-slate-800">
						Redirect-URI:er (en per rad)
					</span>
					<textarea
						className="rounded border border-slate-300 px-2 py-1 font-mono text-sm"
						rows={4}
						value={redirectUris}
						onChange={(e) => setRedirectUris(e.target.value)}
					/>
				</label>

				{client.protocol !== "saml" ? (
					<label className="grid gap-1">
						<span className="font-medium text-slate-800">
							Post logout redirect-URI:er (en per rad)
						</span>
						<textarea
							className="rounded border border-slate-300 px-2 py-1 font-mono text-sm"
							rows={2}
							value={postLogoutUris}
							onChange={(e) => setPostLogoutUris(e.target.value)}
						/>
						<span className="text-sm text-slate-500">
							<code>+</code> betyder samma som redirect-URI:erna ovan, så nya
							redirect-URI:er gäller automatiskt även vid utloggning. Tomt
							tillåter ingen omdirigering efter utloggning.
						</span>
					</label>
				) : null}

				<label className="grid gap-1">
					<span className="font-medium text-slate-800">
						Web origins (en per rad)
					</span>
					<textarea
						className="rounded border border-slate-300 px-2 py-1 font-mono text-sm"
						rows={3}
						value={webOrigins}
						onChange={(e) => setWebOrigins(e.target.value)}
					/>
				</label>

				<label className="flex items-center gap-2">
					<input
						type="checkbox"
						checked={enabled}
						onChange={(e) => setEnabled(e.target.checked)}
					/>
					<span className="font-medium text-slate-800">Aktiverad</span>
				</label>

				{error ? (
					<p className="rounded border border-red-300 bg-red-50 p-3 text-red-800">
						{error}
					</p>
				) : null}
				{saved ? <p className="text-green-700">Sparat.</p> : null}

				<div>
					<button
						type="button"
						onClick={save}
						disabled={saving}
						className="rounded bg-blue-700 px-4 py-2 font-medium text-white disabled:opacity-50"
					>
						{saving ? "Sparar…" : "Spara"}
					</button>
				</div>
			</div>
		</div>
	);
}
