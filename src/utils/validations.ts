import type {
  Carrier,
  Country,
  Department,
  Order,
  OrderStatus,
  ProductCondition,
  ProductItem,
  Return,
  ReturnStatus,
  ServiceType,
  Warehouse,
  WarehouseManagementSystem,
  Worker,
} from "../types/models";

/* -----------------------------
   Validation result structures
------------------------------ */

export interface ValidationError {
  field: string;
  message: string;
}

export interface ValidationResult {
  isValid: boolean;
  errors: ValidationError[];
}

/* -----------------------------
   Runtime guards / constants
------------------------------ */

const VALID_COUNTRIES: Country[] = ["Mexico", "Spain"];
const VALID_SERVICES: ServiceType[] = ["local", "international"];
const VALID_WMS: WarehouseManagementSystem[] = [
  "commercial software",
  "advanced spreadsheet",
];
const VALID_ORDER_STATUS: OrderStatus[] = [
  "In Process",
  "Shipped",
  "Delivered",
  "Returned",
];
const VALID_RETURN_STATUS: ReturnStatus[] = [
  "Under review",
  "Approved",
  "Rejected",
  "In transit",
  "Refunded",
  "Disposed",
];
const VALID_PRODUCT_CONDITION: ProductCondition[] = [
  "Poor",
  "Fair",
  "Excellent",
  "Like New",
];

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isValidDate(value: unknown): value is Date {
  return value instanceof Date && !Number.isNaN(value.getTime());
}

function inRange(value: number, min?: number, max?: number): boolean {
  if (!Number.isFinite(value)) return false;
  if (min !== undefined && value < min) return false;
  if (max !== undefined && value > max) return false;
  return true;
}

function createResult(errors: ValidationError[]): ValidationResult {
  return { isValid: errors.length === 0, errors };
}

function combineResults(results: ValidationResult[]): ValidationResult {
  const errors: ValidationError[] = [];
  for (const result of results) {
    errors.push(...result.errors);
  }
  return createResult(errors);
}

function enumIncludes<T extends string>(validValues: T[], value: T): boolean {
  return validValues.includes(value);
}

/* ----------------------------------------
   Generic business validation primitives
----------------------------------------- */

/** 1) Required fields validation */
export function validateRequiredFields(
  source: Record<string, unknown>,
  requiredFields: string[]
): ValidationResult {
  const errors: ValidationError[] = [];

  for (const field of requiredFields) {
    const value: unknown = source[field];

    if (value === undefined || value === null) {
      errors.push({ field, message: "Field is required." });
      continue;
    }

    if (typeof value === "string" && value.trim().length === 0) {
      errors.push({ field, message: "Field cannot be empty." });
    }
  }

  return createResult(errors);
}

/** 2) Numeric range validation */
export function validateNumericRange(
  field: string,
  value: number,
  min?: number,
  max?: number
): ValidationResult {
  if (inRange(value, min, max)) return createResult([]);

  const minText: string = min !== undefined ? String(min) : "-∞";
  const maxText: string = max !== undefined ? String(max) : "+∞";
  return createResult([
    {
      field,
      message: `Field must be a finite number in range [${minText}, ${maxText}].`,
    },
  ]);
}

/** 3) Date coherence validation */
export function validateDateCoherence(
  startField: string,
  startDate: Date,
  endField: string,
  endDate: Date
): ValidationResult {
  const errors: ValidationError[] = [];

  if (!isValidDate(startDate)) {
    errors.push({ field: startField, message: "Invalid date." });
  }

  if (!isValidDate(endDate)) {
    errors.push({ field: endField, message: "Invalid date." });
  }

  if (isValidDate(startDate) && isValidDate(endDate) && endDate.getTime() < startDate.getTime()) {
    errors.push({
      field: `${startField}-${endField}`,
      message: `${endField} must be greater than or equal to ${startField}.`,
    });
  }

  return createResult(errors);
}

/* -----------------------------
   Small field-level validators
------------------------------ */

function validateStringField(field: string, value: unknown): ValidationResult {
  if (isNonEmptyString(value)) return createResult([]);
  return createResult([{ field, message: "Field is required and must be a non-empty string." }]);
}

function validateEnumField<T extends string>(
  field: string,
  value: T,
  validValues: T[]
): ValidationResult {
  if (enumIncludes(validValues, value)) return createResult([]);
  return createResult([{ field, message: "Invalid value." }]);
}

