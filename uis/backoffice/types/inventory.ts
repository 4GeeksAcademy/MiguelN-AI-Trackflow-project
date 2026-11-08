export type Warehouse = "Monterrey" | "Zaragoza";

export type ProductSummary = {
  id: string;
  name: string;
  sku: string;
  warehouse: Warehouse;
};

export type Product = ProductSummary & { current_stock: number };
export type ProductAvailability = Pick<Product, "id" | "current_stock">;
export type ProductCreate = Omit<ProductSummary, "id">;
export type OrderDirection = "inbound" | "outbound";
export type OrderCreate = { product_id: string; quantity: number };

type OrderHistoryFields = {
  id: string;
  product_id: string;
  quantity: number;
  created_at: string;
  user_uuid: string;
  product_name: string;
  product_sku: string;
  warehouse: Warehouse;
};

type MovementConfirmationFields = {
  quantity: number;
  product_name: string;
  warehouse: Warehouse;
};

export type InboundOrder = MovementConfirmationFields & { direction: "inbound" };
export type OutboundOrder = MovementConfirmationFields & { direction: "outbound" };
export type InventoryOrder = OrderHistoryFields & { direction: OrderDirection };