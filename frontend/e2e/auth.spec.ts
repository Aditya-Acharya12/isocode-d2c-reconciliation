import { expect, test, type Page } from "@playwright/test";

const FINANCE = {
  email: "finance@loomandfold.example",
  password: process.env.SEED_FINANCE_PASSWORD ?? "",
};
const FOUNDER = {
  email: "founder@loomandfold.example",
  password: process.env.SEED_FOUNDER_PASSWORD ?? "",
};
// Writes are skipped on a deployment so that test rows don't enter the demo data.
const ALLOW_WRITES = process.env.E2E_ALLOW_WRITES === "true";

test.beforeAll(() => {
  if (!FINANCE.password || !FOUNDER.password) {
    throw new Error("Set SEED_FINANCE_PASSWORD and SEED_FOUNDER_PASSWORD to run these tests.");
  }
});

async function signIn(page: Page, user: { email: string; password: string }, path = "/login") {
  await page.goto(path);
  await page.getByLabel("Email").fill(user.email);
  await page.getByLabel("Password").fill(user.password);
  const button = page.getByRole("button", { name: "Sign in" });
  await expect(button).toBeEnabled({ timeout: 150_000 }); // covers a server cold start
  await button.click();
}

test("login explains a sleeping server and enables itself once it wakes", async ({ page }) => {
  let awake = false;
  await page.route("**/api/healthz", (route) =>
    awake
      ? route.continue()
      : route.fulfill({ status: 504, contentType: "text/html", body: "<h1>Gateway timeout</h1>" }),
  );
  await page.goto("/login");
  await expect(page.getByTestId("server-waking")).toBeVisible();
  await expect(page.getByRole("button", { name: "Waiting for server…" })).toBeDisabled();

  awake = true;
  await expect(page.getByRole("button", { name: "Sign in" })).toBeEnabled({ timeout: 10_000 });
  await expect(page.getByTestId("server-waking")).toHaveCount(0);
});

test("signed-out visitors are sent to login and returned afterwards", async ({ page }) => {
  await page.goto("/settings/partners");
  await expect(page).toHaveURL(/\/login\?next=%2Fsettings%2Fpartners/);
  await signIn(page, FINANCE, page.url());
  await expect(page).toHaveURL(/\/settings\/partners$/);
});

test("a wrong password shows an error and doesn't sign in", async ({ page }) => {
  await signIn(page, { email: FINANCE.email, password: "definitely-wrong-password" });
  await expect(page.locator("form").getByRole("alert")).toHaveText(
    "Incorrect email or password.",
  );
  await expect(page).toHaveURL(/\/login/);
});

test("the session cookie can't be read by scripts", async ({ page, context }) => {
  await signIn(page, FINANCE);
  await expect(page.getByTestId("current-role")).toBeVisible();
  const cookie = (await context.cookies()).find((item) => item.name === "recon_session");
  expect(cookie?.httpOnly).toBe(true);
  expect(cookie?.sameSite).toBe("Lax");
  expect(await page.evaluate(() => document.cookie)).not.toContain("recon_session");
});

test("finance executive: read-only partners, own activity, and API refuses writes", async ({
  page,
}) => {
  await signIn(page, FINANCE);
  await expect(page.getByTestId("current-role")).toHaveText("Finance Executive");
  await expect(page.getByRole("link", { name: "My activity" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Audit log" })).toHaveCount(0);

  await page.getByRole("link", { name: "Partners" }).click();
  await expect(page.getByTestId("partner-Marketplace 1")).toBeVisible();
  await expect(page.getByRole("button", { name: "Add partner" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Edit Marketplace 1" })).toHaveCount(0);

  // Hiding the button isn't the control. The API must refuse the action itself.
  const response = await page.request.post("/api/v1/partners", {
    data: {
      name: "Should Not Exist",
      type: "courier",
      settlement_terms_days: 1,
      return_grace_days: 1,
    },
  });
  expect(response.status()).toBe(403);
});

test("co-founder: can manage partners and sees the whole audit log", async ({ page }) => {
  await signIn(page, FOUNDER);
  await expect(page.getByTestId("current-role")).toHaveText("Co-founder");
  await expect(page.getByRole("link", { name: "Audit log" })).toBeVisible();
  await expect(page.getByRole("link", { name: "My activity" })).toHaveCount(0);

  await page.getByRole("link", { name: "Partners" }).click();
  await expect(page.getByRole("button", { name: "Add partner" })).toBeVisible();

  if (ALLOW_WRITES) {
    const name = `E2E Courier ${Date.now()}`;
    await page.getByRole("button", { name: "Add partner" }).click();
    await page.getByLabel("Name").fill(name);
    await page.getByRole("combobox", { name: "Partner type" }).click();
    await page.getByRole("option", { name: "Courier" }).click();
    await page.getByLabel("Payment terms (days)").fill("abc");
    await page.getByRole("dialog").getByRole("button", { name: "Add partner" }).click();
    await expect(page.getByText("Enter a whole number of days.")).toBeVisible();

    await page.getByLabel("Payment terms (days)").fill("12");
    await page.getByRole("dialog").getByRole("button", { name: "Add partner" }).click();
    const row = page.getByTestId(`partner-${name}`);
    await expect(row).toContainText("Courier");
    await expect(row).toContainText("12 days");

    await page.getByRole("button", { name: `Deactivate ${name}` }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Deactivate" }).click();
    await expect(row).toContainText("Inactive");

    await page.getByRole("link", { name: "Audit log" }).click();
    await expect(page.getByText("Changed partner").first()).toBeVisible();
  }
});

test("signing out ends the session, including for the old cookie", async ({ page, context }) => {
  await signIn(page, FINANCE);
  await expect(page.getByTestId("current-role")).toBeVisible();
  const oldCookie = (await context.cookies()).find((item) => item.name === "recon_session");

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);

  await context.addCookies([oldCookie!]);
  const response = await page.request.get("/api/v1/auth/me");
  expect(response.status()).toBe(401);
});
