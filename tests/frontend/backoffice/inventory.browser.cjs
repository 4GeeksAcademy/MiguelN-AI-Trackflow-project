const assert = require("node:assert/strict");
const { mkdirSync } = require("node:fs");
const { chromium } = require("playwright");

const base = process.env.BACKOFFICE_URL || "http://localhost:3001";
const root = "/backoffice/inventory";
const artifacts = process.env.INVENTORY_SCREENSHOTS || "/tmp/trackflow-inventory-screenshots";
const actor = "10000000-0000-4000-8000-000000000001";
const products = [
  { id: "20000000-0000-4000-8000-000000000001", name: "Black Running Shoes - Size 42", sku: "SHOE-BLK-42", warehouse: "Monterrey", current_stock: 45 },
  { id: "20000000-0000-4000-8000-000000000002", name: "Dell Laptop 15 inch", sku: "LAPTOP-DELL-15", warehouse: "Zaragoza", current_stock: 8 },
  { id: "20000000-0000-4000-8000-000000000003", name: "Perfume", sku: "PERFUME", warehouse: "Monterrey", current_stock: 0 },
];
const movement = (direction, product, quantity) => ({
  id: `30000000-0000-4000-8000-${direction === "inbound" ? "000000000001" : "000000000002"}`,
  direction, product_id: product.id, quantity, user_uuid: actor, created_at: "2026-10-06T07:00:00Z",
  product: { id: product.id, name: product.name, sku: product.sku, warehouse: product.warehouse },
});
let checks = 0;
function check(value, message) { assert.ok(value, message); checks += 1; }
async function visible(page, text) {
  await page.getByText(text, { exact: true }).waitFor({ state: "visible" });
  checks += 1;
}
async function noOverflow(page) {
  check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), "No viewport overflow");
}

