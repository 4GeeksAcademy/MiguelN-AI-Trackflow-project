import type {
  Carrier,
  Department,
  Order,
  Return,
  Warehouse,
  Worker,
} from "../types/models";

/**
 * 1) Linear search (for unsorted arrays)
 * Returns the first matching element or null if not found.
 */
export function linearSearch<T>(
  items: T[],
  predicate: (item: T) => boolean
): T | null {
  for (const item of items) {
    if (predicate(item)) return item;
  }
  return null;
}

/**
 * Linear search variant that returns the index.
 * Returns -1 if not found.
 */
export function linearSearchIndex<T>(
  items: T[],
  predicate: (item: T) => boolean
): number {
  for (let index: number = 0; index < items.length; index += 1) {
    if (predicate(items[index])) return index;
  }
  return -1;
}

export type CompareFn<T> = (left: T, right: T) => number;

type ComparablePrimitive = string | number | boolean | Date;

function toComparableNumber(value: ComparablePrimitive): number | string {
  if (value instanceof Date) return value.getTime();
  if (typeof value === "boolean") return Number(value);
  return value;
}

/**
 * Default comparator for primitive sortable values.
 */
function defaultCompare(left: ComparablePrimitive, right: ComparablePrimitive): number {
  const leftValue: number | string = toComparableNumber(left);
  const rightValue: number | string = toComparableNumber(right);

  if (leftValue === rightValue) return 0;
  return leftValue < rightValue ? -1 : 1;
}

/**
 * 2) Binary search (for previously sorted arrays)
 * Returns the index of target or -1 if not found.
 */
export function binarySearch<T>(
  sortedItems: T[],
  target: T,
  compare?: CompareFn<T>
): number {
  const comparator: CompareFn<T> =
    compare ?? (defaultCompare as unknown as CompareFn<T>);

  let low: number = 0;
  let high: number = sortedItems.length - 1;

  while (low <= high) {
    const mid: number = Math.floor((low + high) / 2);
    const comparison: number = comparator(sortedItems[mid], target);

    if (comparison === 0) return mid;
    if (comparison < 0) low = mid + 1;
    else high = mid - 1;
  }

  return -1;
}

/**
 * Binary search by selector/key in a sorted array.
 * Array must be sorted ASC by the same selector.
 */
export function binarySearchBy<T, K>(
  sortedItems: T[],
  target: K,
  selector: (item: T) => K,
  compare?: CompareFn<K>
): number {
  const comparator: CompareFn<K> =
    compare ?? (defaultCompare as unknown as CompareFn<K>);

  let low: number = 0;
  let high: number = sortedItems.length - 1;

  while (low <= high) {
    const mid: number = Math.floor((low + high) / 2);
    const selectedValue: K = selector(sortedItems[mid]);
    const comparison: number = comparator(selectedValue, target);

    if (comparison === 0) return mid;
    if (comparison < 0) low = mid + 1;
    else high = mid - 1;
  }

  return -1;
}

/* ---------------------------------------
   Model-specific linear search helpers
---------------------------------------- */

function normalizeId(value: string): string {
  return value.trim().toLowerCase();
}

export function findOrderById(orders: Order[], orderID: string): Order | null {
  const target: string = normalizeId(orderID);
  return linearSearch<Order>(orders, (order: Order): boolean => normalizeId(order.orderID) === target);
}

export function findReturnById(returnsList: Return[], returnID: string): Return | null {
  const target: string = normalizeId(returnID);
  return linearSearch<Return>(
    returnsList,
    (item: Return): boolean => normalizeId(item.returnID) === target
  );
}

export function findWorkerById(workers: Worker[], workerID: string): Worker | null {
  const target: string = normalizeId(workerID);
  return linearSearch<Worker>(workers, (worker: Worker): boolean => normalizeId(worker.workerID) === target);
}

export function findCarrierById(carriers: Carrier[], carrierID: string): Carrier | null {
  const target: string = normalizeId(carrierID);
  return linearSearch<Carrier>(carriers, (carrier: Carrier): boolean => normalizeId(carrier.carrierID) === target);
}

export function findWarehouseById(
  warehouses: Warehouse[],
  warehouseID: string
): Warehouse | null {
  const target: string = normalizeId(warehouseID);
  return linearSearch<Warehouse>(
    warehouses,
    (warehouse: Warehouse): boolean => normalizeId(warehouse.warehouseID) === target
  );
}

export function findDepartmentById(
  departments: Department[],
  departmentID: string
): Department | null {
  const target: string = normalizeId(departmentID);
  return linearSearch<Department>(
    departments,
    (department: Department): boolean => normalizeId(department.departmentID) === target
  );
}