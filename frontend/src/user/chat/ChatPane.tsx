import { useEffect, useRef } from "react";
import type { RefObject } from "react";
import { isActive } from "@/shared/events/reducer";
import { Icon } from "@/shared/ui/Icon";
import type { SessionController } from "../controller";
import type { ViewState } from "../controller";
import { LogoMark } from "../Logo";
import { ChatText } from "./ChatText";
import { CheckpointCard } from "./CheckpointCard";
import { Composer } from "./Composer";
import type { ComposerHandle } from "./Composer";
import { RunProgress } from "./RunProgress";

interface Props {
  view: ViewState;
  ctl: SessionController;
  draft: string;
  setDraft: (v: string) => void;
  composerRef: RefObject<ComposerHandle | null>;
  onSend: () => void;
}

const FOLLOW_UPS = ["再来 3 道同类题", "整体简单一点", "第 1 题换个场景"];

export function ChatPane({ view, ctl, draft, setDraft, composerRef, onSend }: Props) {
  const { messages, pendingUser, run, paper } = view;
  const endRef = useRef<HTMLDivElement>(null);
  const active = isActive(run);

  const liveReply =
    run?.reply && !messages.some((m) => m.role === "assistant" && m.run_id === run.runId)
      ? run.reply
      : null;

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [
    messages.length,
    pendingUser,
    run?.steps.length,
    run?.phase,
    run?.checkpoint?.id,
    liveReply?.text,
  ]);

  const lastUser =
    [...messages].reverse().find((m) => m.role === "user")?.content ?? pendingUser ?? "";

  return (
    <section className="chat" aria-label="对话">
      <div className="chat__scroll">
        <div className="chat__list" aria-live="off">
          {messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="msg msg--user">
                <div className="msg__bubble">{m.content}</div>
              </div>
            ) : (
              <div key={m.id} className="msg msg--bot">
                <span className="msg__avatar">
                  <LogoMark size={24} />
                </span>
                <ChatText text={m.content} />
              </div>
            ),
          )}
          {pendingUser &&
            !messages.some((m) => m.role === "user" && m.run_id && m.run_id === run?.runId) && (
              <div className="msg msg--user">
                <div className="msg__bubble">{pendingUser}</div>
              </div>
            )}
          {active && run && (
            <div className="msg msg--bot">
              <span className="msg__avatar">
                <LogoMark size={24} />
              </span>
              <div className="msg__body">
                {run.reply && !run.reply.done && <ChatText text={run.reply.text} />}
                {run.phase === "awaiting_user" && run.checkpoint ? (
                  <CheckpointCard
                    checkpoint={run.checkpoint}
                    onAnswer={(a) => void ctl.answer(a)}
                  />
                ) : (
                  <RunProgress run={run} onStop={() => void ctl.cancel()} />
                )}
              </div>
            </div>
          )}
          {!active && liveReply && (
            <div className="msg msg--bot">
              <span className="msg__avatar">
                <LogoMark size={24} />
              </span>
              <ChatText text={liveReply.text} />
            </div>
          )}
          {run?.phase === "failed" && (
            <div className="msg msg--bot">
              <span className="msg__avatar">
                <LogoMark size={24} />
              </span>
              <div className="msg__body">
                <div className="notice notice--bad" role="alert">
                  <Icon name="alert" size={16} />
                  <span>{run.error?.user_message || "这次没有完成。"}</span>
                  <button
                    type="button"
                    className="btn btn--sm"
                    onClick={() => lastUser && void onRetry(ctl, lastUser)}
                  >
                    重试
                  </button>
                </div>
              </div>
            </div>
          )}
          {run?.phase === "cancelled" && (
            <div className="msg msg--bot">
              <span className="msg__avatar">
                <LogoMark size={24} />
              </span>
              <p className="muted">已停止。想继续的话，直接告诉我就行。</p>
            </div>
          )}
          <div ref={endRef} />
        </div>
      </div>
      <div className="chat__foot">
        <Composer
          ref={composerRef}
          value={draft}
          onChange={setDraft}
          onSend={onSend}
          onStop={() => void ctl.cancel()}
          busy={active}
          suggestions={paper ? FOLLOW_UPS : undefined}
          onSuggest={(s) => {
            setDraft(s);
            composerRef.current?.focus();
          }}
        />
      </div>
    </section>
  );
}

async function onRetry(ctl: SessionController, text: string): Promise<void> {
  await ctl.send(text);
}