async function environment(browser, authenticated = true, viewport = { width: 1280, height: 900 }) {
  const context = await browser.newContext({ viewport });
  if (authenticated) await context.addInitScript(() => localStorage.setItem("trackflow.access_token", "browser-test-session"));
  const state = { requests: [], productsStatus: 200, ordersStatus: 200, stockStatus: 200, postStatus: 201, sessionStatus: 200, empty: false, stock: new Map(products.map((product) => [product.id, product.current_stock])) };
  await context.route("**/*", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (!path.startsWith("/inventory/") && path !== "/auth/me" && path !== "/auth/login") return route.continue();
    const answer = (status, body) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body), headers: { "access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*" } });
    if (request.method() === "OPTIONS") return answer(200, {});
    state.requests.push({ path, method: request.method(), headers: request.headers(), body: request.postDataJSON() });
    if (path === "/auth/login") return answer(200, { access_token: "browser-test-session", token_type: "bearer" });
    if (path === "/auth/me") return answer(state.sessionStatus, state.sessionStatus === 200 ? { id: actor, email: "operations@example.test", role: "user", is_active: true, profile: null } : { detail: "Session expired" });
    if (path === "/inventory/products") {
      if (state.offline) return route.abort("failed");
      if (state.html) return route.fulfill({ status: 502, contentType: "text/html", body: "<html>Bad Gateway</html>" });
      return answer(state.productsStatus, state.productsStatus === 200 ? state.malformed ? {} : state.empty ? [] : products : { detail: { field: null, message: "Products temporarily unavailable." } });
    }
    if (path === "/inventory/orders") return answer(state.ordersStatus, state.ordersStatus === 200 ? state.empty ? [] : [movement("inbound", products[0], 4), movement("outbound", products[1], 2)] : { detail: { field: null, message: "Orders temporarily unavailable." } });
    if (request.method() === "POST") {
      if (state.holdWrite) await new Promise((resolve) => { state.releaseWrite = resolve; });
      const payload = request.postDataJSON();
      const product = products.find(({ id }) => id === payload.product_id);
      if (state.postStatus === 400) return answer(400, { detail: { field: "quantity", message: "Insufficient stock in Zaragoza: 1 units available, 2 requested." } });
      if (state.postStatus === 422) return answer(422, { detail: [{ loc: ["body", "quantity"], msg: "Quantity must be a whole number." }] });
      if (state.postStatus === 500) return answer(500, { detail: { field: null, message: "Unable to register this order. Please try again later." } });
      return answer(201, movement(path.endsWith("outbound") ? "outbound" : "inbound", product, payload.quantity));
    }
    const id = path.split("/").at(-1);
    const product = products.find((item) => item.id === id);
    const value = state.stock.get(id);
    const status = state.stockStatus;
    if (state.holdProduct === id) await new Promise((resolve) => { state.releaseProduct = resolve; });
    return answer(status, status === 200 ? { ...product, current_stock: value } : { detail: { field: null, message: "Current stock temporarily unavailable." } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  return { context, page, state };
}

async function run() {
  mkdirSync(artifacts, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    for (const path of ["/products", "/orders/inbound", "/orders/outbound", "/orders"]) {
      const { context, page, state } = await environment(browser, false);
      const requested = `${root}${path}?product_id=${products[1].id}`;
      await page.goto(`${base}${requested}`);
      await page.waitForURL("**/login?next=*");
      check(new URL(page.url()).searchParams.get("next") === requested, "Anonymous redirect preserves path and query");
      check(state.requests.length === 0, "No protected API requests without a session");
      await context.close();
    }

    const { context, page, state } = await environment(browser);
    await page.goto(`${base}${root}/products`);
    await visible(page, "Healthy stock");
    await visible(page, "Low stock");
    await visible(page, "Out of stock");
    check(await page.getByRole("row").count() === 4, "All products are shown");
    await noOverflow(page);
    await page.screenshot({ path: `${artifacts}/products-desktop.png`, fullPage: true });
    await page.getByRole("link", { name: `Inbound order for ${products[1].name}, Zaragoza`, exact: true }).click();
    await page.waitForFunction((id) => document.querySelector("#product")?.value === id, products[1].id);
    check(await page.getByLabel("Product", { exact: true }).inputValue() === products[1].id, "Named product preselection");
    await page.getByLabel("Quantity", { exact: true }).fill("0");
    await page.getByRole("button", { name: "Register inbound order" }).click();
    check((await page.locator("#quantity-error").textContent()).includes("whole number"), "Invalid quantity shown inline");
    check(!state.requests.some(({ method }) => method === "POST"), "Invalid quantity never submitted");

    await page.getByLabel("Quantity", { exact: true }).fill("2");
    state.postStatus = 500;
    await page.getByRole("button", { name: "Register inbound order" }).click();
    await visible(page, "Unable to register this order. Please try again later.");
    check(await page.getByLabel("Quantity", { exact: true }).inputValue() === "2", "Failed submission preserves fields");
    state.postStatus = 422;
    await page.getByRole("button", { name: "Register inbound order" }).click();
    await visible(page, "Quantity must be a whole number.");
    state.postStatus = 400;
    await page.getByRole("button", { name: "Register inbound order" }).click();
    await visible(page, "Insufficient stock in Zaragoza: 1 units available, 2 requested.");
    state.postStatus = 201;
    state.holdWrite = true;
    const beforeWrites = state.requests.filter(({ method }) => method === "POST").length;
    await page.getByRole("button", { name: "Register inbound order" }).click();
    await page.getByRole("button", { name: "Registering..." }).waitFor();
    await page.evaluate(() => document.querySelector("form").requestSubmit());
    check(state.requests.filter(({ method }) => method === "POST").length === beforeWrites + 1, "Double submit sends exactly one write");
    check(typeof state.releaseWrite === "function", "Write handler reached");
    state.releaseWrite();
    state.holdWrite = false;
    await visible(page, `Inbound order registered: 2 units of ${products[1].name} (Zaragoza).`);
    check(await page.getByLabel("Product", { exact: true }).inputValue() === "", "Success clears product including query preselection");
    check(await page.getByLabel("Quantity", { exact: true }).inputValue() === "", "Success clears quantity");

    await page.goto(`${base}${root}/orders/outbound`);
    await page.getByLabel("Product", { exact: true }).waitFor();
    check(await page.getByLabel("Quantity", { exact: true }).isDisabled(), "Quantity disabled before stock selection");
    state.holdProduct = products[0].id;
    await page.getByLabel("Product", { exact: true }).selectOption(products[0].id);
    await visible(page, "Loading current stock...");
    await page.getByLabel("Product", { exact: true }).selectOption(products[1].id);
    await page.getByLabel("Quantity", { exact: true }).waitFor({ state: "visible" });
    await page.waitForFunction(() => !document.querySelector("#quantity").disabled);
    check((await page.locator("form").textContent()).includes("8 units available"), "Stock updates for selected product");
    check(typeof state.releaseProduct === "function", "Delayed stock request reached");
    const delayed = page.waitForResponse((response) => new URL(response.url()).pathname === `/inventory/products/${products[0].id}`);
    state.releaseProduct();
    await delayed;
    check((await page.locator("form").textContent()).includes("8 units available"), "Obsolete stock success cannot overwrite current selection");
    state.holdProduct = null;

    await page.getByLabel("Quantity", { exact: true }).fill("9");
    await visible(page, "Only 8 units are available. Reduce the quantity.");
    check(await page.getByRole("button", { name: "Register outbound order" }).isDisabled(), "Over-stock quantity blocked");
    await page.getByLabel("Quantity", { exact: true }).fill("8");
    check(await page.getByRole("button", { name: "Register outbound order" }).isEnabled(), "Exact stock permitted");
    await page.getByLabel("Quantity", { exact: true }).fill("2");
    state.postStatus = 400;
    state.stock.set(products[1].id, 1);
    await page.getByRole("button", { name: "Register outbound order" }).click();
    await visible(page, "Insufficient stock in Zaragoza: 1 units available, 2 requested.");
    check(await page.locator("#quantity-error").textContent() === "Insufficient stock in Zaragoza: 1 units available, 2 requested.", "Server 400 remains next to quantity");
    await page.waitForFunction(() => document.querySelector("form").textContent.includes("1 units available"));
    check(await page.getByLabel("Quantity", { exact: true }).inputValue() === "2", "Stock refresh preserves rejected quantity");

    state.stockStatus = 500;
    state.holdProduct = products[0].id;
    await page.getByLabel("Product", { exact: true }).selectOption(products[0].id);
    await visible(page, "Loading current stock...");
    state.stockStatus = 200;
    await page.getByLabel("Product", { exact: true }).selectOption(products[2].id);
    await visible(page, "Out of stock");
    const delayedError = page.waitForResponse((response) => new URL(response.url()).pathname === `/inventory/products/${products[0].id}`);
    state.releaseProduct();
    await delayedError;
    check(await page.getByText("Current stock temporarily unavailable.", { exact: true }).count() === 0, "Obsolete stock failure ignored");
    check(await page.getByRole("button", { name: "Register outbound order" }).isDisabled(), "Zero stock cannot submit");
    state.holdProduct = null;

    state.stockStatus = 500;
    await page.getByLabel("Product", { exact: true }).selectOption(products[0].id);
    await visible(page, "Current stock temporarily unavailable.");
    check(await page.getByLabel("Quantity", { exact: true }).isDisabled(), "Unknown stock cannot accept quantity");
    state.stockStatus = 200;
    await page.getByRole("button", { name: "Try again" }).click();
    await page.waitForFunction(() => !document.querySelector("#quantity").disabled);
    state.postStatus = 500;
    await page.getByLabel("Quantity", { exact: true }).fill("1");
    await page.getByRole("button", { name: "Register outbound order" }).click();
    await visible(page, "Unable to register this order. Please try again later.");
    state.postStatus = 201;
    await page.getByRole("button", { name: "Register outbound order" }).click();
    await visible(page, `Outbound order registered: 1 units of ${products[0].name} (Monterrey).`);

    await page.goto(`${base}${root}/orders`);
    await visible(page, "Inbound");
    await visible(page, "Outbound");
    check(await page.getByRole("cell", { name: actor, exact: true }).count() === 2, "Full creator UUID displayed on each row");
    check(await page.locator("tbody button").count() === 0, "History has no mutation actions");
    check(await page.locator("time").count() === 2, "Creation dates use semantic time elements");
    check(await page.locator('[aria-current="page"]').count() === 1, "Navigation has one current page");
    await noOverflow(page);
    await page.screenshot({ path: `${artifacts}/orders-desktop.png`, fullPage: true });

    state.ordersStatus = 500;
    await page.goto(`${base}${root}/orders`);
    await page.locator("main").getByRole("alert").waitFor();
    check((await page.locator("main").getByRole("alert").textContent()).includes("Orders temporarily unavailable."), "History API failure shown with retry");
    state.ordersStatus = 200;
    await page.getByRole("button", { name: "Try again" }).click();
    await visible(page, "Inbound");
    state.empty = true;
    await page.goto(`${base}${root}/orders`);
    await visible(page, "No orders on record.");
    await page.goto(`${base}${root}/products`);
    await visible(page, "No products on record.");
    await page.goto(`${base}${root}/orders/inbound`);
    await visible(page, "No products are available for an order.");
    check(await page.getByRole("button", { name: "Register inbound order" }).isDisabled(), "Empty products cannot submit");
    state.empty = false;

    for (const mode of ["productsStatus", "html", "offline", "malformed"]) {
      state[mode] = mode === "productsStatus" ? 500 : true;
      await page.goto(`${base}${root}/products`);
      await page.getByRole("button", { name: "Try again" }).waitFor();
      const text = await page.locator('main [role="alert"]').textContent();
      check(Boolean(text) && !/Traceback|<html>|Failed to fetch|status 502/.test(text), `${mode} failure visible and readable`);
      state[mode] = mode === "productsStatus" ? 200 : false;
      await page.getByRole("button", { name: "Try again" }).click();
      await visible(page, "Healthy stock");
    }

    await page.setViewportSize({ width: 390, height: 844 });
    for (const [path, ready] of [["products", "Low stock"], ["orders/inbound", "Register inbound order"], ["orders/outbound", "Register outbound order"], ["orders", "Outbound"]]) {
      await page.goto(`${base}${root}/${path}`);
      await visible(page, ready);
      if (path === "orders/outbound") {
        await page.getByLabel("Product", { exact: true }).selectOption(products[1].id);
        await page.waitForFunction(() => !document.querySelector("#quantity").disabled);
      }
      await noOverflow(page);
      await page.screenshot({ path: `${artifacts}/${path.replaceAll("/", "-")}-mobile.png`, fullPage: true });
    }
    const writes = state.requests.filter(({ method }) => method === "POST");
    check(writes.every(({ body }) => Object.keys(body).sort().join(",") === "product_id,quantity" && Number.isInteger(body.quantity)), "Movement payloads contain only product ID and integer quantity");
    check(state.requests.filter(({ path }) => path.startsWith("/inventory/")).every(({ headers }) => headers.authorization === "Bearer browser-test-session"), "Bearer header on every inventory request");
    await context.close();

    for (const path of ["/products", "/orders/inbound", "/orders/outbound", "/orders"]) {
      const expired = await environment(browser);
      expired.state.sessionStatus = 401;
      await expired.page.goto(`${base}${root}${path}`);
      await expired.page.waitForURL("**/login?next=*");
      check(await expired.page.evaluate(() => localStorage.getItem("trackflow.access_token")) === null, "Expired token cleared");
      check(!expired.state.requests.some(({ path: requestPath }) => requestPath.startsWith("/inventory/")), "Inventory hidden until session validated");
      await expired.context.close();
    }
    const revoked = await environment(browser);
    revoked.state.productsStatus = 401;
    await revoked.page.goto(`${base}${root}/products`);
    await revoked.page.waitForURL("**/login?next=*");
    check(await revoked.page.evaluate(() => localStorage.getItem("trackflow.access_token")) === null, "Inventory endpoint 401 ends session");
    await revoked.context.close();
    console.log(`PASS: ${checks} inventory browser checks. Screenshots: ${artifacts}`);
  } finally {
    await browser.close();
  }
}

run().catch((error) => { console.error(error); process.exitCode = 1; });