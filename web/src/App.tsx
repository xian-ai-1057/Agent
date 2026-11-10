/** 整個網頁：左側欄加上依網址切換的主畫面。 */

import { useEffect } from "react";
import { Conversation } from "./components/Conversation.tsx";
import { NewChat } from "./components/NewChat.tsx";
import { Settings } from "./components/Settings.tsx";
import { Sidebar } from "./components/Sidebar.tsx";
import { WorkView } from "./components/WorkView.tsx";
import { useRoute } from "./router.ts";
import { connect, useStore } from "./store.ts";

export function App() {
  const route = useRoute();
  const connected = useStore((s) => s.connected);
  const loadError = useStore((s) => s.loadError);

  useEffect(() => connect(), []);

  return (
    <div className="app">
      <Sidebar route={route} />
      {route.page === "new" && <NewChat />}
      {route.page === "session" && <Conversation id={route.id} />}
      {route.page === "work" && <WorkView />}
      {route.page === "settings" && <Settings section={route.section} />}
      {(!connected || loadError) && (
        <div
          className="banner"
          role="status"
          style={{ position: "fixed", top: 12, left: 0, right: 0, zIndex: 20 }}
        >
          {loadError ?? "和 harness web 的連線中斷了，正在重新連線…"}
        </div>
      )}
    </div>
  );
}
