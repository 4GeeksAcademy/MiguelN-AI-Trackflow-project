 export type Country = "Mexico" | "Spain";
export type ServiceType = "local" | "international";
export type WarehouseManagementSystem = "commercial software" | "advanced spreadsheet";

export type OrderStatus = "In Process" | "Shipped" | "Delivered" | "Returned";
export type ReturnStatus =
  | "Under review"
  | "Approved"
  | "Rejected"
  | "In transit"
  | "Refunded"
  | "Disposed";

export type ProductCondition = "Poor" | "Fair" | "Excellent" | "Like New";

export interface ProductItem {
  product: string;
  quantity: number;
}

export interface Department {
  name: string;
  departmentID: string;
  location: Country[];
  needs: string[];
  managerID: string;

  operatesIn(country: Country): boolean;
  hasNeed(need: string): boolean;
}

export interface Warehouse {
  warehouseID: string;
  location: string;
  country: Country;
  managementSystem: WarehouseManagementSystem;

  isInCountry(country: Country): boolean;
  usesManagementSystem(system: WarehouseManagementSystem): boolean;
}

export interface Worker {
  name: string;
  workerID: string;
  location: Country;
  departmentID: string;
  position: string;

  belongsToDepartment(departmentID: string): boolean;
  isBasedIn(country: Country): boolean;
}

export interface Carrier {
  name: string;
  carrierID: string;
  location: Country[];
  service: ServiceType;

  servesCountry(country: Country): boolean;
  providesService(service: ServiceType): boolean;
}

export interface Order {
  orderID: string;
  placedAt: Date;
  expectedDelivery: Date;
  packageQuantity: number;
  packageSize: string;
  weight: number;
  urgent: boolean;
  destination: string;
  products: ProductItem[];
  carrierID: string;
  warehouseID: string;
  client: string;
  status: OrderStatus;

  totalProducts(): number;
  isDelayed(referenceDate?: Date): boolean;
}

export interface Return {
  orderID: string;
  returnID: string;
  returningReason: string;
  requestedAt: Date;
  productCondition: ProductCondition;
  status: ReturnStatus;
  expectedArrival: Date;
  packageQuantity: number;
  packageSize: string;
  weight: number;
  products: ProductItem[];
  carrierID: string;
  warehouseID: string;
  client: string;

  totalProducts(): number;
  hasCoherentDates(): boolean;
}