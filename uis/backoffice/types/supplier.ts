export type SupplierCountry = "USA" | "Spain";

export type SupplierCurrency = "USD" | "EUR";

export type SupplierStatus = "active" | "suspended";

export type SupplierCategory =
  | "carrier_last_mile"
  | "carrier_international"
  | "warehouse_supplies"
  | "packaging_materials"
  | "reverse_logistics"
  | "fleet_maintenance"
  | "it_and_wms_software"
  | "cleaning_and_facilities";

export const SUPPLIER_COUNTRIES: readonly SupplierCountry[] = ["USA", "Spain"];

export const SUPPLIER_STATUSES: readonly SupplierStatus[] = ["active", "suspended"];

export const SUPPLIER_CATEGORIES: readonly SupplierCategory[] = [
  "carrier_last_mile",
  "carrier_international",
  "warehouse_supplies",
  "packaging_materials",
  "reverse_logistics",
  "fleet_maintenance",
  "it_and_wms_software",
  "cleaning_and_facilities",
];

/** The API rejects any payload whose currency does not match its country. */
export const CURRENCY_BY_COUNTRY: Record<SupplierCountry, SupplierCurrency> = {
  USA: "USD",
  Spain: "EUR",
};

export type Supplier = {
  id: number;
  name: string;
  country: SupplierCountry;
  categories: SupplierCategory[];
  rate_per_shipment: number;
  currency: SupplierCurrency;
  status: SupplierStatus;
  service_zone: string | null;
  contact_email: string | null;
  notes: string | null;
  updated_at: string;
};

export type SupplierListItem = Pick<
  Supplier,
  "id" | "name" | "country" | "categories" | "rate_per_shipment" | "currency" | "status" | "service_zone"
>;

export type SupplierCreatePayload = {
  name: string;
  country: SupplierCountry;
  categories: SupplierCategory[];
  rate_per_shipment: number;
  currency: SupplierCurrency;
  status: SupplierStatus;
  service_zone: string | null;
  contact_email: string | null;
  notes: string | null;
};

export type SupplierFormValues = {
  name: string;
  country: SupplierCountry;
  categories: SupplierCategory[];
  ratePerShipment: string;
  status: SupplierStatus;
  serviceZone: string;
  contactEmail: string;
  notes: string;
};

export type SupplierFilters = {
  country: SupplierCountry | "";
  category: SupplierCategory | "";
};
