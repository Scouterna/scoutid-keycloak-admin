import { type FormEvent, useEffect, useMemo, useState } from "react";
import {
	type CreateRequest,
	createClient,
	listPresets,
	type Me,
	type Preset,
	type Preview,
	previewClient,
} from "../api";

/**
 * The form only collects a preset and a few fields. What gets created — the
 * full Keycloak client — is decided by the server, which also derives the
 * entity ID and endpoints; the form shows those back from /clients/preview.
 */
export function ClientCreate({ me }: { me: Me }) {
	const myGroups = Object.entries(me.groups);
	const [presets, setPresets] = useState<Preset[]>([]);
	const [presetId, setPresetId] = useState("");
	const [owner, setOwner] = useState(
		myGroups.length === 1 ? myGroups[0][0] : "",
	);
	/** What the user typed; empty means "use what the server derives". */
	const [clientIdInput, setClientIdInput] = useState("");
	const [name, setName] = useState("");
	const [domain, setDomain] = useState("");
	const [endpointInput, setEndpointInput] = useState("");
	const [memberships, setMemberships] = useState(false);
	const [karId, setKarId] = useState("");
	const [preview, setPreview] = useState<Preview | null>(null);
	const [previewError, setPreviewError] = useState<string | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [saving, setSaving] = useState(false);
	const [created, setCreated] = useState<{
		id: string;
		clientId: string;
		secret: string | null;
	} | null>(null);

	useEffect(() => {
		listPresets()
			.then(setPresets)
			.catch((e: Error) => setError(e.message));
	}, []);

	const preset = presets.find((p) => p.id === presetId) ?? null;
	const ownerId = owner.trim();
	// OIDC clients owned by a kår are named <kår>-<something>; the prefix is
	// fixed in the form and only the rest is typed.
	const prefix =
		preset?.protocol === "openid-connect" && ownerId ? `${ownerId}-` : "";

	const req: CreateRequest | null = useMemo(
		() =>
			preset
				? {
						preset: preset.id,
						owner: ownerId || null,
						clientId: clientIdInput ? `${prefix}${clientIdInput}` : "",
						name,
						domain,
						endpoint: endpointInput,
						memberships,
						karId,
					}
				: null,
		[
			preset,
			ownerId,
			prefix,
			clientIdInput,
			name,
			domain,
			endpointInput,
			memberships,
			karId,
		],
	);

	// Live preview of what will be sent, debounced while typing. The old admin
	// hid this normalisation, which made it hard to tell what had been created.
	useEffect(() => {
		if (!req) {
			setPreview(null);
			return;
		}
		const timer = window.setTimeout(() => {
			previewClient(req)
				.then((p) => {
					setPreview(p);
					setPreviewError(null);
				})
				.catch((e: Error) => {
					setPreview(null);
					setPreviewError(e.message);
				});
		}, 300);
		return () => window.clearTimeout(timer);
	}, [req]);

	const derivedClientId = preview?.clientId.startsWith(prefix)
		? preview.clientId.slice(prefix.length)
		: "";
	const effectiveKarId = ownerId || karId.trim();

	const submit = async (event: FormEvent) => {
		event.preventDefault();
		if (!req) {
			return;
		}
		setError(null);
		setSaving(true);
		try {
			const result = await createClient(req);
			if (result.secret) {
				setCreated(result);
			} else {
				window.location.hash = `#/clients/${result.id}`;
			}
		} catch (e) {
			setError((e as Error).message);
		} finally {
			setSaving(false);
		}
	};

	if (created?.secret) {
		return (
			<div>
				<h2 className="mb-4 text-xl font-semibold text-slate-900">
					Klient {created.clientId} skapad
				</h2>
				<p className="mb-2 text-slate-700">
					Client secret — kopiera nu, eller hämta den senare från klientens
					sida:
				</p>
				<pre className="mb-4 overflow-x-auto rounded border border-amber-300 bg-amber-50 p-3 font-mono text-sm">
					{created.secret}
				</pre>
				<a href={`#/clients/${created.id}`} className="text-blue-700 underline">
					Till klienten →
				</a>
			</div>
		);
	}

	return (
		<form onSubmit={submit}>
			<p className="mb-2">
				<a href="#/clients" className="text-blue-700 underline">
					← Lista klienter
				</a>
			</p>
			<h2 className="mb-4 text-xl font-semibold text-slate-900">
				Lägg till klient
			</h2>

			<OwnerField me={me} owner={owner} setOwner={setOwner} />

			<fieldset className="mb-6">
				<legend className="mb-2 font-medium text-slate-800">Typ</legend>
				<div className="grid gap-2">
					{presets.map((p) => (
						<label
							key={p.id}
							className="flex items-start gap-3 rounded border border-slate-300 p-3 has-checked:border-blue-600 has-checked:bg-blue-50"
						>
							<input
								type="radio"
								name="preset"
								value={p.id}
								checked={presetId === p.id}
								onChange={() => {
									setPresetId(p.id);
									setClientIdInput("");
									setEndpointInput("");
									setMemberships(p.membershipsByDefault);
								}}
								className="mt-1"
							/>
							<span>
								<span className="block font-medium text-slate-900">
									{p.label}
								</span>
								<span className="block text-sm text-slate-600">
									{p.description}
								</span>
							</span>
						</label>
					))}
				</div>
			</fieldset>

			{preset ? (
				<div className="grid gap-4">
					<label className="grid gap-1">
						<span className="font-medium text-slate-800">
							{preset.protocol === "saml" ? "Entity ID" : "Client ID"} (krävs)
						</span>
						<span className="flex items-center">
							{prefix ? (
								<span className="rounded-l border border-r-0 border-slate-300 bg-slate-100 px-2 py-1 font-mono text-sm">
									{prefix}
								</span>
							) : null}
							<input
								className={`w-full border border-slate-300 px-2 py-1 ${prefix ? "rounded-r" : "rounded"}`}
								value={clientIdInput}
								onChange={(e) => setClientIdInput(e.target.value)}
								placeholder={derivedClientId}
							/>
						</span>
						{preset.protocol === "saml" ? (
							<span className="text-sm text-slate-500">
								Härleds från domänen om fältet lämnas tomt.
							</span>
						) : null}
					</label>

					<label className="grid gap-1">
						<span className="font-medium text-slate-800">Namn</span>
						<input
							className="rounded border border-slate-300 px-2 py-1"
							value={name}
							onChange={(e) => setName(e.target.value)}
							placeholder={preview?.clientId}
						/>
					</label>

					{preset.needsKarId && !ownerId ? (
						<label className="grid gap-1">
							<span className="font-medium text-slate-800">Kår-ID (krävs)</span>
							<input
								className="rounded border border-slate-300 px-2 py-1"
								value={karId}
								onChange={(e) => setKarId(e.target.value)}
								placeholder="766"
								inputMode="numeric"
							/>
							<span className="text-sm text-slate-500">
								Scoutnet-ID för kåren. Används både i Client ID och i mappern{" "}
								<code>group_email_{effectiveKarId || "<kårid>"}</code>.
							</span>
						</label>
					) : null}

					{preset.needsDomain ? (
						<label className="grid gap-1">
							<span className="font-medium text-slate-800">Domän (krävs)</span>
							<input
								className="rounded border border-slate-300 px-2 py-1"
								value={domain}
								onChange={(e) => setDomain(e.target.value)}
								placeholder="minkar.scout.se eller minkar.scout.se/wp"
							/>
							<span className="text-sm text-slate-500">
								Klistra in domän eller full URL. Behåll underkatalogen om
								WordPress ligger i en sådan (t.ex.{" "}
								<code>minkar.scout.se/wp</code>) — den ingår i Entity-ID och i
								ACS-URL:en.
							</span>
						</label>
					) : null}

					{preset.hasEndpoint ? (
						<label className="grid gap-1">
							<span className="font-medium text-slate-800">
								{preset.protocol === "saml"
									? "ACS-URL (Assertion Consumer Service)"
									: "Redirect-URI"}
							</span>
							<input
								className="rounded border border-slate-300 px-2 py-1 font-mono text-sm"
								value={endpointInput}
								onChange={(e) => setEndpointInput(e.target.value)}
								placeholder={preview?.endpoint}
							/>
							<span className="text-sm text-slate-500">
								{preset.id === "karwebb"
									? "Härleds från domänen om fältet lämnas tomt. Sajter som kör WordPress i en underkatalog använder /wp/wp-login.php."
									: "Härleds från domänen om fältet lämnas tomt. Kontrollera mot tjänstens metadata."}
							</span>
						</label>
					) : null}

					{preset.protocol === "openid-connect" && !preset.needsKarId ? (
						<fieldset className="rounded border border-slate-300 p-3">
							<legend className="px-1 font-medium text-slate-800">
								Organisationsdata
							</legend>
							<label className="flex items-start gap-3">
								<input
									type="checkbox"
									className="mt-1"
									checked={memberships}
									onChange={(e) => setMemberships(e.target.checked)}
								/>
								<span>
									<span className="block font-medium text-slate-900">
										Inkludera <code>scoutnet-memberships</code>
									</span>
									<span className="block text-sm text-slate-600">
										Ger klienten användarens kår och roller i kåren (t.ex.{" "}
										<code>it_manager</code>, <code>member_registrar</code>).
										Behövs för tjänster som styr behörighet utifrån roll — men
										lämna urbockat för tjänster som bara behöver veta{" "}
										<em>vem</em> användaren är.
									</span>
									<span className="mt-1 block text-sm text-amber-700">
										Ungefär fördubblar storleken på JWT:n, och är
										personuppgifter utöver ren identitet.
									</span>
								</span>
							</label>
						</fieldset>
					) : null}

					{preset.needsKarId ? (
						<div className="rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
							<p className="mb-1 font-medium">
								Två steg som inte görs härifrån:
							</p>
							<ol className="ml-4 list-decimal">
								<li>
									Attributet <code>domain</code> (t.ex. <code>minkar.se</code>)
									måste vara satt på gruppen{" "}
									<code>{effectiveKarId || "<kårid>"}</code> under{" "}
									<code>scoutnet</code> i Keycloak. Utan det får ingen någon
									e-postclaim och inloggningen fungerar inte. Kontakta
									ScoutID-teamet om det inte är gjort.
								</li>
								<li>
									Klistra in Redirect-URI:n som Google visar när SSO-profilen
									skapats — den läggs till på klientens sida efteråt.
								</li>
							</ol>
						</div>
					) : null}

					{preview ? (
						<div>
							<p className="mb-1 font-medium text-slate-800">Detta skapas:</p>
							<pre className="overflow-x-auto rounded bg-slate-100 p-3 text-xs">
								{JSON.stringify(preview.payload, null, 2)}
							</pre>
							<p className="mt-1 text-sm text-slate-500">
								Keycloak normaliserar och sorterar om vissa fält, så det sparade
								resultatet kan se något annorlunda ut.
							</p>
						</div>
					) : previewError ? (
						<p className="text-sm text-slate-500">{previewError}</p>
					) : null}

					{error ? (
						<p className="rounded border border-red-300 bg-red-50 p-3 text-red-800">
							{error}
						</p>
					) : null}

					<div>
						<button
							type="submit"
							disabled={saving}
							className="rounded bg-blue-700 px-4 py-2 font-medium text-white disabled:opacity-50"
						>
							{saving ? "Sparar…" : "Spara / Lägg till"}
						</button>
					</div>
				</div>
			) : null}
		</form>
	);
}

