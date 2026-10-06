import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/shared/api/client";
import { Icon } from "@/shared/ui/Icon";
import { ChatPane } from "./chat/ChatPane";
import type { ComposerHandle } from "./chat/Composer";
import { SessionController } from "./controller";
import { LogoMark } from "./Logo";
import { Workspace } from "./paper/Workspace";
import { ControllerContext, useView } from "./useController";
import { Welcome } from "./Welcome";
import "./user.css";

export function UserApp() {
  const ctl = useMemo(() => new SessionController(), []);
  useEffect(() => {
    void ctl.init();
    void api.warmup().catch(() => undefined); // 页面打开即预热模型连接（D28）
    return () => ctl.dispose();
  }, [ctl]);
  return (
    <ControllerContext.Provider value={ctl}>
      <Shell ctl={ctl} />
    </ControllerContext.Provider>
  );
}

function Shell({ ctl }: { ctl: SessionController }) {
  const view = useView();
  const [draft, setDraft] = useState("");
  const [tab, setTab] = useState<"chat" | "paper">("chat");
  const [menuOpen, setMenuOpen] = useState(false);
  const composerRef = useRef<ComposerHandle>(null);

  const hasContent = view.messages.length > 0 || !!view.pendingUser || !!view.run || !!view.paper;

  async function send(text?: string) {
    const t = (text ?? draft).trim();
    if (!t) return;
    if (text === undefined) setDraft("");
    setTab("chat");
    const ok = await ctl.send(t);
    if (!ok && text === undefined) setDraft(t);
  }

  function askItem(no: number) {
    setDraft(`第 ${no} 题`);
    setTab("chat");
    requestAnimationFrame(() => composerRef.current?.focus());
  }

  function prefill(text: string) {
    setDraft(text);
    setTab("chat");
    requestAnimationFrame(() => composerRef.current?.focus());
  }

  return (
    <div className="app">
      <TopBar
        title={view.recent.find((r) => r.id === view.sessionId)?.title}
        recent={view.recent}
        currentId={view.sessionId}
        menuOpen={menuOpen}
        setMenuOpen={setMenuOpen}
        onNew={() => {
          setDraft("");
          void ctl.newSession();
        }}
        onOpen={(id) => void ctl.openSession(id)}
      />
      {!view.ready ? (
        <div className="boot" role="status">
          <span className="spinner" aria-hidden /> 正在打开……
        </div>
      ) : !hasContent ? (
        <Welcome
          draft={draft}
          setDraft={setDraft}
          onSend={() => void send()}
          composerRef={composerRef}
        />
      ) : (
        <>
          <main className={`split split--${tab}`}>
            <ChatPane
              view={view}
              ctl={ctl}
              draft={draft}
              setDraft={setDraft}
              composerRef={composerRef}
              onSend={() => void send()}
            />
            <Workspace
              view={view}
              ctl={ctl}
              onAskItem={askItem}
              onApply={(t) => void send(t)}
              onPrefill={prefill}
            />
          </main>
          <nav className="tabs" aria-label="切换视图">
            <button type="button" aria-pressed={tab === "chat"} onClick={() => setTab("chat")}>
              <Icon name="chat" size={18} /> 对话
            </button>
            <button type="button" aria-pressed={tab === "paper"} onClick={() => setTab("paper")}>
              <Icon name="file" size={18} /> 试卷
              {view.paper && (
                <span className="tabs__count">
                  {view.paper.sections.reduce((n, s) => n + s.items.length, 0)}
                </span>
              )}
            </button>
          </nav>
        </>
      )}
      <Toasts view={view} ctl={ctl} />
    </div>
  );
}

interface TopBarProps {
  title?: string;
  recent: { id: string; title: string; ts: number }[];
  currentId: string | null;
  menuOpen: boolean;
  setMenuOpen: (b: boolean) => void;
  onNew: () => void;
  onOpen: (id: string) => void;
}

function TopBar({ title, recent, currentId, menuOpen, setMenuOpen, onNew, onOpen }: TopBarProps) {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menuOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMenuOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen, setMenuOpen]);

  return (
    <header className="topbar">
      <a className="brand" href="/" aria-label="VeriChalk 首页">
        <LogoMark />
        <span className="brand__name">VeriChalk</span>
        <span className="brand__sub">命题助手</span>
      </a>
      {title && <span className="topbar__title">{title}</span>}
      <div className="topbar__right" ref={root}>
        <button type="button" className="btn btn--ghost" onClick={onNew}>
          <Icon name="plus" size={16} /> <span className="hide-sm">新对话</span>
        </button>
        <div className="menu-wrap">
          <button
            type="button"
            className="btn btn--ghost"
            aria-haspopup="true"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen(!menuOpen)}
          >
            <Icon name="history" size={16} /> <span className="hide-sm">历史</span>
          </button>
          {menuOpen && (
            <div className="menu" role="menu" aria-label="历史对话">
              {recent.length === 0 && <p className="menu__empty muted">还没有历史对话</p>}
              {recent.map((r) => (
                <button
                  key={r.id}
                  type="button"
                  role="menuitem"
                  className={`menu__item ${r.id === currentId ? "menu__item--on" : ""}`}
                  onClick={() => {
                    setMenuOpen(false);
                    onOpen(r.id);
                  }}
                >
                  <span className="menu__title">{r.title || "新对话"}</span>
                  <time className="muted">
                    {new Date(r.ts).toLocaleDateString("zh-CN", {
                      month: "numeric",
                      day: "numeric",
                    })}
                  </time>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </header>
  );
}

function Toasts({ view, ctl }: { view: ReturnType<typeof useView>; ctl: SessionController }) {
  return (
    <div className="toasts" aria-live="polite" role="status">
      {view.toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind === "error" ? "toast--error" : ""}`}>
          <span>{t.text}</span>
          {t.action && (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => {
                t.action!.run();
                ctl.dismissToast(t.id);
              }}
            >
              {t.action.label}
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
