import { redirect } from "next/navigation";
import { routes } from "@/lib/routes";

/** The workspace is the home page. Service status lives in Settings → Status. */
export default function Home() {
  redirect(routes.newConversation);
}
