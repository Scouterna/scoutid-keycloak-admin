import { useEffect, useState } from "react";
import { ApiError, getMe, login, logout, type Me } from "./api";
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

/** What the signed-in user may do, in a few words for the header. */
function describeRole(me: Me): string {
	if (me.isAdmin) {
		return "Administratör";
	}
	const groups = Object.entries(me.groups).map(([id, name]) => `${id} ${name}`);
	return groups.length ? `IT-ansvarig: ${groups.join(", ")}` : "";
}

export function App() {
	const [me, setMe] = useState<Me | null>(null);
	const [signedOut, setSignedOut] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const hash = useHashRoute();

	useEffect(() => {
		getMe()
			.then(setMe)
			.catch((e: Error) => {
				if (e instanceof ApiError && e.status === 401) {
					setSignedOut(true);
				} else {
					setError(e.message);
				}
			});
	}, []);

	if (error) {
		return (
			<div className="mx-auto max-w-5xl p-6">
				<p className="rounded border border-red-300 bg-red-50 p-3 text-red-800">
					{error}
				</p>
			</div>
		);
	}

	if (!me && !signedOut) {
		return <p className="p-8 text-slate-600">Laddar…</p>;
	}

	return (
		<div className="mx-auto max-w-5xl p-6">
			<header className="mb-8 flex items-baseline justify-between gap-4 border-b border-slate-200 pb-4">
				<h1 className="text-2xl font-bold text-slate-900">
					<a href="#/clients">ScoutID Admin</a>
				</h1>
				{me ? (
					<div className="text-right text-sm">
						<p className="text-slate-700">Inloggad som {me.name}</p>
						<p className="text-slate-500">{describeRole(me)}</p>
						<button
							type="button"
							className="text-blue-700 underline"
							onClick={logout}
						>
							Logga ut
						</button>
					</div>
				) : null}
			</header>

			{!me ? (
				<div>
					<p className="mb-4 text-slate-700">
						Logga in med ScoutID för att administrera klienter.
					</p>
					<button
						type="button"
						className="rounded bg-blue-700 px-4 py-2 font-medium text-white"
						onClick={login}
					>
						Logga in
					</button>
				</div>
			) : !me.hasAccess ? (
				<NoAccess me={me} />
			) : (
				<Router hash={hash} me={me} />
			)}
		</div>
	);
}

function NoAccess({ me }: { me: Me }) {
	return (
		<div className="grid gap-3 text-slate-700">
			<p className="rounded border border-amber-300 bg-amber-50 p-3 text-amber-900">
				Du saknar behörighet att administrera klienter.
			</p>
			<p>
				Behörighet kommer från Scoutnet: rollen <em>IT-ansvarig</em> i en kår
				ger rätt att hantera kårens klienter. Ändras rollen i Scoutnet gäller
				det från nästa inloggning.
			</p>
			{me.membershipsError ? (
				<p className="rounded border border-red-300 bg-red-50 p-3 text-red-800">
					Dina uppgifter från Scoutnet blev ofullständiga (
					<code>{me.membershipsError}</code>), vilket kan vara orsaken. Kontakta
					ScoutID-teamet.
				</p>
			) : null}
		</div>
	);
}

function Router({ hash, me }: { hash: string; me: Me }) {
	if (hash === "#/clients/add") {
		return <ClientCreate me={me} />;
	}
	const match = hash.match(/^#\/clients\/([^/]+)$/);
	if (match) {
		return <ClientView id={match[1]} me={me} />;
	}
	return <ClientList me={me} />;
}
