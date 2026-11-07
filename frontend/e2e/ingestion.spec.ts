import { expect, test, type Page } from "@playwright/test";

const FINANCE = {
  email: "finance@loomandfold.example",
  password: process.env.SEED_FINANCE_PASSWORD ?? "",
};
const FOUNDER = {
  email: "founder@loomandfold.example",
  password: process.env.SEED_FOUNDER_PASSWORD ?? "",
};
// These tests create real records, so they don't run against a deployment by default.
const ALLOW_WRITES = process.env.E2E_ALLOW_WRITES === "true";
const RUN = Date.now();
let counter = 0;
const unique = (prefix: string) => `${prefix}${RUN}-${++counter}`;
// A header of its own each run, so a mapping saved by an earlier run never matches.
const NOTES = `Internal Notes ${RUN}`;

test.skip(!ALLOW_WRITES, "Set E2E_ALLOW_WRITES=true to run tests that create records.");

async function signIn(page: Page, user: { email: string; password: string }) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(user.email);
  await page.getByLabel("Password").fill(user.password);
  const button = page.getByRole("button", { name: "Sign in" });
  await expect(button).toBeEnabled({ timeout: 150_000 });
  await button.click();
  await expect(page.getByTestId("current-role")).toBeVisible();
}

async function uploadCsv(
  page: Page,
  name: string,
  content: string,
  category: string,
  source: string,
) {
  await page.goto("/imports");
  await page.getByLabel("What's in this file?").selectOption({ label: category });
  await page.getByLabel(/^(Partner|Sales channel)$/).selectOption({ label: source });
  await page.getByLabel("File to upload").setInputFiles({
    name,
    mimeType: "text/csv",
    buffer: Buffer.from(content),
  });
  await expect(page.getByTestId("chosen-file")).toHaveText(name);
  await page.getByRole("button", { name: "Upload and continue" }).click();
}

async function validateAndWait(page: Page) {
  await page.getByRole("button", { name: "Validate these rows" }).click();
  await expect(page.getByTestId("stat-total")).toBeVisible({ timeout: 60_000 });
}

