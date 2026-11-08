import {
  createInboundOrder, createOutboundOrder, createProduct, describeInventoryError,
  getOrders, getProduct, getProducts, quantityError, stockLevel,
} from "@/lib/inventory";
import { ApiError, NetworkError, UnauthorizedError } from "@/lib/api-client";
import { clearToken, readToken, storeToken } from "@/lib/auth-storage";
import { installBrowser, removeBrowser } from "../shared/support/browser";
import { API, jsonResponse, scriptFetch, textResponse } from "../shared/support/http";

const product = { id: "p1", name: "Laptop", sku: "LAPTOP", warehouse: "Zaragoza", current_stock: 8 };
const availability = { id: "p1", current_stock: 8 };
const order = {
  id: "o1", product_id: "p1", quantity: 2, user_uuid: "u1", created_at: "2026-10-06T07:00:00Z",
  product_name: "Laptop", product_sku: "LAPTOP", warehouse: "Zaragoza", direction: "inbound",
};
const confirmation = { quantity: 2, product_name: "Laptop", warehouse: "Zaragoza", direction: "inbound" };

beforeEach(() => { installBrowser(); storeToken("test-session"); });
afterEach(() => { clearToken(); removeBrowser(); });

it("centralizes all six endpoints and sends bearer authorization on every call", async () => {
  const requests = scriptFetch(
    jsonResponse(200, [product]), jsonResponse(200, availability), jsonResponse(201, product),
    jsonResponse(201, confirmation), jsonResponse(201, { ...confirmation, direction: "outbound" }), jsonResponse(200, [order]),
  );
  await getProducts({ warehouse: "Zaragoza", sku: "LAPTOP" });
  await getProduct("p1");
  await createProduct({ name: "Laptop", sku: "LAPTOP", warehouse: "Zaragoza" });
  await createInboundOrder({ product_id: "p1", quantity: 2 });
  await createOutboundOrder({ product_id: "p1", quantity: 2 });
  await getOrders({ product_id: "p1" });
  expect(requests.map(({ url }) => url)).toEqual([
    `${API}/inventory/products?warehouse=Zaragoza&sku=LAPTOP`, `${API}/inventory/products/p1`,
    `${API}/inventory/products`, `${API}/inventory/products/inbound`, `${API}/inventory/products/outbound`,
    `${API}/inventory/orders?product_id=p1`,
  ]);
  expect(requests.every(({ headers }) => headers.Authorization === "Bearer test-session")).toBe(true);
  expect(requests.slice(2, 5).every(({ method, headers }) => method === "POST" && headers["Content-Type"] === "application/json")).toBe(true);
  expect(requests[3].body).toEqual({ product_id: "p1", quantity: 2 });
});

it("encodes product path segments", async () => {
  const requests = scriptFetch(jsonResponse(200, availability));
  await getProduct("part/one");
  expect(requests[0].url).toBe(`${API}/inventory/products/part%2Fone`);
});

it("accepts legitimate empty lists", async () => {
  scriptFetch(jsonResponse(200, []), jsonResponse(200, []));
  expect(await getProducts()).toEqual([]);
  expect(await getOrders()).toEqual([]);
});

it.each([null, {}, [{ ...product, current_stock: undefined }], [{ ...product, current_stock: -1 }]])(
  "rejects malformed products rather than treating them as an empty list: %j", async (body) => {
    scriptFetch(jsonResponse(200, body));
    await expect(getProducts()).rejects.toThrow("unreadable response");
  },
);

it("rejects a successful HTML response", async () => {
  scriptFetch(textResponse(200, "<html>Login</html>"));
  await expect(getProducts()).rejects.toThrow("unreadable response");
});

it("rejects malformed order history", async () => {
  scriptFetch(jsonResponse(200, [{ ...order, direction: "other" }]));
  await expect(getOrders()).rejects.toThrow("unreadable response");
});

it("rejects a movement response with the wrong direction", async () => {
  scriptFetch(jsonResponse(201, { ...confirmation, direction: "outbound" }));
  await expect(createInboundOrder({ product_id: "p1", quantity: 2 })).rejects.toThrow("unreadable response");
});

it("preserves a 400 quantity error from the API", async () => {
  const message = "Insufficient stock in Zaragoza: 1 units available, 2 requested.";
  scriptFetch(jsonResponse(400, { detail: { field: "quantity", message } }));
  try { await createOutboundOrder({ product_id: "p1", quantity: 2 }); }
  catch (error) { expect(describeInventoryError(error)).toEqual({ message, fieldErrors: { quantity: message } }); return; }
  throw new Error("Expected an API refusal");
});

it.each([400, 403, 404, 409, 500, 503])("surfaces readable API text for HTTP %i", (status) => {
  expect(describeInventoryError(new ApiError(status, { detail: { field: null, message: "Please try again later." } })).message)
    .toBe("Please try again later.");
});

it("maps FastAPI validation messages onto inputs", () => {
  expect(describeInventoryError(new ApiError(422, { detail: [{ loc: ["body", "quantity"], msg: "Must be an integer." }] })).fieldErrors)
    .toEqual({ quantity: "Must be an integer." });
});

it.each([null, { detail: "Traceback in warehouse.py:25" }, { detail: "<html>Bad Gateway</html>" }])(
  "uses readable fallback for missing or unsafe errors: %j", (body) => {
    const message = describeInventoryError(new ApiError(500, body)).message;
    expect(message).toContain("inventory service");
    expect(message).not.toMatch(/Traceback|<html>|status 500/);
  },
);

it("describes offline failures without raw browser errors", () => {
  expect(describeInventoryError(new NetworkError(new TypeError("Failed to fetch"))).message).toContain("Check your connection");
});

it("clears an expired token and rejects instead of hiding a 401", async () => {
  scriptFetch(jsonResponse(401, { detail: "Expired" }));
  await expect(getProducts()).rejects.toBeInstanceOf(UnauthorizedError);
  expect(readToken()).toBeNull();
});

it("does not request inventory when the token is missing", async () => {
  clearToken();
  const requests = scriptFetch();
  await expect(getOrders()).rejects.toBeInstanceOf(UnauthorizedError);
  expect(requests).toHaveLength(0);
});

it.each(["", "0", "-1", "1.5", "2147483648", "Infinity", "abc"])("rejects invalid quantity %s", (value) => {
  expect(quantityError(value)).not.toBeNull();
});
it.each(["1", "10", "2147483647"])("accepts quantity %s", (value) => expect(quantityError(value)).toBeNull());
it.each([[0, "Out of stock"], [1, "Low stock"], [9, "Low stock"], [10, "Healthy stock"]])(
  "classifies %i units as %s", (stock, label) => expect(stockLevel(Number(stock))).toBe(label),
);