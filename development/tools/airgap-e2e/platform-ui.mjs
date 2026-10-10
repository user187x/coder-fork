// Browser checks of the dashboard features (run after step8.py, which leaves the running workspace admin/e2e):
//   node platform-ui.mjs OUT_DIR URL [HOST_RESOLVER_RULES]
// e.g. node platform-ui.mjs shots https://coder.airgap.test "MAP coder.airgap.test:443 127.0.0.1:18443"
// Needs @playwright/test (the fork's site/node_modules) and its headless Chromium.
import { createRequire } from "node:module";
const require = createRequire(`${process.env.CODER_SITE ?? `${process.env.HOME}/workspace/coder/site`}/package.json`);
const { chromium } = require("@playwright/test");

const [OUT, URL, RESOLVE] = process.argv.slice(2);
const results = [];
const check = (name, ok, detail = "") => {
	results.push(ok);
	console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? ` - ${detail}` : ""}`);
};
const browser = await chromium.launch(RESOLVE ? { args: [`--host-resolver-rules=${RESOLVE}`] } : {});
const page = await browser.newPage({ ignoreHTTPSErrors: true, viewport: { width: 1440, height: 1000 } });
const snap = (name) => page.screenshot({ path: `${OUT}/${name}.png` });
try {
	await page.goto(`${URL}/login`);
	await page.getByLabel("Email").fill("admin@admin.com");
	await page.getByLabel("Password").fill("admin");
	await page.getByRole("button", { name: /^sign in$/i }).click();
	await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 30000 });

	// ---- terminal accordion
	await page.goto(`${URL}/@admin/e2e`);
	await page.getByRole("button", { name: "Terminal", exact: true }).click();
	const group = page.getByRole("group", { name: "Terminal text size" }).first();
	await group.waitFor({ timeout: 30000 });
	check("text size controls at the far left of the row", await group.isVisible());
	const dims = page.getByTitle(/Columns x rows/).first();
	await dims.waitFor({ timeout: 30000 });
	const [cols, rows] = (await dims.textContent()).split("x").map(Number);
	check("starts at a size that fits btop (>= 80x24)", cols >= 80 && rows >= 24, `${cols}x${rows}`);
	const size = async () => Number(await group.getByTitle("Text size (px)").textContent());
	const before = await size();
	await group.getByRole("button", { name: "Larger text" }).click();
	check("Larger text increases the size", (await size()) === before + 1, `${before} -> ${await size()}`);
	await group.getByRole("button", { name: "Smaller text" }).click();
	// The terminal draws on a canvas (WebGL); what counts is that the browser loaded the bundled Nerd Font for it.
	await page.waitForFunction(() => [...document.fonts].some((f) => f.family.includes("JetBrainsMono Nerd Font") && f.status === "loaded"), null, { timeout: 30000 }).catch(() => {});
	const loaded = await page.evaluate(() => [...document.fonts].filter((f) => f.family.includes("JetBrainsMono Nerd Font")).map((f) => f.status));
	check("web terminal font is JetBrainsMono Nerd Font (loaded by the terminal)", loaded.includes("loaded"), JSON.stringify(loaded));
	await snap("01-terminal");

	await page.getByRole("button", { name: "Pop out" }).click();
	const popout = page.getByRole("dialog", { name: /Terminal/ });
	await popout.waitFor();
	check("Pop out opens the terminal in a window", await popout.locator(".xterm").first().isVisible());
	await snap("02-popout");
	await popout.getByRole("button", { name: "Put the terminal back in the drop-down" }).click();
	await page.getByRole("button", { name: "Pop out" }).waitFor();
	check("Dock puts it back in the drop-down", (await page.getByRole("dialog", { name: /Terminal/ }).count()) === 0);
	await page.getByRole("button", { name: "Pop out" }).click();
	await page.getByRole("dialog", { name: /Terminal/ }).getByRole("button", { name: "Close the terminal window" }).click();
	await page.waitForTimeout(500);
	check("Close leaves the drop-down collapsed (no terminal running)",
		(await page.locator(".xterm").count()) === 0 && (await page.getByRole("button", { name: "Pop out" }).count()) === 0);

	// ---- HUD
	await page.goto(`${URL}/hud`);
	const tiles = page.getByRole("list", { name: "Metrics" }).getByRole("button");
	await tiles.first().waitFor({ timeout: 30000 });
	check("HUD shows its metrics", (await tiles.count()) >= 15, `${await tiles.count()} tiles`);
	await snap("03-hud");
	const target = tiles.filter({ hasText: "Highest ping" });
	await target.click();
	await page.waitForTimeout(300);
	check("clicking a metric moves it to the top", /Highest ping/.test(await tiles.first().textContent()));
	await page.reload();
	await tiles.first().waitFor();
	check("the order is remembered", /Highest ping/.test(await tiles.first().textContent()));

	// ---- Monitoring and User Quota
	await page.goto(`${URL}/deployment/monitoring`);
	await page.getByRole("heading", { name: "Security posture" }).waitFor({ timeout: 30000 });
	await page.getByText("Privileged accounts").waitFor({ timeout: 30000 });
	check("Monitoring shows the security posture", true);
	await snap("04-monitoring");
	await page.goto(`${URL}/health/user-quota`);
	await page.getByRole("heading", { name: /Global Workspace Scheduler/ }).waitFor({ timeout: 30000 });
	check("User Quota has the Global Workspace Scheduler", await page.getByText("Maximum active time").isVisible());
	await snap("05-scheduler");
} catch (err) {
	check("no unexpected error", false, String(err).split("\n")[0]);
	await snap("error");
} finally {
	await browser.close();
	console.log(`${results.filter(Boolean).length}/${results.length} checks passed`);
}
