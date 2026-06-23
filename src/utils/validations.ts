import type {
  Carrier,
  Country,
  Product,
  Shipment,
  ShipmentPriority,
} from "../types/models";

export interface ValidationOutcome {
  valid: boolean;
  errors: string[];
}

const VALID_COUNTRIES: Country[] = ["United States", "Spain"];
const VALID_PRIORITIES: ShipmentPriority[] = ["Standard", "Express", "Same-day"];

function isNonEmptyString(value: string): boolean {
  return value.trim().length > 0;
}

function isFiniteNumber(value: number): boolean {
  return Number.isFinite(value);
}

function isValidDate(value: Date): boolean {
  return value instanceof Date && !Number.isNaN(value.getTime());
}

function createOutcome(errors: string[]): ValidationOutcome {
  return {
    valid: errors.length === 0,
    errors,
  };
}

export function validateProduct(product: Product): ValidationOutcome {
  const errors: string[] = [];

  if (!isNonEmptyString(product.sku)) {
    errors.push("sku must not be empty");
  }

  if (!isFiniteNumber(product.weightKg) || product.weightKg <= 0 || product.weightKg > 100) {
    errors.push("weightKg must be > 0 and <= 100");
  }

  if (
    !isFiniteNumber(product.dimensions.lengthCm) ||
    product.dimensions.lengthCm <= 0 ||
    product.dimensions.lengthCm > 200
  ) {
    errors.push("dimensions.lengthCm must be > 0 and <= 200");
  }

  if (
    !isFiniteNumber(product.dimensions.widthCm) ||
    product.dimensions.widthCm <= 0 ||
    product.dimensions.widthCm > 200
  ) {
    errors.push("dimensions.widthCm must be > 0 and <= 200");
  }

  if (
    !isFiniteNumber(product.dimensions.heightCm) ||
    product.dimensions.heightCm <= 0 ||
    product.dimensions.heightCm > 200
  ) {
    errors.push("dimensions.heightCm must be > 0 and <= 200");
  }

  if (!isFiniteNumber(product.stockQuantity) || product.stockQuantity < 0) {
    errors.push("stockQuantity must be >= 0");
  }

  if (!isFiniteNumber(product.minStockThreshold) || product.minStockThreshold < 0) {
    errors.push("minStockThreshold must be >= 0");
  }

  if (!isFiniteNumber(product.unitCostUSD) || product.unitCostUSD <= 0) {
    errors.push("unitCostUSD must be > 0");
  }

  return createOutcome(errors);
}

export function validateShipment(shipment: Shipment): ValidationOutcome {
  const errors: string[] = [];

  if (!isFiniteNumber(shipment.quantity) || shipment.quantity <= 0) {
    errors.push("quantity must be > 0");
  }

  if (!isFiniteNumber(shipment.declaredValueUSD) || shipment.declaredValueUSD <= 0) {
    errors.push("declaredValueUSD must be > 0");
  }

  if (
    !isFiniteNumber(shipment.destination.distanceKm) ||
    shipment.destination.distanceKm < 0
  ) {
    errors.push("destination.distanceKm must be >= 0");
  }

  if (!isValidDate(shipment.createdAt)) {
    errors.push("createdAt must be a valid date");
  }

  if (!VALID_COUNTRIES.includes(shipment.destination.country)) {
    errors.push("destination.country is invalid");
  }

  if (!VALID_PRIORITIES.includes(shipment.priority)) {
    errors.push("priority is invalid");
  }

  return createOutcome(errors);
}

export function validateCarrier(carrier: Carrier): ValidationOutcome {
  const errors: string[] = [];

  if (!isFiniteNumber(carrier.baseRateUSD) || carrier.baseRateUSD < 0) {
    errors.push("baseRateUSD must be >= 0");
  }

  if (!isFiniteNumber(carrier.ratePerKgUSD) || carrier.ratePerKgUSD < 0) {
    errors.push("ratePerKgUSD must be >= 0");
  }

  if (!isFiniteNumber(carrier.ratePerKmUSD) || carrier.ratePerKmUSD < 0) {
    errors.push("ratePerKmUSD must be >= 0");
  }

  if (!isFiniteNumber(carrier.avgDeliveryDays) || carrier.avgDeliveryDays <= 0) {
    errors.push("avgDeliveryDays must be > 0");
  }

  if (!isFiniteNumber(carrier.onTimeRate) || carrier.onTimeRate < 0 || carrier.onTimeRate > 100) {
    errors.push("onTimeRate must be between 0 and 100");
  }

  if (!isFiniteNumber(carrier.maxWeightKg) || carrier.maxWeightKg <= 0) {
    errors.push("maxWeightKg must be > 0");
  }

  if (!Array.isArray(carrier.operatesIn) || carrier.operatesIn.length === 0) {
    errors.push("operatesIn must contain at least 1 country");
  }

  return createOutcome(errors);
}