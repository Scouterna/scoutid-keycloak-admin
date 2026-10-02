import { useCallback, useEffect, useState } from "react";
import {
	type ClientDetail,
	deleteClient,
	getClient,
	getClientSecret,
	MEMBERSHIP_SCOPE,
	type Me,
	setMemberships,
	type UpdateRequest,
	updateClient,
} from "../api";

const lines = (value: string) =>
	value
		.split("\n")
		.map((line) => line.trim())
		.filter(Boolean);

export function ClientView({ id, me }: { id: string; me: Me }) {
	const [client, setClient] = useState<ClientDetail | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [saving, setSaving] = useState(false);
	const [saved, setSaved] = useState(false);
	const [secret, setSecret] = useState<string | null>(null);
	const [scopeBusy, setScopeBusy] = useState(false);

	// Editable fields, filled from the client whenever it is (re)loaded.
	const [name, setName] = useState("");
	const [redirectUris, setRedirectUris] = useState("");
	const [postLogoutUris, setPostLogoutUris] = useState("");
	const [webOrigins, setWebOrigins] = useState("");
	const [enabled, setEnabled] = useState(true);
	const [owner, setOwner] = useState("");

	const load = useCallback((c: ClientDetail) => {
		setClient(c);
		setName(c.name);
		setRedirectUris(c.redirectUris.join("\n"));
		setPostLogoutUris(c.postLogoutRedirectUris.join("\n"));
		setWebOrigins(c.webOrigins.join("\n"));
		setEnabled(c.enabled);
		setOwner(c.owner ?? "");
	}, []);

	useEffect(() => {
		getClient(id)
			.then(load)
			.catch((e: Error) => setError(e.message));
	}, [id, load]);

	// A field edited after a save makes "Sparat." stale.
	const edit =
		<T,>(setter: (value: T) => void) =>
		(value: T) => {
			setter(value);
			setSaved(false);
		};

	const toggleMemberships = async () => {
		if (!client) {
			return;
		}
		setScopeBusy(true);
		setError(null);
		try {
			await setMemberships(id, !client.memberships);
			setClient({ ...client, memberships: !client.memberships });
		} catch (e) {
			setError((e as Error).message);
		} finally {
			setScopeBusy(false);
		}
	};

	const save = async () => {
		if (!client) {
			return;
		}
		setSaving(true);
		setError(null);
		try {
			const req: UpdateRequest = {
				name,
				enabled,
				redirectUris: lines(redirectUris),
				webOrigins: lines(webOrigins),
				...(client.protocol !== "saml"
					? { postLogoutRedirectUris: lines(postLogoutUris) }
					: {}),
				...(me.isAdmin && owner.trim() !== (client.owner ?? "")
					? { owner: owner.trim() }
					: {}),
			};
			load(await updateClient(id, req));
			setSaved(true);
		} catch (e) {
			setError((e as Error).message);
		} finally {
			setSaving(false);
		}
	};

	const remove = async () => {
		if (!client) {
			return;
		}
		if (
			!window.confirm(
				`Ta bort klienten ${client.clientId}? Tjänsten kan inte längre logga in med ScoutID, och det går inte att ångra.`,
			)
		) {
			return;
		}
		try {
			await deleteClient(id);
			window.location.hash = "#/clients";
		} catch (e) {
			setError((e as Error).message);
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

	const readOnly = client.protected;
	const oidc = client.protocol !== "saml";
	const ownerName = client.owner ? me.groups[client.owner] : undefined;

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

			{readOnly ? (
				<p className="mb-4 rounded border border-amber-300 bg-amber-50 p-3 text-amber-900">
					Keycloaks inbyggda klienter, och den här GUI:ns egen, kan inte ändras
					härifrån.
				</p>
			) : null}

			<dl className="mb-6 grid grid-cols-[10rem_1fr] gap-x-4 gap-y-1 text-sm">
				<dt className="text-slate-500">Kår</dt>
				<dd>
					{client.owner
						? `${client.owner}${ownerName ? ` ${ownerName}` : ""}`
						: "— (bara administratörer)"}
				</dd>
				<dt className="text-slate-500">Typ</dt>
				<dd>{client.type}</dd>
				{client.acsUrl ? (
					<>
						<dt className="text-slate-500">ACS-URL</dt>
						<dd className="break-all font-mono text-xs">{client.acsUrl}</dd>
					</>
				) : null}
				<dt className="text-slate-500">Client scopes</dt>
				<dd>{client.defaultClientScopes.join(", ") || "—"}</dd>
				<dt className="text-slate-500">Keycloak-ID</dt>
				<dd className="font-mono text-xs">{client.id}</dd>
			</dl>

			{oidc && !readOnly ? (
				<fieldset className="mb-6 rounded border border-slate-300 p-3">
					<legend className="px-1 font-medium text-slate-800">
						Organisationsdata
					</legend>
					<label className="flex items-start gap-3">
						<input
							type="checkbox"
							className="mt-1"
							checked={client.memberships}
							disabled={scopeBusy}
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
						</span>
					</label>
				</fieldset>
			) : null}

			{client.hasSecret ? (
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

			<fieldset disabled={readOnly} className="grid gap-4">
				{me.isAdmin ? (
					<label className="grid gap-1">
						<span className="font-medium text-slate-800">Kår</span>
						<input
							className="w-40 rounded border border-slate-300 px-2 py-1"
							value={owner}
							onChange={(e) => edit(setOwner)(e.target.value)}
							placeholder="ingen"
							inputMode="numeric"
						/>
						<span className="text-sm text-slate-500">
							Kårens Scoutnet-ID. Kårens IT-ansvariga kan då hantera klienten.
							Tomt betyder att bara administratörer kan det.
						</span>
					</label>
				) : null}

				<label className="grid gap-1">
					<span className="font-medium text-slate-800">Namn</span>
					<input
						className="rounded border border-slate-300 px-2 py-1"
						value={name}
						onChange={(e) => edit(setName)(e.target.value)}
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
						onChange={(e) => edit(setRedirectUris)(e.target.value)}
					/>
				</label>

				{oidc ? (
					<label className="grid gap-1">
						<span className="font-medium text-slate-800">
							Post logout redirect-URI:er (en per rad)
						</span>
						<textarea
							className="rounded border border-slate-300 px-2 py-1 font-mono text-sm"
							rows={2}
							value={postLogoutUris}
							onChange={(e) => edit(setPostLogoutUris)(e.target.value)}
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
						onChange={(e) => edit(setWebOrigins)(e.target.value)}
					/>
				</label>

				<label className="flex items-center gap-2">
					<input
						type="checkbox"
						checked={enabled}
						onChange={(e) => edit(setEnabled)(e.target.checked)}
					/>
					<span className="font-medium text-slate-800">Aktiverad</span>
				</label>

				{error ? (
					<p className="rounded border border-red-300 bg-red-50 p-3 text-red-800">
						{error}
					</p>
				) : null}
				{saved ? <p className="text-green-700">Sparat.</p> : null}

				{!readOnly ? (
					<div className="flex justify-between">
						<button
							type="button"
							onClick={save}
							disabled={saving}
							className="rounded bg-blue-700 px-4 py-2 font-medium text-white disabled:opacity-50"
						>
							{saving ? "Sparar…" : "Spara"}
						</button>
						<button
							type="button"
							onClick={remove}
							className="rounded border border-red-300 px-4 py-2 font-medium text-red-700"
						>
							Ta bort klient
						</button>
					</div>
				) : null}
			</fieldset>
		</div>
	);
}
