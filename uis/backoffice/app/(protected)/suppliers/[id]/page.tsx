import SupplierDetailPage from "@/components/suppliers/SupplierDetailPage";

type SupplierDetailRouteProps = {
  params: Promise<{ id: string }>;
};

export default async function SupplierDetailRoutePage({ params }: SupplierDetailRouteProps) {
  const { id } = await params;

  return <SupplierDetailPage supplierId={id} />;
}
