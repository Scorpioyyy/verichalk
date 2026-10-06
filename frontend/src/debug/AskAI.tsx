import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type ChatEvent, type ChatTurn } from "@/shared/api/client";
import { Icon } from "@/shared/ui/Icon";
import { Markdown } from "./markdown";

const SUGGESTIONS = [
  "总结这次运行：做了什么、花了多久、花了多少钱",
  "有没有异常、重试或失败？根因可能是什么",
  "有题目被核验拦下吗？为什么，后来怎么处理的",
  "耗时最长的是哪几步？有什么优化空间",
];

interface Msg {
  role: "user" | "assistant";
  text: string;
  tools: string[];
  error?: string;
  streaming?: boolean;
}

/** 与"这一次运行"对话的分析助手。上下文由后端组织（运行摘要常驻 + 工具按需查询），这里只负责对话界面。 */
export function AskAI({ runId, ready }: { runId: string; ready: boolean }) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => {
    bottom.current?.scrollIntoView?.({ block: "end" });
  }, [msgs]);

  const patchLast = (f: (m: Msg) => Msg) =>
    setMsgs((all) => all.map((m, i) => (i === all.length - 1 ? f(m) : m)));

  const ask = useCallback(
    async (question: string) => {
      const q = question.trim();
      if (!q || busy) return;
      const history: ChatTurn[] = [
        ...msgs.filter((m) => m.text && !m.error).map((m) => ({ role: m.role, content: m.text })),
        { role: "user", content: q },
      ];
      setMsgs((all) => [
        ...all,
        { role: "user", text: q, tools: [] },
        { role: "assistant", text: "", tools: [], streaming: true },
      ]);
      setDraft("");
      setBusy(true);
      const ctl = new AbortController();
      abort.current = ctl;
      const onEvent = (e: ChatEvent) => {
        if (e.type === "delta") patchLast((m) => ({ ...m, text: m.text + e.text }));
        else if (e.type === "tool") patchLast((m) => ({ ...m, tools: [...m.tools, e.label] }));
        else if (e.type === "error") patchLast((m) => ({ ...m, error: e.message }));
      };
      try {
        await api.debug.chat(runId, history, { signal: ctl.signal, onEvent });
      } catch (e) {
        const message = e instanceof ApiError ? e.userMessage : "分析助手没有响应，请稍后再试。";
        patchLast((m) => ({ ...m, error: message }));
      } finally {
        patchLast((m) => ({ ...m, streaming: false }));
        setBusy(false);
        abort.current = null;
      }
    },
    [busy, msgs, runId],
  );

  return (
    <div className="ai" data-testid="ai-panel">
      {msgs.length === 0 ? (
        <div className="ai__empty">
          <h3>
            <Icon name="sparkle" size={18} /> 问问这次运行
          </h3>
          <p className="muted">
            助手已读过这次运行的概况（阶段、耗时、模型调用、题目与核验结果），需要细节时会自己去查对应的提示词、回复和证据。
          </p>
          <div className="ai__chips">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                type="button"
                className="ai__chip"
                disabled={!ready || busy}
                onClick={() => void ask(s)}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="ai__thread" role="log" aria-live="polite">
          {msgs.map((m, i) => (
            <div key={i} className={`ai__msg ai__msg--${m.role}`}>
              {m.role === "user" ? (
                <p>{m.text}</p>
              ) : (
                <>
                  {m.tools.length > 0 && (
                    <ul className="ai__tools" aria-label="查询过程">
                      {m.tools.map((t, j) => (
                        <li key={j}>
                          <Icon name="activity" size={12} /> {t}
                        </li>
                      ))}
                    </ul>
                  )}
                  {m.text ? (
                    <Markdown source={m.text} />
                  ) : (
                    m.streaming &&
                    !m.error && (
                      <p className="muted">
                        <span className="spinner" aria-hidden /> 正在分析……
                      </p>
                    )
                  )}
                  {m.streaming && m.text && <span className="ai__caret" aria-hidden />}
                  {m.error && <p className="notice notice--bad">{m.error}</p>}
                </>
              )}
            </div>
          ))}
          <div ref={bottom} />
        </div>
      )}

      <form
        className="ai__form"
        onSubmit={(e) => {
          e.preventDefault();
          void ask(draft);
        }}
      >
        <textarea
          className="input"
          rows={2}
          value={draft}
          placeholder={ready ? "问点什么，例如：第 2 题为什么被重试？" : "运行加载中……"}
          aria-label="向分析助手提问"
          disabled={!ready}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void ask(draft);
            }
          }}
        />
        <div className="ai__actions">
          {msgs.length > 0 && !busy && (
            <button type="button" className="btn btn--ghost" onClick={() => setMsgs([])}>
              清空对话
            </button>
          )}
          {busy ? (
            <button type="button" className="btn btn--stop" onClick={() => abort.current?.abort()}>
              <Icon name="stop" size={14} /> 停止
            </button>
          ) : (
            <button type="submit" className="btn btn--primary" disabled={!ready || !draft.trim()}>
              <Icon name="send" size={14} /> 发送
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
