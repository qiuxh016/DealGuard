import { expect, test, type Page } from "@playwright/test";

const API = `http://127.0.0.1:${process.env.PLAYWRIGHT_BACKEND_PORT ?? "8001"}`;

async function registerAdmin(page: Page, suffix: string) {
  const email = `owner-${suffix}@dealguard.test`;
  await page.goto("/");
  await page.getByRole("button", { name: "创建工作区" }).click();
  await page.getByLabel("公司名称").fill(`E2E Trading ${suffix}`);
  await page.getByLabel("管理员姓名").fill("Workspace Owner");
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("密码").fill("secure-e2e-password-123");
  const registration = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/auth/register") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "创建工作区", exact: true }).last().click();
  const response = await registration;
  expect(response.status(), `Registration failed: ${response.status()} ${await response.text()}`).toBe(201);
  await expect(page.getByRole("heading", { name: "询盘与报价工作台" })).toBeVisible();
  return email;
}

async function apiPost<T>(page: Page, path: string, data: unknown): Promise<T> {
  const response = await page.request.post(`${API}${path}`, { data });
  expect(response.ok(), `${path}: ${response.status()} ${await response.text()}`).toBeTruthy();
  return response.json() as Promise<T>;
}

async function createPolicy(page: Page, policyId: string, version: string, limit: number, source: string) {
  const uploaded = await page.request.post(`${API}/api/v1/policy-drafts`, {
    multipart: {
      policy_id: policyId,
      version,
      file: { name: `${version}.md`, mimeType: "text/markdown", buffer: Buffer.from(source) },
    },
  });
  expect(uploaded.ok(), await uploaded.text()).toBeTruthy();
  const draft = await uploaded.json() as { id: string };
  const updated = await page.request.put(`${API}/api/v1/policy-drafts/${draft.id}`, {
    data: {
      title: `${policyId} ${version}`,
      action_type: "quote_discount",
      policy_scope: "general",
      customer_tier: "VIP",
      max_discount_pct: limit,
      review_max_discount_pct: null,
      exception_approval_allowed: false,
    },
  });
  expect(updated.ok(), await updated.text()).toBeTruthy();
  const activated = await page.request.post(`${API}/api/v1/policy-drafts/${draft.id}/activate`);
  expect(activated.ok(), await activated.text()).toBeTruthy();
}

