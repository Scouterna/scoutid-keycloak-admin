import type { User } from "oidc-client-ts";
import { useEffect, useState } from "react";
import { initAuth, login, logout } from "./auth";
import { config, isConfigured } from "./config";
import { ClientCreate } from "./views/ClientCreate";
import { ClientList } from "./views/ClientList";
import { ClientView } from "./views/ClientView";

/**
 * Hash routing, keeping the URL shapes of the old ScoutID admin (#/services,
 * #/service/<id>) so existing bookmarks and habits carry over.
 */
function useHashRoute(): string {
	const [hash, setHash] = useState(window.location.hash || "#/clients");
	useEffect(() => {
		const onChange = () => setHash(window.location.hash || "#/clients");
		window.addEventListener("hashchange", onChange);
		return () => window.removeEventListener("hashchange", onChange);
	}, []);
	return hash;
}

export function App() {
	const [user, setUser] = useState<User | null>(null);
	const [loading, setLoading] = useState(true);
	const [authError, setAuthError] = useState<string | null>(null);
	const hash = useHashRoute();

	useEffect(() => {
		// Nothing to resolve against when no realm is configured; the setup
		// message below is rendered instead.
		if (!isConfigured) {
			setLoading(false);
			return;
		}
		initAuth()
			.then(setUser)
			.catch((error: Error) => setAuthError(error.message))
			.finally(() => setLoading(false));
	}, []);

	if (!isConfigured) {
		return (
			<div className="mx-auto max-w-2xl p-6">
				<h1 className="mb-4 text-2xl font-bold text-slate-900">
					ScoutID Admin
				</h1>
				<p className="mb-3 rounded border border-amber-300 bg-amber-50 p-3 text-amber-900">
					Ingen Keycloak-server är konfigurerad.
				</p>
				<p className="mb-2 text-slate-700">
					Sätt <code>VITE_KC_URL</code> och <code>VITE_KC_REALM</code> i en{" "}
					<code>.env.local</code> för lokal utveckling, eller{" "}
					<code>KC_URL</code> och <code>KC_REALM</code> som miljövariabler i
					containern. Se README.
				</p>
			</div>
		);
	}

	if (loading) {
		return <p className="p-8 text-slate-600">Laddar…</p>;
	}

	return (
		<div className="mx-auto max-w-5xl p-6">
			<header className="mb-8 flex items-baseline justify-between gap-4 border-b border-slate-200 pb-4">
				<div>
					<h1 className="text-2xl font-bold text-slate-900">
						<a href="#/clients">ScoutID Admin</a>
					</h1>
					<p className="text-sm text-slate-500">
						{config.realm} @ {new URL(config.authority).host}
					</p>
				</div>
				{user ? (
					<div className="text-right text-sm">
						<p className="text-slate-700">
							Inloggad som{" "}
							{user.profile.name ?? user.profile.preferred_username}
						</p>
						<button
							type="button"
							className="text-blue-700 underline"
							onClick={() => logout()}
						>
							Logga ut
						</button>
					</div>
				) : null}
			</header>

			{authError ? (
				<p className="mb-4 rounded border border-red-300 bg-red-50 p-3 text-red-800">
					Inloggning misslyckades: {authError}
				</p>
			) : null}

			{!user ? (
				<div>
					<p className="mb-4 text-slate-700">
						Logga in för att administrera OAuth-klienter.
					</p>
					<button
						type="button"
						className="rounded bg-blue-700 px-4 py-2 font-medium text-white"
						onClick={() => login()}
					>
						Logga in
					</button>
				</div>
			) : (
				<Router hash={hash} />
			)}
		</div>
	);
}

function Router({ hash }: { hash: string }) {
	if (hash === "#/clients/add") {
		return <ClientCreate />;
	}
	const match = hash.match(/^#\/clients\/([^/]+)$/);
	if (match) {
		return <ClientView id={match[1]} />;
	}
	return <ClientList />;
}
