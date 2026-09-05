import "@fontsource-variable/source-sans-3";
import "@scouterna/ui-webc/style.css";
import "./style.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";

createRoot(document.getElementById("root") as HTMLElement).render(
	<StrictMode>
		<App />
	</StrictMode>,
);
