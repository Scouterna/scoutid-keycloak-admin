import { useEffect, useMemo, useState } from "react";
import { type KcClient, listClients } from "../api";
import { describeClient } from "../presets";

/**
 * Endpoints to show in the list. SAML clients keep their ACS URL in attributes
 * rather than redirectUris, so read both.
 */
function endpointsOf(client: KcClient): string[] {
	const acs = client.attributes?.saml_assertion_consumer_url_post;
	const uris = client.redirectUris ?? [];
	return acs && !uris.includes(acs) ? [acs, ...uris] : uris;
}

/**
 * Clients Keycloak creates for its own use. Hiding them keeps the list to the
 * integrations admins actually manage; the toggle still exposes them.
 */
const BUILT_IN = new Set([
	"account",
	"account-console",
	"admin-cli",
	"broker",
	"realm-management",
	"security-admin-console",
]);

export function ClientList() {
	const [clients, setClients] = useState<KcClient[] | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [idFilter, setIdFilter] = useState("");
	const [typeFilter, setTypeFilter] = useState("");
	const [urlFilter, setUrlFilter] = useState("");
	const [showBuiltIn, setShowBuiltIn] = useState(false);

	useEffect(() => {
		listClients()
			.then(setClients)
			.catch((e: Error) => setError(e.message));
	}, []);

	const filtered = useMemo(() => {
		if (!clients) {
			return [];
		}
		return clients
			.filter((c) => showBuiltIn || !BUILT_IN.has(c.clientId))
			.filter((c) => c.clientId.toLowerCase().includes(idFilter.toLowerCase()))
			.filter((c) => !typeFilter || describeClient(c) === typeFilter)
			.filter((c) =>
				!urlFilter
					? true
					: endpointsOf(c).some((u) =>
							u.toLowerCase().includes(urlFilter.toLowerCase()),
						),
			)
			.sort((a, b) => a.clientId.localeCompare(b.clientId));
	}, [clients, idFilter, typeFilter, urlFilter, showBuiltIn]);

	if (error) {
		return (
			<p className="rounded border border-red-300 bg-red-50 p-3 text-red-800">
				Kunde inte hämta klienter: {error}
			</p>
		);
	}

	return (
		<div>
			<div className="mb-4 flex items-center justify-between">
				<h2 className="text-xl font-semibold text-slate-900">OAuth-klienter</h2>
				<a
					href="#/clients/add"
					className="rounded bg-blue-700 px-3 py-2 text-sm font-medium text-white"
				>
					Lägg till klient
				</a>
			</div>

			<table className="w-full border-collapse text-sm">
				<thead>
					<tr className="border-b border-slate-300 text-left">
						<th className="p-2">Client ID</th>
						<th className="p-2">Typ</th>
						<th className="p-2">Redirect-URI:er</th>
						<th className="p-2" />
					</tr>
					<tr className="border-b border-slate-200">
						<td className="p-2">
							<input
								className="w-full rounded border border-slate-300 px-2 py-1"
								placeholder="Sök på ID"
								value={idFilter}
								onChange={(e) => setIdFilter(e.target.value)}
							/>
						</td>
						<td className="p-2">
							<select
								className="w-full rounded border border-slate-300 px-2 py-1"
								value={typeFilter}
								onChange={(e) => setTypeFilter(e.target.value)}
							>
								<option value="">-- Alla --</option>
								<option value="saml">SAML</option>
								<option value="confidential">Confidential</option>
								<option value="public">Public</option>
								<option value="service">Service</option>
							</select>
						</td>
						<td className="p-2" colSpan={2}>
							<input
								className="w-full rounded border border-slate-300 px-2 py-1"
								placeholder="Sök på domän / URI"
								value={urlFilter}
								onChange={(e) => setUrlFilter(e.target.value)}
							/>
						</td>
					</tr>
				</thead>
				<tbody>
					{clients === null ? (
						<tr>
							<td className="p-2 text-slate-500" colSpan={4}>
								Laddar…
							</td>
						</tr>
					) : (
						filtered.map((c) => (
							<tr
								key={c.id}
								className="border-b border-slate-100 odd:bg-slate-50"
							>
								<td className="p-2 font-medium">
									{c.clientId}
									{!c.enabled ? (
										<span className="ml-2 text-xs text-slate-500">
											(inaktiv)
										</span>
									) : null}
								</td>
								<td className="p-2">{describeClient(c)}</td>
								<td className="p-2 break-all text-slate-600">
									{endpointsOf(c).join(", ") || "—"}
								</td>
								<td className="p-2 text-right">
									<a
										href={`#/clients/${c.id}`}
										className="text-blue-700 underline"
									>
										Visa
									</a>
								</td>
							</tr>
						))
					)}
				</tbody>
			</table>

			<label className="mt-4 flex items-center gap-2 text-sm text-slate-600">
				<input
					type="checkbox"
					checked={showBuiltIn}
					onChange={(e) => setShowBuiltIn(e.target.checked)}
				/>
				Visa Keycloaks inbyggda klienter
			</label>
		</div>
	);
}