function validateCountryArray(field: string, countries: Country[]): ValidationResult {
  const errors: ValidationError[] = [];

  if (!Array.isArray(countries) || countries.length === 0) {
    errors.push({ field, message: "At least one country is required." });
    return createResult(errors);
  }

  countries.forEach((country: Country, index: number): void => {
    if (!enumIncludes(VALID_COUNTRIES, country)) {
      errors.push({ field: `${field}[${index}]`, message: "Invalid country." });
    }
  });

  return createResult(errors);
}

function validatePositiveNumber(field: string, value: number): ValidationResult {
  return validateNumericRange(field, value, Number.EPSILON);
}

function validateMinNumber(field: string, value: number, min: number): ValidationResult {
  return validateNumericRange(field, value, min);
}

function validateProductItems(items: ProductItem[]): ValidationResult {
  const errors: ValidationError[] = [];

  if (!Array.isArray(items) || items.length === 0) {
    return createResult([{ field: "products", message: "At least one product item is required." }]);
  }

  items.forEach((item: ProductItem, index: number): void => {
    if (!isNonEmptyString(item.product)) {
      errors.push({
        field: `products[${index}].product`,
        message: "Product name is required.",
      });
    }

    if (!inRange(item.quantity, 1)) {
      errors.push({
        field: `products[${index}].quantity`,
        message: "Quantity must be >= 1.",
      });
    }
  });

  return createResult(errors);
}

/* -----------------------------
   Model-specific validations
------------------------------ */

export function validateDepartment(department: Department): ValidationResult {
  return combineResults([
    validateStringField("name", department.name),
    validateStringField("departmentID", department.departmentID),
    validateCountryArray("location", department.location),
    Array.isArray(department.needs)
      ? createResult([])
      : createResult([{ field: "needs", message: "Needs must be an array." }]),
    validateStringField("managerID", department.managerID),
  ]);
}

export function validateWarehouse(warehouse: Warehouse): ValidationResult {
  return combineResults([
    validateStringField("warehouseID", warehouse.warehouseID),
    validateStringField("location", warehouse.location),
    validateEnumField<Country>("country", warehouse.country, VALID_COUNTRIES),
    validateEnumField<WarehouseManagementSystem>(
      "managementSystem",
      warehouse.managementSystem,
      VALID_WMS
    ),
  ]);
}

export function validateWorker(worker: Worker): ValidationResult {
  return combineResults([
    validateStringField("name", worker.name),
    validateStringField("workerID", worker.workerID),
    validateEnumField<Country>("location", worker.location, VALID_COUNTRIES),
    validateStringField("departmentID", worker.departmentID),
    validateStringField("position", worker.position),
  ]);
}

export function validateCarrier(carrier: Carrier): ValidationResult {
  return combineResults([
    validateStringField("name", carrier.name),
    validateStringField("carrierID", carrier.carrierID),
    validateCountryArray("location", carrier.location),
    validateEnumField<ServiceType>("service", carrier.service, VALID_SERVICES),
  ]);
}

export function validateOrder(order: Order): ValidationResult {
  return combineResults([
    validateRequiredFields(order as unknown as Record<string, unknown>, [
      "orderID",
      "packageSize",
      "destination",
      "carrierID",
      "warehouseID",
      "client",
      "products",
      "placedAt",
      "expectedDelivery",
      "status",
    ]),
    validateDateCoherence("placedAt", order.placedAt, "expectedDelivery", order.expectedDelivery),
    validateMinNumber("packageQuantity", order.packageQuantity, 1),
    validatePositiveNumber("weight", order.weight),
    validateEnumField<OrderStatus>("status", order.status, VALID_ORDER_STATUS),
    validateProductItems(order.products),
  ]);
}

export function validateReturn(returnItem: Return): ValidationResult {
  return combineResults([
    validateRequiredFields(returnItem as unknown as Record<string, unknown>, [
      "orderID",
      "returnID",
      "returningReason",
      "requestedAt",
      "expectedArrival",
      "packageSize",
      "carrierID",
      "warehouseID",
      "client",
      "status",
      "productCondition",
      "products",
    ]),
    validateDateCoherence(
      "requestedAt",
      returnItem.requestedAt,
      "expectedArrival",
      returnItem.expectedArrival
    ),
    validateMinNumber("packageQuantity", returnItem.packageQuantity, 1),
    validatePositiveNumber("weight", returnItem.weight),
    validateEnumField<ReturnStatus>("status", returnItem.status, VALID_RETURN_STATUS),
    validateEnumField<ProductCondition>(
      "productCondition",
      returnItem.productCondition,
      VALID_PRODUCT_CONDITION
    ),
    validateProductItems(returnItem.products),
  ]);
}