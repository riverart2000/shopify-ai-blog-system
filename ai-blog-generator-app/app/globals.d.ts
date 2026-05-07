import type * as React from "react";

declare module "*.css";

declare global {
	namespace JSX {
		interface IntrinsicElements {
			"s-app-nav": React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement>;
		}
	}
}
