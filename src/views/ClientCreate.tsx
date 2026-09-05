import { type FormEvent, useEffect, useMemo, useState } from "react";
import { createClient, findClient, getClientSecret } from "../api";
import {
	hasSecret,
	normalizeDomain,
	type PresetId,
	presetById,
	presets,
} from "../presets";

export function ClientCreate() {
	const [presetId, setPresetId] = useState<PresetId | "">("");
	const [clientId, setClientId] = useState("");
	const [clientIdTouched, setClientIdTouched] = useState(false);
	const [name, setName] = useState("");
	const [domain, setDomain] = useState("");
	/** Derived from the domain, but editable — sites vary. */
	const [endpoint, setEndpoint] = useState("");
	const [endpointTouched, setEndpointTouched] = useState(false);
	const [memberships, setMemberships] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [saving, setSaving] = useState(false);
	const [secret, setSecret] = useState<string | null>(null);
	const [created, setCreated] = useState<string | null>(null);

	const preset = presetId ? presetById(presetId) : null;

	// Keep the endpoint in step with the domain until the admin edits it.
	useEffect(() => {
		if (!preset?.endpoint || endpointTouched) {
			return;
		}
		const clean = normalizeDomain(domain);
		setEndpoint(clean ? preset.endpoint(clean) : "");
	}, [preset, domain, endpointTouched]);

	// For SAML the entity ID is the domain (including any /wp subdirectory),
	// matching how the legacy SPs are keyed. Derive it until the admin types.
	useEffect(() => {
		if (preset?.protocol !== "saml" || clientIdTouched) {
			return;
		}
		setClientId(normalizeDomain(domain));
	}, [preset, domain, clientIdTouched]);

	// Live preview of what will be sent — the old admin hid this normalisation,
	// which made it hard to tell what had actually been created.
	const payload = useMemo(() => {
		if (!preset || !clientId) {
			return null;
		}
		return preset.build({
			clientId,
			name,
			domain: normalizeDomain(domain),
			endpoint,
			memberships,
		});
	}, [preset, clientId, name, domain, endpoint, memberships]);

	const submit = async (event: FormEvent) => {
		event.preventDefault();
		if (!preset || !payload) {
			return;
		}
		if (preset.needsDomain && !normalizeDomain(domain)) {
			setError("Domän krävs för den här klienttypen.");
			return;
		}
		setError(null);
		setSaving(true);
		try {
			await createClient(payload);
			setCreated(clientId);
			// Confidential clients get a generated secret the admin needs in
			// order to configure the other end.
			if (hasSecret(payload as never)) {
				const stored = await findClient(clientId);
				if (stored) {
					const result = await getClientSecret(stored.id);
					setSecret(result.value);
					return;
				}
			}
			window.location.hash = "#/clients";
		} catch (e) {
			setError((e as Error).message);
		} finally {
			setSaving(false);
		}
	};

	if (created && secret) {
		return (
			<div>
				<h2 className="mb-4 text-xl font-semibold text-slate-900">
					Klient {created} skapad
				</h2>
				<p className="mb-2 text-slate-700">
					Client secret — kopiera nu, den visas inte igen på den här sidan:
				</p>
				<pre className="mb-4 overflow-x-auto rounded border border-amber-300 bg-amber-50 p-3 font-mono text-sm">
					{secret}
				</pre>
				<a href="#/clients" className="text-blue-700 underline">
					← Lista klienter
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
									setEndpointTouched(false);
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
							Client ID / Entity ID (required)
						</span>
						<input
							className="rounded border border-slate-300 px-2 py-1"
							value={clientId}
							onChange={(e) => {
								setClientId(e.target.value);
								setClientIdTouched(true);
							}}
							required
						/>
					</label>

					<label className="grid gap-1">
						<span className="font-medium text-slate-800">Namn</span>
						<input
							className="rounded border border-slate-300 px-2 py-1"
							value={name}
							onChange={(e) => setName(e.target.value)}
							placeholder={clientId}
						/>
					</label>

					{preset.needsDomain ? (
						<label className="grid gap-1">
							<span className="font-medium text-slate-800">
								Domän (required)
							</span>
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

					{preset.endpoint ? (
						<label className="grid gap-1">
							<span className="font-medium text-slate-800">
								{preset.protocol === "saml"
									? "ACS-URL (Assertion Consumer Service)"
									: "Redirect-URI"}
							</span>
							<input
								className="rounded border border-slate-300 px-2 py-1 font-mono text-sm"
								value={endpoint}
								onChange={(e) => {
									setEndpoint(e.target.value);
									setEndpointTouched(true);
								}}
							/>
							<span className="text-sm text-slate-500">
								{preset.id === "karwebb"
									? "Härledd från domänen. Sajter som kör WordPress i en underkatalog använder /wp/wp-login.php — ändra vid behov."
									: "Härledd från domänen. Kontrollera mot tjänstens metadata."}
							</span>
						</label>
					) : null}

					{preset.protocol === "openid-connect" ? (
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

					{payload ? (
						<div>
							<p className="mb-1 font-medium text-slate-800">Detta skapas:</p>
							<pre className="overflow-x-auto rounded bg-slate-100 p-3 text-xs">
								{JSON.stringify(payload, null, 2)}
							</pre>
							<p className="mt-1 text-sm text-slate-500">
								Keycloak normaliserar och sorterar om vissa fält, så det sparade
								resultatet kan se något annorlunda ut.
							</p>
						</div>
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
