"use client";
import type { ReactNode } from "react";
import { DeliveryPanel } from "@/features/delivery/delivery-panel";
import { useDeliveryContext } from "@/features/delivery/delivery-context";

/**
 * The conversation column and, beside it, the delivery panel. On large screens the thread and composer simply get narrower; on
 * small ones the panel is a sheet over the page, and the page underneath is inert while it is open.
 */
export function DeliverySplit({ children }: { children: ReactNode }) {
  const delivery = useDeliveryContext();
  return <>
    <div inert={delivery?.sheet} className="relative flex min-w-0 flex-1 flex-col">{children}</div>
    <DeliveryPanel />
  </>;
}