test.describe("finance executive", () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page, FINANCE);
  });

  test("a file with unfamiliar headers goes from upload to inspected failures", async ({ page }) => {
    const name = `${unique("orders-")}.csv`;
    const first = unique("ORD-");
    const repeated = unique("ORD-");
    const bad = unique("ORD-");
    const csv = [
      "Partner export",
      "",
      `Reference Thing,Order Date,Order Value,Payment Mode,Status,AWB No,${NOTES}`,
      `${first},01/10/2026,"₹1,499.00",COD,Delivered,AWB-${first},fine`,
      `${bad},31/02/2026,100,COD,Delivered,AWB-${bad},bad date`,
      `${repeated},02/10/2026,899.50,Prepaid,RTO,AWB-${repeated},fine`,
      `${repeated},02/10/2026,899.50,Prepaid,RTO,AWB-${repeated},repeat`,
      "TOTAL,,5000,,,,",
    ].join("\n");

    await uploadCsv(page, name, csv, "Orders", "Website");

    // Analysis finds the real header below the title rows, and the mapping form appears.
    await expect(page.getByTestId("mapping-table")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText("Headers found on row 3")).toBeVisible();
    await expect(page.getByTestId("provider-note")).toContainText("rule-based");

    // Nothing is decided for an unrecognised column, and validation is blocked.
    const order = page.getByRole("combobox", { name: "Import Reference Thing as" });
    await expect(order).toHaveValue("");
    await expect(page.getByTestId("mapping-blockers")).toContainText("Map a column to Order ID.");
    await expect(page.getByRole("button", { name: "Validate these rows" })).toBeDisabled();
    await expect(page.getByRole("combobox", { name: `Import ${NOTES} as` })).toHaveValue("");

    // A person maps it by hand. Confident suggestions are visible and already selected.
    await order.selectOption({ label: "Order ID (required)" });
    await expect(page.getByTestId("confidence-Order Date")).toContainText("97%");
    await expect(page.getByRole("combobox", { name: "Date format for Order Date" })).toHaveValue(
      "DD/MM/YYYY",
    );
    await expect(page.getByRole("combobox", { name: "Delivery status value Delivered" })).toHaveValue(
      "delivered",
    );

    await validateAndWait(page);
    await expect(page.getByTestId("stat-total")).toHaveText("5");
    await expect(page.getByTestId("stat-valid")).toHaveText("2");
    await expect(page.getByTestId("stat-invalid")).toHaveText("1");
    await expect(page.getByTestId("stat-duplicate")).toHaveText("1");
    await expect(page.getByTestId("stat-excluded")).toHaveText("1");

    // Errors, duplicates and left-out rows are each explained, with row numbers.
    const groups = page.getByTestId("issue-groups");
    await expect(groups.locator('[data-code="invalid_date"]')).toContainText("Row 5");
    await expect(groups.locator('[data-code="invalid_date"]')).toContainText("31/02/2026");
    await expect(groups.locator('[data-code="duplicate_in_file"]')).toContainText("Row 7");
    await expect(groups.locator('[data-code="summary_row"]')).toContainText("totals row");

    // The preview shows source columns, canonical values, and the problem cell.
    const preview = page.getByTestId("preview-table");
    await expect(preview).toContainText("from Reference Thing");
    await expect(page.getByTestId("preview-row-4")).toContainText("1499.00");
    await expect(page.getByTestId("preview-row-4")).toContainText("2026-10-01");
    await expect(page.getByTestId("preview-row-4")).toContainText("COD");
    await expect(page.getByTestId("preview-row-5")).toContainText("Error");
    await expect(page.getByRole("list", { name: "Progress" }).locator('[aria-current="step"]')).toHaveText(
      /Review/,
    );

    // Changing the mapping makes the result stale, and it can't be acted on.
    await page.getByRole("combobox", { name: `Import ${NOTES} as` }).selectOption({
      label: "Zone",
    });
    await expect(page.getByTestId("stale-validation")).toBeVisible();
    await expect(page.getByRole("button", { name: "Approve and import" })).toHaveCount(0);
    await page.getByRole("combobox", { name: `Import ${NOTES} as` }).selectOption({
      label: "Don't import",
    });

    await validateAndWait(page);

    // Invalid rows are never dropped silently: the person has to accept them.
    await page.getByRole("button", { name: "Approve and import" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("1 rows have errors");
    await dialog.getByRole("button", { name: "Import 2 rows" }).click();

    await expect(page.getByTestId("file-stage")).toHaveText("Imported", { timeout: 60_000 });
    await expect(page.getByTestId("result-total")).toHaveText("5");
    await expect(page.getByTestId("result-inserted")).toHaveText("2");
    await expect(page.getByTestId("result-failed")).toHaveText("1");
    await expect(page.getByTestId("result-duplicates")).toHaveText("1");

    // Failed rows can be inspected, filtered and downloaded.
    const issues = page.getByTestId("issues-table");
    await expect(issues).toContainText("31/02/2026");
    await page.getByLabel("Show").selectOption({ label: "Duplicates" });
    await expect(issues).not.toContainText("31/02/2026");
    await page.getByLabel("Show").selectOption({ label: "Errors" });
    await expect(issues).toContainText("31/02/2026");
    const download = page.waitForEvent("download");
    await page.getByTestId("download-issues").click();
    expect((await download).suggestedFilename()).toContain("row-issues.csv");

    // The history records it all.
    await page.getByRole("link", { name: "← Imports" }).click();
    const row = page.getByTestId(`file-row-${name}`);
    await expect(row).toContainText("Website");
    await expect(row).toContainText("Orders");
    await expect(row).toContainText("finance@loomandfold.example");
    await expect(row).toContainText("Imported");
    await expect(row.locator("td").nth(4)).toHaveText("5");
    await expect(row.locator("td").nth(5)).toHaveText("2");
    await expect(row.locator("td").nth(6)).toHaveText("1");
    // Every column fits at laptop width, without having to scroll sideways.
    const fits = await page
      .getByTestId("history-table")
      .evaluate((table) => table.scrollWidth <= (table.parentElement?.clientWidth ?? 0) + 1);
    expect(fits).toBe(true);

    // Uploading the same file again is refused, with a way to find the earlier one.
    await uploadCsv(page, `renamed-${name}`, csv, "Orders", "Website");
    await expect(page.getByTestId("upload-error")).toContainText("already uploaded");
    await page.getByRole("link", { name: "Open that upload" }).click();
    await expect(page).toHaveURL(/\/imports\/\d+$/);
    await expect(page.getByTestId("result-inserted")).toHaveText("2");
  });

  const categories: [string, string, (id: string) => string][] = [
    [
      "Settlements",
      "Marketplace 1",
      (id) =>
        `Settlement ID,Sub Order ID,Net Paid,Settlement Date,TCS,TDS\nSET-${id},ORD-${id},855.09,12/10/2026,5.71,1.20\n`,
    ],
    [
      "COD remittances",
      "Courier Aggregator A",
      (id) =>
        `AWB_No,COD_Collected,Net_Rem,Remittance Date\nAWB-${id},1499.00,1409.00,15/10/2026\n`,
    ],
    [
      "Freight invoices",
      "Courier Aggregator A",
      (id) =>
        `Invoice No,AWB No,Total Charge,Invoice Date,Shipment Type\nINV-${id},AWB-${id},142.00,20/10/2026,RTO\n`,
    ],
    [
      "Returns",
      "Courier Aggregator A",
      (id) => `AWB No,Return Type,Return Date,Quantity\nAWB-${id},RTO,18/10/2026,2\n`,
    ],
    [
      "Warehouse receipts",
      "Warehouse",
      (id) => `GRN No,AWB No,Received On,Qty Received\nGRN-${id},AWB-${id},19/10/2026,2\n`,
    ],
  ];

  for (const [category, source, build] of categories) {
    test(`${category} import through the browser`, async ({ page }) => {
      const name = `${unique("file-")}.csv`;
      await uploadCsv(page, name, build(unique("")), category, source);
      await expect(page.getByTestId("mapping-table")).toBeVisible({ timeout: 60_000 });
      await validateAndWait(page);
      await expect(page.getByTestId("stat-valid")).toHaveText("1");
      await page.getByRole("button", { name: "Approve and import" }).click();
      await expect(page.getByTestId("file-stage")).toHaveText("Imported", { timeout: 60_000 });
      await expect(page.getByTestId("result-inserted")).toHaveText("1");
    });
  }

  test("an unusable file explains itself and can be deleted", async ({ page }) => {
    const name = `${unique("empty-")}.csv`;
    await uploadCsv(page, name, "Order No,Order Date\n", "Orders", "Website");
    await expect(page.getByTestId("analysis-error")).toContainText("no data rows", {
      timeout: 60_000,
    });
    await page.getByRole("button", { name: "Delete upload" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
    await expect(page).toHaveURL(/\/imports$/);
    await expect(page.getByTestId(`file-row-${name}`)).toHaveCount(0);
  });

  test("the wrong file type is refused before uploading", async ({ page }) => {
    await page.goto("/imports");
    await page.getByLabel("File to upload").setInputFiles({
      name: "report.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF"),
    });
    await expect(page.getByRole("alert").filter({ hasText: "Only .csv and .xlsx" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Upload and continue" })).toBeDisabled();
  });

  test("a saved mapping is reused, versioned and restorable", async ({ page }) => {
    // A partner of its own, so that earlier runs can't affect its mapping history.
    const id = unique("");
    const partner = `Courier ${id}`;
    const created = await page.request.post("/api/v1/auth/logout");
    expect(created.status()).toBe(204);
    await signIn(page, FOUNDER);
    const added = await page.request.post("/api/v1/partners", {
      data: { name: partner, type: "courier", settlement_terms_days: 10, return_grace_days: 15 },
    });
    expect(added.status()).toBe(201);
    await page.request.post("/api/v1/auth/logout");
    await signIn(page, FINANCE);

    const header = "AWB_No,COD_Collected,Net_Rem,Remittance Date\n";
    await uploadCsv(page, `${unique("cod-")}.csv`, `${header}A-${id},10,9,01/10/2026\n`, "COD remittances", partner);
    await expect(page.getByTestId("mapping-table")).toBeVisible({ timeout: 60_000 });
    await validateAndWait(page);
    await page.getByRole("button", { name: "Approve and import" }).click();
    await expect(page.getByTestId("file-stage")).toHaveText("Imported", { timeout: 60_000 });

    // The next file from this partner starts from the saved mapping.
    await uploadCsv(page, `${unique("cod-")}.csv`, `${header}B-${id},20,18,02/10/2026\n`, "COD remittances", partner);
    await expect(page.getByTestId("provider-note")).toContainText("saved mapping", {
      timeout: 60_000,
    });
    await expect(page.getByTestId("provider-note")).toContainText("version 1");
    await page.getByRole("combobox", { name: "Import COD_Collected as" }).selectOption({
      label: "Don't import",
    });
    await validateAndWait(page);
    await page.getByRole("button", { name: "Approve and import" }).click();
    await expect(page.getByTestId("file-stage")).toHaveText("Imported", { timeout: 60_000 });

    // The change became version 2, and version 1 can be brought back as version 3.
    await page.getByRole("link", { name: "Saved mappings" }).click();
    const item = page.getByTestId(`mapping-${partner}-cod_remittance`);
    await item.getByRole("button").first().click();
    await expect(item.getByTestId("version-2")).toContainText("Active");
    await expect(item.getByTestId("version-1")).toContainText("COD_Collected");
    await expect(item.getByTestId("version-2")).not.toContainText("COD_Collected");
    await item.getByRole("button", { name: "Restore version 1" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Restore" }).click();
    await expect(item.getByTestId("version-3")).toContainText("Active");
    await expect(item.getByTestId("version-3")).toContainText("COD_Collected");

    // Audited, in the person's own activity.
    await page.getByRole("link", { name: "My activity" }).click();
    await expect(page.getByText("Restored a column mapping").first()).toBeVisible();
    await expect(page.getByText("Approved a column mapping").first()).toBeVisible();
    await expect(page.getByText("Uploaded a file").first()).toBeVisible();
  });
});

test.describe("co-founder", () => {
  test("can see history but not operate the workflow, and the API agrees", async ({ page }) => {
    // A file that's waiting for a mapping, from the finance executive.
    await signIn(page, FINANCE);
    const name = `${unique("waiting-")}.csv`;
    await uploadCsv(page, name, `AWB_No,Net_Rem,Remittance Date\nW-${RUN},9,01/10/2026\n`, "COD remittances", "Courier Aggregator A");
    await expect(page.getByTestId("mapping-table")).toBeVisible({ timeout: 60_000 });
    const fileUrl = page.url();
    await page.request.post("/api/v1/auth/logout");

    await signIn(page, FOUNDER);
    await page.getByRole("link", { name: "Imports" }).click();
    await expect(page.getByTestId("history-table")).toBeVisible();
    await expect(page.getByLabel("File to upload")).toHaveCount(0);
    await expect(page.getByTestId(`file-row-${name}`)).toContainText("Needs mapping");

    await page.goto(fileUrl);
    await expect(page.getByTestId("awaiting-finance")).toBeVisible();
    await expect(page.getByTestId("mapping-table")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Delete upload" })).toHaveCount(0);

    // Hiding the controls isn't the protection. The API refuses the actions themselves.
    const id = fileUrl.split("/").pop();
    const refused = [
      await page.request.post(`/api/v1/files/${id}/mapping-suggestions`),
      await page.request.post(`/api/v1/files/${id}/validations`, { data: { spec: { fields: {} } } }),
      await page.request.delete(`/api/v1/files/${id}`),
      await page.request.post("/api/v1/files", {
        multipart: {
          file_type: "order",
          file: { name: "a.csv", mimeType: "text/csv", buffer: Buffer.from("a,b\n1,2\n") },
        },
      }),
    ];
    for (const response of refused) expect(response.status()).toBe(403);
  });
});
