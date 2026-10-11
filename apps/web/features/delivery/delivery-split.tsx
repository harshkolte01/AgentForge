"use client";
import type { ReactNode } from "react";
import dynamic from "next/dynamic";
import { useDeliveryContext } from "@/features/delivery/delivery-context";

// Most conversations never open the panel: its code (diffs, checks, the pull request view) loads the first time it is opened.
// Nothing of it is mounted while closed anyway, and it animates in on mount, so loading on open loses no motion.
const DeliveryPanel = dynamic(() => import("@/features/delivery/delivery-panel").then((mod) => mod.DeliveryPanel), { ssr: false });

/**
 * The conversation column and, beside it, the delivery panel. On large screens the thread and composer simply get narrower; on
 * small ones the panel is a sheet over the page, and the page underneath is inert while it is open.
 */
export function DeliverySplit({ children }: { children: ReactNode }) {
  const delivery = useDeliveryContext();
  return <>
    <div inert={delivery?.sheet} className="relative flex min-w-0 flex-1 flex-col">{children}</div>
    {delivery?.panelOpen ? <DeliveryPanel /> : null}
  </>;
}
