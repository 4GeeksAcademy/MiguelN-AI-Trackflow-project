type NumericSelector<T> = (item: T) => number;
type CategorySelector<T, K extends string> = (item: T) => K;

function isValidNumber(value: number): boolean {
  return Number.isFinite(value);
}

function getValidNumbers<T>(items: T[], selector: NumericSelector<T>): number[] {
  const values: number[] = [];

  for (const item of items) {
    const value: number = selector(item);
    if (isValidNumber(value)) values.push(value);
  }

  return values;
}

/**
 * 1) Count elements by category
 */
export function countByCategory<T, K extends string>(
  items: T[],
  selector: CategorySelector<T, K>
): Record<K, number> {
  const result: Record<K, number> = {} as Record<K, number>;

  for (const item of items) {
    const key: K = selector(item);
    result[key] = (result[key] ?? 0) + 1;
  }

  return result;
}

/**
 * 2) Calculate totals (sum)
 */
export function calculateTotal<T>(items: T[], selector: NumericSelector<T>): number {
  const values: number[] = getValidNumbers(items, selector);
  let total: number = 0;

  for (const value of values) {
    total += value;
  }

  return total;
}

/**
 * 3) Calculate averages
 */
export function calculateAverage<T>(items: T[], selector: NumericSelector<T>): number | null {
  const values: number[] = getValidNumbers(items, selector);
  if (values.length === 0) return null;

  let total: number = 0;
  for (const value of values) {
    total += value;
  }

  return total / values.length;
}

/**
 * 4) Find maximum
 */
export function findMaximum<T>(items: T[], selector: NumericSelector<T>): number | null {
  const values: number[] = getValidNumbers(items, selector);
  if (values.length === 0) return null;

  let maxValue: number = values[0];
  for (let index: number = 1; index < values.length; index += 1) {
    if (values[index] > maxValue) maxValue = values[index];
  }

  return maxValue;
}

/**
 * 5) Find minimum
 */
export function findMinimum<T>(items: T[], selector: NumericSelector<T>): number | null {
  const values: number[] = getValidNumbers(items, selector);
  if (values.length === 0) return null;

  let minValue: number = values[0];
  for (let index: number = 1; index < values.length; index += 1) {
    if (values[index] < minValue) minValue = values[index];
  }

  return minValue;
}

export interface NumericReport {
  total: number;
  average: number | null;
  maximum: number | null;
  minimum: number | null;
}

export function buildNumericReport<T>(
  items: T[],
  selector: NumericSelector<T>
): NumericReport {
  return {
    total: calculateTotal(items, selector),
    average: calculateAverage(items, selector),
    maximum: findMaximum(items, selector),
    minimum: findMinimum(items, selector),
  };
}