test("registered workspace members can quote within current policy and cannot read another customer's quote", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  const suffix = Date.now().toString().slice(-8);
  await registerAdmin(page, suffix);

  const customerA = await apiPost<{ id: string }>(page, "/api/v1/customers", { name: `Buyer A ${suffix}`, tier: "VIP" });
  const customerB = await apiPost<{ id: string }>(page, "/api/v1/customers", { name: `Buyer B ${suffix}`, tier: "VIP" });
  const productA = `P-E2EA${suffix}`;
  const productB = `P-E2EB${suffix}`;
  for (const [id, name, price] of [[productA, "Product A", "100.00"], [productB, "Product B", "250.00"]]) {
    await apiPost(page, "/api/v1/products", { id, name, currency: "USD", unit_price: price, stock: 2000 });
  }
  await createPolicy(page, `E2E-${suffix}`, "v1", 10, "VIP discount quotations may use a discount up to 10%.");
  await createPolicy(page, `E2E-${suffix}`, "v2", 5, "VIP discount quotations may use a discount up to 5%.");

  const invitationA = await apiPost<{ token: string }>(page, "/api/v1/auth/invitations", {
    email: `sales-a-${suffix}@dealguard.test`, role: "sales", customer_id: customerA.id,
  });
  const invitationB = await apiPost<{ token: string }>(page, "/api/v1/auth/invitations", {
    email: `sales-b-${suffix}@dealguard.test`, role: "sales", customer_id: customerB.id,
  });
  await apiPost(page, "/api/v1/auth/accept-invitation", {
    token: invitationA.token, display_name: "Sales A", password: "secure-sales-password-123",
  });
  await page.reload();
  await expect(page.getByRole("heading", { name: "询盘与报价工作台" })).toBeVisible();

  const allowResponse = page.waitForResponse((response) => response.url().includes("/api/v1/runs") && response.request().method() === "POST");
  await page.getByLabel("询盘内容").fill(`Please quote ${productA} 10 units at 5% discount; ${productB} 4 units at 3% discount.`);
  await page.getByRole("button", { name: "运行询盘" }).click();
  const allowed = await (await allowResponse).json();
  await expect(page.locator(".dg-decision-name")).toHaveAttribute("data-decision", "ALLOW");
  await expect(page.locator(".dg-quote-total")).toHaveText("$1,920.00");
  const quoteId = allowed.quote.id as string;
  await page.getByRole("navigation", { name: "工作区导航" }).getByRole("link", { name: "报价中心" }).click();
  const quoteRow = page.locator(".dg-quote-table tbody tr").filter({ hasText: quoteId.slice(0, 8) });
  await expect(quoteRow).toBeVisible();
  await quoteRow.click();
  await page.getByRole("button", { name: "生成邮件草稿" }).click();
  await expect(page.getByLabel("邮件主题")).toHaveValue(/报价草稿/);
  await expect(page.getByLabel("邮件内容")).toHaveValue(/USD 1,920\.00/);
  const pdfDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载报价单 PDF" }).click();
  expect((await pdfDownload).suggestedFilename()).toMatch(/\.pdf$/);
  await page.getByRole("navigation", { name: "工作区导航" }).getByRole("link", { name: "工作台", exact: true }).click();
  await expect(page.getByRole("heading", { name: "询盘与报价工作台" })).toBeVisible();
  const run = await page.request.get(`${API}/api/v1/runs/${allowed.run_id}`);
  const result = await run.json();
  expect(result.quote.breakdown.currency).toBe("USD");
  expect(result.quote.lines).toHaveLength(2);
  expect(result.policy_evidence[0].version).toBe("v2");
  expect(result.trace.some((event: { step: string }) => event.step === "retrieve_policy")).toBeTruthy();

  await page.getByLabel("询盘内容").fill(`Please quote ${productA} 10 units at 8% discount.`);
  await page.getByRole("button", { name: "运行询盘" }).click();
  await expect(page.locator(".dg-decision-name")).toHaveAttribute("data-decision", "DENY");
  await expect(page.locator(".dg-decision-code")).toHaveAttribute("data-reason-code", "DISCOUNT_LIMIT_EXCEEDED");
  await expect(page.locator(".dg-quote")).toHaveCount(0);

  await apiPost(page, "/api/v1/auth/accept-invitation", {
    token: invitationB.token, display_name: "Sales B", password: "secure-sales-password-456",
  });
  await page.getByRole("button", { name: "退出登录" }).click();
  await page.getByLabel("邮箱").fill(`sales-b-${suffix}@dealguard.test`);
  await page.getByLabel("密码").fill("secure-sales-password-456");
  await page.getByRole("button", { name: "登录", exact: true }).last().click();
  await expect.poll(async () => (await page.request.get(`${API}/api/v1/auth/me`)).json().then((user) => user.display_name)).toBe("Sales B");
  const forbiddenQuote = await page.request.get(`${API}/api/v1/quotes/${quoteId}`);
  expect(forbiddenQuote.status()).toBe(404);
  expect(pageErrors).toEqual([]);
});

test("workspace admin can assign a product-specific policy scope", async ({ page }) => {
  const suffix = Date.now().toString().slice(-8);
  await registerAdmin(page, suffix);
  const productId = `P-SCOPE${suffix}`;
  await apiPost(page, "/api/v1/products", {
    id: productId, name: "Scoped Product", currency: "USD", unit_price: "20.00", stock: 100,
  });

  await page.getByLabel("政策编号").fill(`SCOPE-${suffix}`);
  await page.getByLabel("政策版本").fill("v1");
  await page.getByLabel("政策文本文件").setInputFiles({
    name: "scope-policy.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("Eligible product-specific promotion for this selected product."),
  });
  await page.getByRole("button", { name: "仅保存原文草稿" }).click();
  await page.getByLabel("政策适用类型").selectOption("product_specific");
  await page.getByLabel("适用商品 ID").fill(productId);
  await expect(page.getByLabel("适用商品 ID")).toHaveValue(productId);
  await page.getByRole("button", { name: "保存规则" }).click();
  await expect(page.getByText("结构化规则已保存为待审草稿")).toBeVisible();
  await page.getByRole("button", { name: "保存并激活" }).click();
  await expect(page.getByText(`SCOPE-${suffix}@v1 已激活`)).toBeVisible();

  const customer = await apiPost<{ id: string }>(page, "/api/v1/customers", { name: `Diff Buyer ${suffix}`, tier: "VIP" });
  await createPolicy(page, `DIFF-${suffix}`, "v1", 10, "Discount policy v1 permits up to 10 percent.");
  await createPolicy(page, `DIFF-${suffix}`, "v2", 5, "Discount policy v2 permits up to 5 percent.");
  await page.reload();
  await expect(page.getByRole("heading", { name: "询盘与报价工作台" })).toBeVisible();
  await page.getByLabel("客户账户").selectOption(customer.id);
  await page.getByLabel("询盘内容").fill(`Please quote ${productId} 10 units at 8% discount.`);
  await page.getByLabel("基准政策").selectOption(`DIFF-${suffix}@v1`);
  await page.getByLabel("对比政策").selectOption(`DIFF-${suffix}@v2`);
  await page.getByRole("button", { name: "比较当前询盘" }).click();
  await expect(page.locator(".dg-diff-policy").nth(0)).toHaveAttribute("data-decision", "ALLOW");
  await expect(page.locator(".dg-diff-policy").nth(1)).toHaveAttribute("data-decision", "DENY");
});

