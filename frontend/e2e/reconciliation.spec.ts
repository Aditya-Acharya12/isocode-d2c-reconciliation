import { expect, test, type Page } from "@playwright/test";

const FINANCE = {
  email: "finance@loomandfold.example",
  password: process.env.SEED_FINANCE_PASSWORD ?? "",
};
const FOUNDER = {
  email: "founder@loomandfold.example",
  password: process.env.SEED_FOUNDER_PASSWORD ?? "",
};
// Imports and a reconciliation run change shared data, so this never runs against a
// deployment unless asked to.
const ALLOW_WRITES = process.env.E2E_ALLOW_WRITES === "true";
const RUN = Date.now();

test.skip(!ALLOW_WRITES, "Set E2E_ALLOW_WRITES=true to run tests that create records.");

// The assignment's three samples, under IDs of their own so earlier runs can't interfere.
const S1 = `ORD-W-E${RUN}-1`; // Website COD, delivered, never remitted
const S2 = `ORD-M-E${RUN}-2`; // Marketplace 1 prepaid, short-paid by ₹37
const S3 = `ORD-W-E${RUN}-3`; // Website prepaid, returned, RTO freight overcharged

const FILES: [string, string, string, string][] = [
  [
    "Orders",
    "Website",
    `web-${RUN}.csv`,
    "Order No,Order Date,Order Value,Payment Mode,Status,AWB No,Courier,Delivered On,Returned On,Wt_Slab,Qty\n" +
      `${S1},01/10/2026,1499.00,COD,Delivered,AWB-${S1},Courier Aggregator A,05/10/2026,,0.5kg,1\n` +
      `${S3},02/10/2026,2398.00,Prepaid,RTO,AWB-${S3},Courier Aggregator A,,10/10/2026,0.5kg,2\n`,
  ],
  [
    "Orders",
    "Marketplace 1",
    `mkt-${RUN}.csv`,
    "Order No,Order Date,Order Value,Payment Mode,Status,AWB No,Delivered On,Wt_Slab\n" +
      `${S2},03/10/2026,1199.00,Prepaid,Delivered,AWB-${S2},06/10/2026,1kg\n`,
  ],
  [
    "Settlements",
    "Marketplace 1",
    `settlement-${RUN}.csv`,
    "Settlement ID,Sub Order ID,Net Paid,Settlement Date,TCS,TDS\n" +
      `SET-${RUN},${S2},855.09,20/10/2026,5.71,1.20\n`,
  ],
  [
    "Freight invoices",
    "Courier Aggregator A",
    `freight-${RUN}.csv`,
    "Invoice No,AWB No,Total Charge,Invoice Date,Shipment Type,Wt_Slab\n" +
      `INV-${RUN},AWB-${S3},142.00,15/10/2026,RTO,0.5kg\n`,
  ],
  [
    "Returns",
    "Courier Aggregator A",
    `returns-${RUN}.csv`,
    `AWB No,Return Type,Return Date,Quantity\nAWB-${S3},RTO,10/10/2026,2\n`,
  ],
  [
    "Warehouse receipts",
    "Warehouse",
    `grn-${RUN}.csv`,
    `GRN No,AWB No,Received On,Qty Received,Condition\nGRN-${RUN},AWB-${S3},12/10/2026,2,Good\n`,
  ],
];

async function signIn(page: Page, user: { email: string; password: string }) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(user.email);
  await page.getByLabel("Password").fill(user.password);
  const button = page.getByRole("button", { name: "Sign in" });
  await expect(button).toBeEnabled({ timeout: 150_000 });
  await button.click();
  await expect(page.getByTestId("current-role")).toBeVisible();
}

async function importFile(page: Page, [category, source, name, content]: (typeof FILES)[number]) {
  await page.goto("/imports");
  await page.getByLabel("What's in this file?").selectOption({ label: category });
  await page.getByLabel(/^(Partner|Sales channel)$/).selectOption({ label: source });
  await page.getByLabel("File to upload").setInputFiles({
    name,
    mimeType: "text/csv",
    buffer: Buffer.from(content),
  });
  await page.getByRole("button", { name: "Upload and continue" }).click();
  await expect(page.getByTestId("mapping-table")).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "Validate these rows" }).click();
  await expect(page.getByTestId("stat-invalid")).toHaveText("0", { timeout: 60_000 });
  await page.getByRole("button", { name: "Approve and import" }).click();
  await expect(page.getByTestId("file-stage")).toHaveText("Imported", { timeout: 60_000 });
}

async function find(page: Page, orderId: string) {
  await page.getByLabel("Search").fill(orderId);
  return page.getByTestId(`recon-row-${orderId}`);
}