/**
 * Which kår the client belongs to. IT managers pick one of their own kårer;
 * admins may give any kår number, or none for clients only admins manage.
 */
function OwnerField({
	me,
	owner,
	setOwner,
}: {
	me: Me;
	owner: string;
	setOwner: (owner: string) => void;
}) {
	const myGroups = Object.entries(me.groups);

	if (me.isAdmin) {
		return (
			<label className="mb-6 grid gap-1">
				<span className="font-medium text-slate-800">Kår</span>
				<input
					className="w-40 rounded border border-slate-300 px-2 py-1"
					value={owner}
					onChange={(e) => setOwner(e.target.value)}
					placeholder="ingen"
					inputMode="numeric"
				/>
				<span className="text-sm text-slate-500">
					Kårens Scoutnet-ID. Kårens IT-ansvariga kan sedan hantera klienten.
					Lämna tomt för klienter som bara administratörer hanterar.
				</span>
			</label>
		);
	}

	if (myGroups.length === 1) {
		return (
			<p className="mb-6 text-slate-700">
				Kår: <strong>{myGroups[0][1]}</strong> ({myGroups[0][0]})
			</p>
		);
	}

	return (
		<label className="mb-6 grid gap-1">
			<span className="font-medium text-slate-800">Kår (krävs)</span>
			<select
				className="w-fit rounded border border-slate-300 px-2 py-1"
				value={owner}
				onChange={(e) => setOwner(e.target.value)}
				required
			>
				<option value="">-- Välj kår --</option>
				{myGroups.map(([id, name]) => (
					<option key={id} value={id}>
						{id} {name}
					</option>
				))}
			</select>
		</label>
	);
}
