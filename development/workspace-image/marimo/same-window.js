// Workspace image: marimo opens its own pages in the same window. Its "Create a new notebook" card, its
// notebook links and its "open file" actions open a new browser window or tab (target="_blank",
// window.open(url, "_blank")); here they navigate this window instead, so marimo embedded in the dashboard's
// app panel stays in the panel. Ctrl/Cmd/Shift-click and middle-click still open a new tab, and links to
// other sites are left alone. Injected into marimo's index.html by the Dockerfile.
(() => {
	const sameSite = (url) => {
		try {
			return new URL(url, location.href).origin === location.origin;
		} catch {
			return false;
		}
	};
	const open = window.open.bind(window);
	window.open = (url, target, features) => {
		if (url && sameSite(String(url)) && target !== "_self") {
			location.assign(new URL(String(url), location.href).href);
			return window;
		}
		return open(url, target, features);
	};
	document.addEventListener(
		"click",
		(event) => {
			if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
				return;
			}
			const link = event.target instanceof Element ? event.target.closest("a[target]") : null;
			if (link && link.target !== "_self" && link.href && sameSite(link.href) && !link.hasAttribute("download")) {
				event.preventDefault();
				location.assign(link.href);
			}
		},
		true,
	);
})();
