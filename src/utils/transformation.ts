import type {
  Carrier,
  Product,
  ProductCategory,
  Shipment,
  ShipmentStatus,
} from "../types/models";

function roundToTwo(value: number): number {
  return Math.round(value * 100) / 100;
}

function getPriorityMultiplier(priority: Shipment["priority"]): number {
  if (priority === "Express") return 1.3;
  if (priority === "Same-day") return 1.6;
  return 1;
}

export function calculateShippingCost(
  shipment: Shipment,
  product: Product,
  carrier: Carrier
): number {
  const baseRate: number = carrier.baseRateUSD;
  const weightCost: number = product.weightKg * carrier.ratePerKgUSD * shipment.quantity;
  const distanceCost: number = shipment.destination.distanceKm * carrier.ratePerKmUSD;
  const subtotal: number = baseRate + weightCost + distanceCost;
  const totalCost: number = subtotal * getPriorityMultiplier(shipment.priority);

  return roundToTwo(totalCost);
}

export function scoreCarrierForShipment(
  carrier: Carrier,
  shipment: Shipment,
  product: Product
): number {
  let score: number = 0;

  if (carrier.operatesIn.includes(shipment.destination.country)) {
    score += 20;
  }

  const shipmentWeight: number = product.weightKg * shipment.quantity;
  if (shipmentWeight <= carrier.maxWeightKg) {
    score += 20;
  }

  if (carrier.acceptsPriority.includes(shipment.priority)) {
    score += 15;
  }

  if (!product.isFragile || carrier.handlesFragile) {
    score += 15;
  }

  score += carrier.onTimeRate * 0.3;

  return roundToTwo(score);
}

export interface CarrierSelectionResult {
  carrier: Carrier;
  score: number;
  cost: number;
}

export function selectBestCarrier(
  carriers: Carrier[],
  shipment: Shipment,
  product: Product
): CarrierSelectionResult | null {
  let bestOption: CarrierSelectionResult | null = null;

  for (const carrier of carriers) {
    const score: number = scoreCarrierForShipment(carrier, shipment, product);
    if (score < 50) {
      continue;
    }

    const cost: number = calculateShippingCost(shipment, product, carrier);

    if (bestOption === null || cost < bestOption.cost) {
      bestOption = { carrier, score, cost };
    }
  }

  return bestOption;
}

export function countProductsByCategory(
  products: Product[]
): Record<ProductCategory, number> {
  const counts: Record<ProductCategory, number> = {
    Fashion: 0,
    Electronics: 0,
    Cosmetics: 0,
    Home: 0,
    Other: 0,
  };

  for (const product of products) {
    counts[product.category] += 1;
  }

  return counts;
}

export function calculateTotalInventoryValue(products: Product[]): number {
  let total: number = 0;

  for (const product of products) {
    total += product.stockQuantity * product.unitCostUSD;
  }

  return roundToTwo(total);
}

export function calculateAverageShipmentDistance(shipments: Shipment[]): number {
  if (shipments.length === 0) {
    return 0;
  }

  let totalDistance: number = 0;
  for (const shipment of shipments) {
    totalDistance += shipment.destination.distanceKm;
  }

  return roundToTwo(totalDistance / shipments.length);
}

export function groupShipmentsByStatus(
  shipments: Shipment[]
): Record<ShipmentStatus, Shipment[]> {
  const grouped: Record<ShipmentStatus, Shipment[]> = {
    Pending: [],
    Assigned: [],
    "In transit": [],
    Delivered: [],
    Failed: [],
  };

  for (const shipment of shipments) {
    grouped[shipment.status].push(shipment);
  }

  return grouped;
}

export function findTopCarriers(
  shipments: Shipment[],
  topN: number
): Array<{ carrier: string; count: number }> {
  if (topN <= 0) {
    return [];
  }

  const usageCount: Record<string, number> = {};

  for (const shipment of shipments) {
    if (shipment.carrier === null) {
      continue;
    }

    usageCount[shipment.carrier] = (usageCount[shipment.carrier] ?? 0) + 1;
  }

  const ranking: Array<{ carrier: string; count: number }> = [];
  for (const carrierName in usageCount) {
    if (Object.prototype.hasOwnProperty.call(usageCount, carrierName)) {
      ranking.push({ carrier: carrierName, count: usageCount[carrierName] });
    }
  }

  ranking.sort((left, right): number => right.count - left.count);
  return ranking.slice(0, topN);
}