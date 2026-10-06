import { createContext, useContext, useSyncExternalStore } from "react";
import type { SessionController, ViewState } from "./controller";

export const ControllerContext = createContext<SessionController | null>(null);

export function useController(): SessionController {
  const c = useContext(ControllerContext);
  if (!c) throw new Error("缺少 ControllerContext");
  return c;
}

export function useView(): ViewState {
  const c = useController();
  return useSyncExternalStore(c.subscribe, c.getSnapshot, c.getSnapshot);
}
