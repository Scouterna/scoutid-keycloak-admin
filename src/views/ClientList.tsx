import { useEffect, useMemo, useState } from "react";
import { type ClientSummary, listClients, type Me } from "../api";

export function ClientList({ me }: { me: Me }) {
	const [clients, setClients] = useState<ClientSummary[] | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [idFilter, setIdFilter] = useState("");
	const [typeFilter, setTypeFilter] = useState("");
	const [ownerFilter, setOwnerFilter] = useState("");
	const [urlFilter, setUrlFilter] = useState("");
	const [showProtected, setShowProtected] = useState(false);

	useEffect(() => {
		listClients()
			.then(setClients)
			.catch((e: Error) => setError(e.message));
	}, []);

	const filtered = useMemo(() => {
		if (!clients) {
			return [];
		}
		const owner = ownerFilter.trim();
		return clients
			.filter((c) => showProtected || !c.protected)
			.filter((c) => c.clientId.toLowerCase().includes(idFilter.toLowerCase()))
			.filter((c) => !typeFilter || c.type === typeFilter)
			.filter((c) =>
				!owner ? true : owner === "-" ? !c.owner : c.owner === owner,
			)
			.filter((c) =>
				!urlFilter
					? true
					: c.endpoints.some((u) =>
							u.toLowerCase().includes(urlFilter.toLowerCase()),
						),
			);
	}, [clients, idFilter, typeFilter, ownerFilter, urlFilter, showProtected]);

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
				<h2 className="text-xl font-semibold text-slate-900">Klienter</h2>
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
						<th className="p-2">Kår</th>
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
							{me.isAdmin || Object.keys(me.groups).length > 1 ? (
								<input
									className="w-20 rounded border border-slate-300 px-2 py-1"
									placeholder={me.isAdmin ? "nr / -" : "nr"}
									title={me.isAdmin ? "- visar klienter utan kår" : undefined}
									value={ownerFilter}
									onChange={(e) => setOwnerFilter(e.target.value)}
								/>
							) : null}
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
							<td className="p-2 text-slate-500" colSpan={5}>
								Laddar…
							</td>
						</tr>
					) : filtered.length === 0 ? (
						<tr>
							<td className="p-2 text-slate-500" colSpan={5}>
								Inga klienter.
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
								<td className="p-2" title={c.owner ? me.groups[c.owner] : ""}>
									{c.owner ?? "—"}
								</td>
								<td className="p-2">{c.type}</td>
								<td className="p-2 break-all text-slate-600">
									{c.endpoints.join(", ") || "—"}
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

			{me.isAdmin ? (
				<label className="mt-4 flex items-center gap-2 text-sm text-slate-600">
					<input
						type="checkbox"
						checked={showProtected}
						onChange={(e) => setShowProtected(e.target.checked)}
					/>
					Visa Keycloaks inbyggda klienter
				</label>
			) : null}
		</div>
	);
}
