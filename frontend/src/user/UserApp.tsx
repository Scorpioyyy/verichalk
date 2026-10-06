import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/shared/api/client";
import { Icon } from "@/shared/ui/Icon";
import { ChatPane } from "./chat/ChatPane";
import type { ComposerHandle } from "./chat/Composer";
import { acceptPhotos } from "./chat/photos";
import { SessionController } from "./controller";
import { HistoryMenu } from "./HistoryMenu";
import { LogoMark } from "./Logo";
import { Workspace } from "./paper/Workspace";
import { ControllerContext, useView } from "./useController";
import { Welcome } from "./Welcome";
import "./user.css";
import "./chat/photos.css";

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
  const [photos, setPhotos] = useState<File[]>([]);
  const [tab, setTab] = useState<"chat" | "paper">("chat");
  const [menuOpen, setMenuOpen] = useState(false);
  const composerRef = useRef<ComposerHandle>(null);

  const hasContent =
    view.messages.length > 0 || view.pendingUser !== null || !!view.run || !!view.paper;

  function addPhotos(files: File[]) {
    const r = acceptPhotos(photos, files);
    setPhotos(r.files);
    if (r.error) ctl.toast(r.error, "error");
  }

  async function send(text?: string) {
    const t = (text ?? draft).trim();
    const withPhotos = text === undefined ? photos : [];
    if (!t && withPhotos.length === 0) return;
    if (text === undefined) {
      setDraft("");
      setPhotos([]);
    }
    setTab("chat");
    const ok = await ctl.send(t, withPhotos);
    if (!ok && text === undefined) {
      setDraft(t);
      setPhotos(withPhotos);
    }
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
          setPhotos([]);
          void ctl.newSession();
        }}
        onOpen={(id) => void ctl.openSession(id)}
        onRename={(id, title) => void ctl.renameSession(id, title)}
        onDelete={(id) => void ctl.deleteSession(id)}
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
          photos={photos}
          onAddPhotos={addPhotos}
          onRemovePhoto={(i) => setPhotos(photos.filter((_, j) => j !== i))}
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
              photos={photos}
              onAddPhotos={addPhotos}
              onRemovePhoto={(i) => setPhotos(photos.filter((_, j) => j !== i))}
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
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}

function TopBar({
  title,
  recent,
  currentId,
  menuOpen,
  setMenuOpen,
  onNew,
  onOpen,
  onRename,
  onDelete,
}: TopBarProps) {
  const root = useRef<HTMLDivElement>(null);
  const renaming = useRef(false); // 正在改标题时，点空白处只是"改完了"（让教师看到结果），不关菜单
  useEffect(() => {
    if (!menuOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (root.current?.contains(e.target as Node)) return;
      if (renaming.current) {
        if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); // 失焦即提交
        return;
      }
      setMenuOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !renaming.current) setMenuOpen(false); // 改名时 Esc 只取消改名
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen, setMenuOpen]);

  return (
    <header className="topbar">
      <a
        className="brand"
        href="/"
        aria-label="VeriChalk 首页"
        onClick={(e) => {
          e.preventDefault();
          onNew();
        }}
      >
        <LogoMark />
        <span className="brand__name">VeriChalk</span>
        <span className="brand__sub">小学数学命题助手</span>
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
            <Icon name="history" size={16} /> <span className="hide-sm">历史对话</span>
          </button>
          {menuOpen && (
            <div className="menu" role="menu" aria-label="历史对话">
              <HistoryMenu
                recent={recent}
                currentId={currentId}
                onOpen={(id) => {
                  setMenuOpen(false);
                  onOpen(id);
                }}
                onRename={onRename}
                onEditingChange={(on) => {
                  renaming.current = on;
                }}
                onDelete={onDelete}
              />
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
