import type {
  Carrier,
  Country,
  Order,
  OrderStatus,
  Return,
  ReturnStatus,
  ServiceType,
  Warehouse,
  WarehouseManagementSystem,
} from "../types/models";

/* ---------------------------------------
   Search criteria and accessor contracts
---------------------------------------- */

export interface SearchCriteria<TCategory = string, TStatus = string> {
  category?: TCategory;
  minPrice?: number;
  maxPrice?: number;
  status?: TStatus;
  fromDate?: Date;
  toDate?: Date;
  location?: string | Country;
}

export interface SearchAccessors<T, TCategory = string, TStatus = string> {
  getCategory?: (item: T) => TCategory | undefined;
  getPrice?: (item: T) => number | undefined;
  getStatus?: (item: T) => TStatus | undefined;
  getDate?: (item: T) => Date | undefined;
  getLocation?: (item: T) => string | Country | Array<string | Country> | undefined;
}

/* ---------------------------------------
   Small helpers (single purpose)
---------------------------------------- */

function normalizeText(value: string): string {
  return value.trim().toLowerCase();
}

function isValidDate(value: Date): boolean {
  return !Number.isNaN(value.getTime());
}

function normalizeRange(
  min?: number,
  max?: number
): { min?: number; max?: number } {
  if (min !== undefined && max !== undefined && min > max) {
    return { min: max, max: min };
  }
  return { min, max };
}

function isNumberInRange(value: number, min?: number, max?: number): boolean {
  if (!Number.isFinite(value)) return false;
  if (min !== undefined && value < min) return false;
  if (max !== undefined && value > max) return false;
  return true;
}

function toLocationList(
  value: string | Country | Array<string | Country> | undefined
): Array<string | Country> {
  if (value === undefined) return [];
  return Array.isArray(value) ? value : [value];
}

function matchesLocationValue(
  target: string | Country,
  source: string | Country
): boolean {
  return normalizeText(String(target)) === normalizeText(String(source));
}

/* ---------------------------------------
   Search functions
---------------------------------------- */

// 1) Category
export function filterByCategory<T, TCategory>(
  items: T[],
  category: TCategory,
  getCategory: (item: T) => TCategory | undefined
): T[] {
  return items.filter((item: T): boolean => getCategory(item) === category);
}

// 2) Price range
export function filterByPriceRange<T>(
  items: T[],
  getPrice: (item: T) => number | undefined,
  minPrice?: number,
  maxPrice?: number
): T[] {
  if (minPrice === undefined && maxPrice === undefined) return [...items];

  const normalized: { min?: number; max?: number } = normalizeRange(minPrice, maxPrice);

  return items.filter((item: T): boolean => {
    const value: number | undefined = getPrice(item);
    if (value === undefined) return false;
    return isNumberInRange(value, normalized.min, normalized.max);
  });
}

// 3) Status
export function filterByStatus<T, TStatus>(
  items: T[],
  status: TStatus,
  getStatus: (item: T) => TStatus | undefined
): T[] {
  return items.filter((item: T): boolean => getStatus(item) === status);
}

// 4) Date range
export function filterByDateRange<T>(
  items: T[],
  getDate: (item: T) => Date | undefined,
  fromDate?: Date,
  toDate?: Date
): T[] {
  if (fromDate === undefined && toDate === undefined) return [...items];

  const range: { min?: number; max?: number } = normalizeRange(
    fromDate && isValidDate(fromDate) ? fromDate.getTime() : undefined,
    toDate && isValidDate(toDate) ? toDate.getTime() : undefined
  );

  return items.filter((item: T): boolean => {
    const date: Date | undefined = getDate(item);
    if (date === undefined || !isValidDate(date)) return false;
    return isNumberInRange(date.getTime(), range.min, range.max);
  });
}

// 5) Location
export function filterByLocation<T>(
  items: T[],
  location: string | Country,
  getLocation: (item: T) => string | Country | Array<string | Country> | undefined
): T[] {
  return items.filter((item: T): boolean => {
    const locations: Array<string | Country> = toLocationList(getLocation(item));
    return locations.some((value: string | Country): boolean =>
      matchesLocationValue(location, value)
    );
  });
}

// Combined criteria search
export function searchByCriteria<T, TCategory = string, TStatus = string>(
  items: T[],
  criteria: SearchCriteria<TCategory, TStatus>,
  accessors: SearchAccessors<T, TCategory, TStatus>
): T[] {
  let result: T[] = [...items];

  if (criteria.category !== undefined && accessors.getCategory) {
    result = filterByCategory(result, criteria.category, accessors.getCategory);
  }

  if ((criteria.minPrice !== undefined || criteria.maxPrice !== undefined) && accessors.getPrice) {
    result = filterByPriceRange(result, accessors.getPrice, criteria.minPrice, criteria.maxPrice);
  }

  if (criteria.status !== undefined && accessors.getStatus) {
    result = filterByStatus(result, criteria.status, accessors.getStatus);
  }

  if ((criteria.fromDate !== undefined || criteria.toDate !== undefined) && accessors.getDate) {
    result = filterByDateRange(result, accessors.getDate, criteria.fromDate, criteria.toDate);
  }

  if (criteria.location !== undefined && accessors.getLocation) {
    result = filterByLocation(result, criteria.location, accessors.getLocation);
  }

  return result;
}

