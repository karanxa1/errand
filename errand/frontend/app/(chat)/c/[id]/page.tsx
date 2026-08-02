/* /c/<id> — one conversation, linkable and shareable, with browser back working
   the way it should.

   Keyed on the id so switching conversations gives a genuinely fresh view rather
   than reusing the previous one's state under new params. */

import { use } from "react";
import ChatView from "../../ChatView";

export default function ConversationPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  return <ChatView key={id} initialId={id} />;
}
