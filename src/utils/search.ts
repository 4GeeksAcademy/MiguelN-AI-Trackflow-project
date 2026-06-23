import type { Product, Shipment } from "../types/models";

function normalizeText(value: string): string {
  return value.trim().toLowerCase();
}

export function findProductBySKU(products: Product[], sku: string): Product | null {
  const targetSku: string = normalizeText(sku);

  for (const product of products) {
    if (normalizeText(product.sku) === targetSku) {
      return product;
    }
  }

  return null;
}

export function findShipmentById(shipments: Shipment[], id: string): Shipment | null {
  for (const shipment of shipments) {
    if (shipment.id === id) {
      return shipment;
    }
  }

  return null;
}

export function binarySearchProductByWeight(
  sortedProducts: Product[],
  targetWeight: number
): number {
  let left: number = 0;
  let right: number = sortedProducts.length - 1;

  while (left <= right) {
    const middle: number = Math.floor((left + right) / 2);
    const currentWeight: number = sortedProducts[middle].weightKg;

    if (currentWeight === targetWeight) {
      return middle;
    }

    if (currentWeight < targetWeight) {
      left = middle + 1;
    } else {
      right = middle - 1;
    }
  }

  return -1;
}