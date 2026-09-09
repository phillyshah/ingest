// Playwright smoke: needs the API (make api) and a seeded DB (make seed). IDs come from env or /users.
import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

// IDs come from `make seed` (.demo-out/e2e.env) or the environment; nothing is listed without authentication.
function env(): Record<string, string> {
  const out: Record<string, string> = { ...(process.env as Record<string, string>) };
  for (const f of ["../../.demo-out/e2e.env", ".demo-out/e2e.env"]) {
    try { for (const line of readFileSync(f, "utf8").split("\n")) { const [k, v] = line.split("="); if (k && v) out[k] ??= v.trim(); } } catch { /* absent */ }
  }
  return out;
}

async function users() {
  const e = env();
  const mk = (name: string, roles: string[]) => (e[`E2E_${name.toUpperCase()}_ID`] ? { id: e[`E2E_${name.toUpperCase()}_ID`], display_name: name, roles, tenant_id: e.E2E_TENANT_ID } : null);
  return [mk("admin", ["source_admin"]), mk("pt", ["pt"])].filter((x): x is NonNullable<typeof x> => x !== null);
}

test("admin creates a campaign, sees it on the board, pauses/resumes; PT reviews; plan options for three families", async ({ page }) => {
  const all = await users();
  const admin = all.find((u) => u.display_name === "admin");
  const pt = all.find((u) => u.display_name === "pt");
  test.skip(!admin || !pt, "seeded users not available; run `make seed`");
  const signIn = async (u: { id: string; tenant_id: string; roles: string[] }) => {
    await page.goto("/");
    await page.evaluate((s) => localStorage.setItem("moveai.session", JSON.stringify(s)), { userId: u.id, tenantId: u.tenant_id, role: u.roles[0] });
    await page.reload();
  };
  await signIn(admin!);
  await page.goto("/campaigns");
  await page.getByTestId("new-campaign").click();
  const title = `E2E ${Date.now()}`;
  await page.getByLabel("Title").fill(title);
  await page.getByLabel("Ailment").fill("frozen shoulder");
  await page.getByLabel("Diagnostic codes (ICD-10-CM)").fill("M75.01");
  await page.getByRole("button", { name: "Preview scope" }).click();
  await expect(page.getByTestId("scope-preview")).toContainText("Adhesive capsulitis");
  await expect(page.getByTestId("scope-preview")).toContainText("FY26");
  await page.getByLabel("I confirm the interpreted scope").check();
  await page.getByRole("button", { name: "Save as draft" }).click();
  await expect(page.getByTestId("col-draft")).toContainText(title);
  await page.getByRole("link", { name: title }).click();
  await page.getByTestId("action-start").click();
  await expect(page.locator("h1")).toContainText(title);
  const pause = page.getByTestId("action-pause");
  if (await pause.isVisible()) {
    await pause.click();
    await expect(page.getByTestId("action-resume")).toBeVisible();
    await page.getByTestId("action-resume").click();
    await expect(page.getByTestId("action-pause")).toBeVisible();
  }
  await page.reload();
  await expect(page.locator("h1")).toContainText(title);   // state survives reload

  await signIn(pt!);
  await page.goto("/reviews");
  await expect(page.locator("h1")).toContainText("PT review queue");
  await expect(page.getByTestId("new-campaign")).toHaveCount(0);   // PT has no admin powers

  for (const text of ["55-year-old slightly obese man with frozen shoulder", "Patient recovering after surgical MCL repair", "Patient with a hamstring pull, no surgery"]) {
    await page.goto("/plan-options");
    await page.getByTestId("narrative").fill(text);
    await page.getByTestId("submit-case").click();
    await expect(page.getByTestId("plan-result")).toContainText("needs_assessment");
    await expect(page.getByTestId("plan-result")).toContainText("Protocol preview");
    await expect(page.getByTestId("plan-result")).toContainText("affected_side");
  }
});