/* ---------------------------------------
   Model-specific search wrappers
---------------------------------------- */

export function searchOrders(
  orders: Order[],
  criteria: SearchCriteria<never, OrderStatus>,
  dateField: "placedAt" | "expectedDelivery" = "placedAt"
): Order[] {
  return searchByCriteria<Order, never, OrderStatus>(orders, criteria, {
    getStatus: (order: Order): OrderStatus => order.status,
    getDate: (order: Order): Date =>
      dateField === "placedAt" ? order.placedAt : order.expectedDelivery,
    getLocation: (order: Order): string => order.destination,
  });
}

export function searchReturns(
  returnsList: Return[],
  criteria: SearchCriteria<never, ReturnStatus>,
  dateField: "requestedAt" | "expectedArrival" = "requestedAt"
): Return[] {
  return searchByCriteria<Return, never, ReturnStatus>(returnsList, criteria, {
    getStatus: (item: Return): ReturnStatus => item.status,
    getDate: (item: Return): Date =>
      dateField === "requestedAt" ? item.requestedAt : item.expectedArrival,
    getLocation: (item: Return): string => item.client,
  });
}

export function searchCarriers(
  carriers: Carrier[],
  criteria: SearchCriteria<ServiceType, never>
): Carrier[] {
  return searchByCriteria<Carrier, ServiceType, never>(carriers, criteria, {
    getCategory: (carrier: Carrier): ServiceType => carrier.service,
    getLocation: (carrier: Carrier): Country[] => carrier.location,
  });
}

export function searchWarehouses(
  warehouses: Warehouse[],
  criteria: SearchCriteria<WarehouseManagementSystem, never>
): Warehouse[] {
  return searchByCriteria<Warehouse, WarehouseManagementSystem, never>(warehouses, criteria, {
    getCategory: (warehouse: Warehouse): WarehouseManagementSystem =>
      warehouse.managementSystem,
    getLocation: (warehouse: Warehouse): Array<string | Country> => [
      warehouse.country,
      warehouse.location,
    ],
  });
}

/* ---------------------------------------
   Sorting
---------------------------------------- */

export type SortDirection = "asc" | "desc";
export type SortValue = string | number | Date | boolean | null | undefined;

export interface SortField<T> {
  selector: (item: T) => SortValue;
  direction?: SortDirection;
}

function compareNullable(left: SortValue, right: SortValue): number | null {
  if (left == null && right == null) return 0;
  if (left == null) return 1;
  if (right == null) return -1;
  return null;
}

function compareDates(left: SortValue, right: SortValue): number | null {
  if (left instanceof Date && right instanceof Date) {
    return left.getTime() - right.getTime();
  }
  return null;
}

function compareNumbers(left: SortValue, right: SortValue): number | null {
  if (typeof left === "number" && typeof right === "number") {
    return left - right;
  }
  return null;
}

function compareBooleans(left: SortValue, right: SortValue): number | null {
  if (typeof left === "boolean" && typeof right === "boolean") {
    return Number(left) - Number(right);
  }
  return null;
}

function compareStrings(left: SortValue, right: SortValue): number {
  return String(left).localeCompare(String(right), undefined, {
    sensitivity: "base",
    numeric: true,
  });
}

function compareSortValues(left: SortValue, right: SortValue): number {
  const nullableResult: number | null = compareNullable(left, right);
  if (nullableResult !== null) return nullableResult;

  const dateResult: number | null = compareDates(left, right);
  if (dateResult !== null) return dateResult;

  const numberResult: number | null = compareNumbers(left, right);
  if (numberResult !== null) return numberResult;

  const booleanResult: number | null = compareBooleans(left, right);
  if (booleanResult !== null) return booleanResult;

  return compareStrings(left, right);
}

export function sortByField<T>(
  items: T[],
  selector: (item: T) => SortValue,
  direction: SortDirection = "asc"
): T[] {
  const sorted: T[] = [...items].sort((a: T, b: T): number =>
    compareSortValues(selector(a), selector(b))
  );

  if (direction === "asc") return sorted;
  return sorted.reverse();
}

export function sortByMultipleFields<T>(items: T[], fields: SortField<T>[]): T[] {
  if (fields.length === 0) return [...items];

  return [...items].sort((leftItem: T, rightItem: T): number => {
    for (const field of fields) {
      const direction: SortDirection = field.direction ?? "asc";
      const base: number = compareSortValues(field.selector(leftItem), field.selector(rightItem));
      if (base !== 0) return direction === "asc" ? base : -base;
    }
    return 0;
  });
}

export function sortAlphabetically<T>(
  items: T[],
  selector: (item: T) => string | null | undefined,
  direction: SortDirection = "asc"
): T[] {
  return sortByField<T>(items, selector, direction);
}