test("sales can negotiate, issue an immutable revision, and collect scoped customer acceptance", async ({ page }) => {
  const suffix = Date.now().toString().slice(-8);
  await registerAdmin(page, suffix);
  const customer = await apiPost<{ id: string }>(page, "/api/v1/customers", { name: `Lifecycle Buyer ${suffix}`, tier: "VIP" });
  const productId = `P-LIFE${suffix}`;
  await apiPost(page, "/api/v1/products", { id: productId, name: "Lifecycle Product", currency: "USD", unit_price: "100.00", stock: 100 });
  await createPolicy(page, `LIFE-${suffix}`, "v1", 10, "VIP direct discount limit is 10 percent.");
  const invitation = await apiPost<{ token: string }>(page, "/api/v1/auth/invitations", {
    email: `lifecycle-sales-${suffix}@dealguard.test`, role: "sales", customer_id: customer.id,
  });
  await apiPost(page, "/api/v1/auth/accept-invitation", {
    token: invitation.token, display_name: "Lifecycle Sales", password: "secure-sales-password-123",
  });
  await page.reload();
  await expect(page.getByRole("heading", { name: "询盘与报价工作台" })).toBeVisible();
  await page.getByLabel("询盘内容").fill(`Please quote ${productId} 10 units at 5% discount.`);
  await page.getByRole("button", { name: "运行询盘" }).click();
  await expect(page.locator(".dg-decision-name")).toHaveAttribute("data-decision", "ALLOW");
  const quoteId = (await page.locator(".dg-quote .dg-quote-id").innerText()).trim();
  expect(quoteId).toMatch(/^[0-9a-f-]{36}$/i);

  await page.getByRole("navigation", { name: "工作区导航" }).getByRole("link", { name: "报价中心" }).click();
  const quoteRow = page.locator(".dg-quote-table tbody tr").filter({ hasText: quoteId!.slice(0, 8) });
  await expect(quoteRow).toBeVisible(); await quoteRow.click();
  await page.getByLabel("客户要求折扣").fill("12");
  await page.getByLabel("报价数量").fill("12");
  await page.getByRole("button", { name: "预览合规方案" }).click();
  await expect(page.getByText("10.00%", { exact: false })).toBeVisible();
  await expect(page.getByText("$1,080.00")).toBeVisible();
  await page.getByRole("button", { name: "保存为报价修订" }).click();
  await expect(page.getByText("待发布修订", { exact: true })).toBeVisible();
  await expect(page.locator(".dg-quote-line-table tbody tr")).toContainText("12");
  await page.getByRole("button", { name: "发布报价版本" }).click();
  const linkInput = page.getByLabel("客户专属确认链接");
  await expect(linkInput).toBeVisible();
  const publicLink = await linkInput.inputValue();
  expect(publicLink).toContain("/public/quote/");

  const customerPage = await page.context().newPage();
  await customerPage.goto(publicLink);
  await expect(customerPage.getByRole("heading", { name: `Lifecycle Buyer ${suffix}` })).toBeVisible();
  await expect(customerPage.locator(".dg-public-quote-summary").getByText("$1,080.00")).toBeVisible();
  await customerPage.getByRole("button", { name: "接受报价" }).click();
  await expect(customerPage.getByText("您已接受此报价。")).toBeVisible();
  const quote = await page.request.get(`${API}/api/v1/quotes/${quoteId}`);
  expect((await quote.json()).status).toBe("ACCEPTED");
  await customerPage.close();
});
