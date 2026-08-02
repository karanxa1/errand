/* /c — a new chat. Nothing exists yet: no row, no id, no URL of its own. The
   first turn mints all three (see ChatView.submit). */

import ChatView from "../ChatView";

export default function NewChatPage() {
  return <ChatView initialId={null} initialDetail={null} />;
}
