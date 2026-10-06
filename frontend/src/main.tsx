import { lazy, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import "@/shared/ui/base.css";
import "@/shared/ui/components.css";
import { UserApp } from "@/user/UserApp";

// 调试台按需加载：普通用户不会下载它的代码（含图布局库）
const DebugApp = lazy(() => import("@/debug/DebugApp"));

createRoot(document.getElementById("root")!).render(
  <BrowserRouter>
    <Routes>
      <Route
        path="/debug/*"
        element={
          <Suspense fallback={<div style={{ padding: 24 }}>加载调试台……</div>}>
            <DebugApp />
          </Suspense>
        }
      />
      <Route path="*" element={<UserApp />} />
    </Routes>
  </BrowserRouter>,
);