test("finance reconciles the three samples, follows the evidence and files a claim", async ({
  page,
}) => {
  await signIn(page, FINANCE);
  for (const file of FILES) await importFile(page, file);

  await page.getByRole("link", { name: "Reconciliation" }).click();
  await page.getByRole("button", { name: "Run reconciliation" }).click();
  await expect(page.getByTestId("last-run")).toContainText("Last reconciled", { timeout: 120_000 });

  // The three acceptance cases, as the table shows them.
  const row1 = await find(page, S1);
  await expect(row1).toBeVisible({ timeout: 60_000 });
  await expect(row1.getByTestId("recon-status")).toHaveText("Unpaid past due");
  await expect(row1).toContainText("₹1,409.00");
  await expect(row1.getByTestId("difference")).toHaveText("-₹1,409.00");

  const row2 = await find(page, S2);
  await expect(row2.getByTestId("recon-status")).toHaveText("Short-paid");
  await expect(row2).toContainText("₹899.00");
  await expect(row2).toContainText("₹862.00");
  await expect(row2.getByTestId("difference")).toHaveText("-₹37.00");

  const row3 = await find(page, S3);
  await expect(row3.getByTestId("recon-status")).toHaveText("Freight overcharged");
  await expect(row3).toContainText("-₹96.00");
  await expect(row3).toContainText("-₹142.00");
  await expect(row3.getByTestId("difference")).toHaveText("-₹46.00");

  // The detail page explains the figures and links every record back to its file.
  await row3.getByRole("link", { name: S3 }).click();
  await expect(page.getByTestId("order-reason")).toContainText(/freight/i);
  const freight = page.getByTestId("freight-calculation");
  await expect(freight).toContainText(`INV-${RUN}`);
  await expect(freight).toContainText("₹96.00");
  await expect(freight).toContainText("₹142.00");
  const sources = page.getByTestId("source-records");
  await expect(sources).toContainText(`freight-${RUN}.csv`);
  await expect(sources).toContainText(`grn-${RUN}.csv`);
  await page.getByRole("link", { name: `grn-${RUN}.csv, row 2` }).click();
  await expect(page).toHaveURL(/\/imports\/\d+$/);

  // Sample 2's tax was withheld, and counts as received rather than as leakage.
  await page.goto("/reconciliation");
  await (await find(page, S2)).getByRole("link", { name: S2 }).click();
  const receivable = page.getByTestId("receivable-calculation");
  await expect(receivable).toContainText("₹5.71");
  await expect(receivable).toContainText("₹1.20");
  await expect(receivable).toContainText("₹862.00");

  // The return was scanned in, so it isn't at risk.
  await page.getByRole("link", { name: "Returns" }).click();
  await page.getByLabel("Search returns").fill(S3);
  const ret = page.getByTestId(`return-row-${S3}`);
  await expect(ret.getByTestId("scan")).toContainText(`GRN-${RUN}`);
  await expect(ret.getByTestId("recon-status")).toHaveText("Settled");

  // A claim against the courier for the unpaid COD order.
  await page.getByRole("link", { name: "Claims" }).click();
  await page.getByRole("checkbox", { name: `Select ${S1} Unpaid past due` }).check();
  await page
    .getByTestId("eligible-Courier Aggregator A")
    .getByRole("button", { name: "Create 1 claim" })
    .click();
  await page.getByRole("dialog").getByRole("button", { name: "Create claims" }).click();
  const claimRow = page.getByTestId(`claim-row-${S1}`);
  await expect(claimRow.getByTestId("claim-status")).toHaveText("Draft");
  await expect(claimRow).toContainText("₹1,409.00");
  await expect(page.getByRole("checkbox", { name: `Select ${S1} Unpaid past due` })).toHaveCount(0);

  await claimRow.getByRole("link").first().click();
  // A dev server compiles a route on its first visit, which can take a while.
  await expect(page).toHaveURL(/\/claims\/\d+$/, { timeout: 60_000 });
  await expect(page.getByTestId("claim-calculation")).toContainText("1,409", { timeout: 30_000 });
  await expect(page.getByTestId("evidence-table")).toContainText(`web-${RUN}.csv`);
  await page.getByLabel("Move to").selectOption({ label: "Submitted" });
  await page.getByRole("button", { name: "Update claim" }).click();
  await expect(page.locator("h1").getByTestId("claim-status")).toHaveText("Submitted");
  await expect(page.getByTestId("claim-history")).toContainText("Draft → Submitted");

  const href = await page.getByTestId("claim-evidence").getAttribute("href");
  const csv = await page.request.get(href ?? "");
  expect(csv.status()).toBe(200);
  expect(await csv.text()).toContain(S1);

  await page.getByRole("link", { name: "My activity" }).click();
  await expect(page.getByText("Updated a claim").first()).toBeVisible();
  await expect(page.getByText("Reconciliation finished").first()).toBeVisible();
});

test("the co-founder can review reconciliation and claims but not change them", async ({
  page,
}) => {
  await signIn(page, FOUNDER);
  await page.getByRole("link", { name: "Reconciliation" }).click();
  await expect(page.getByTestId("metric-risk")).toBeVisible();
  await expect(page.getByRole("button", { name: "Run reconciliation" })).toHaveCount(0);
  await expect(page.getByTestId("tax-note")).toContainText("not tax advice");

  await page.getByRole("link", { name: "Claims" }).click();
  await expect(page.getByRole("checkbox")).toHaveCount(0);

  await page.getByRole("link", { name: "Rate cards" }).click();
  await expect(page.getByTestId("settlement-rules")).toContainText("Marketplace 1");

  const refused = [
    await page.request.post("/api/v1/reconciliation/runs", { data: { as_of_date: "2026-11-01" } }),
    await page.request.post("/api/v1/claims", { data: { discrepancy_ids: [1] } }),
  ];
  for (const response of refused) expect(response.status()).toBe(403);
});
