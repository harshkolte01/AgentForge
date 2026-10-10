import { redirect } from "next/navigation";
export default function ConnectionsPage() {
  redirect("/settings?section=github");
}
