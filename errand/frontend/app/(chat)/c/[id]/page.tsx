/* /c/<id> — one conversation, linkable and shareable, with browser back working
   the way it should.

   Keyed on the id so switching conversations gives a genuinely fresh view rather
   than reusing the previous one's state under new params.

   A server component: it reads the first-party session cookie and fetches a
   best-effort conversation seed so the thread paints before the browser token
   hydrates. A missing/stale/rejected seed is null and the client falls back to
   its canonical Bearer fetch. */

import { cookies } from "next/headers";
import ChatView from "../../ChatView";
import { fetchConversationSeed } from "@/lib/serverConversation";

export default async function ConversationPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const token = (await cookies()).get("errand_session")?.value;
  const initialDetail = await fetchConversationSeed(id, token);
  return <ChatView key={id} initialId={id} initialDetail={initialDetail} />;
}
