import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/shared/api/client";
import type { ExportOptions } from "@/shared/api/types";
import { Icon } from "@/shared/ui/Icon";
import { userMessageOf } from "../controller";
import { loadExportOptions } from "./ExportDialog";

const PdfPages = lazy(() => import("./PdfPages").then((m) => ({ default: m.PdfPages })));

interface Props {
  sessionId: string;
  rev: number;
  teacher: boolean;
}

/** 试卷视图：显示的就是导出的那份 PDF（预览即导出，D42），不是另一套近似排版。 */
export function PaperPreview({ sessionId, rev, teacher }: Props) {
  const [data, setData] = useState<ArrayBuffer | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const seq = useRef(0);

  useEffect(() => {
    const my = ++seq.current;
    setState("loading");
    const saved = loadExportOptions();
    const opts: ExportOptions = {
      ...saved,
      format: "pdf",
      version: teacher ? "teacher" : "student",
      answers: "inline",
      include_solution: true,
    };
    // 编辑过程中版本变化很快：稍等一下再生成，避免连续请求
    const timer = setTimeout(() => {
      api
        .exportPaper(sessionId, opts, true)
        .then(async (f) => {
          const buf = await f.blob.arrayBuffer();
          if (my !== seq.current) return;
          setData(buf);
          setWarnings(f.warnings);
        })
        .catch((e) => {
          if (my !== seq.current) return;
          setError(userMessageOf(e));
          setState("error");
        });
    }, 250);
    return () => clearTimeout(timer);
  }, [sessionId, rev, teacher]);

  const onRender = useCallback((s: "ready" | "error") => {
    setState(s);
    if (s === "error") setError("预览画面没能显示");
  }, []);

  return (
    <div className="preview" data-testid="paper-preview" data-state={state}>
      {state === "loading" && (
        <div className="preview__loading" role="status">
          <span className="spinner" aria-hidden /> 正在生成试卷预览……
        </div>
      )}
      {state === "error" && (
        <div className="preview__state">
          <Icon name="alert" size={18} />
          <span>预览暂时生成不了：{error}</span>
          <span className="muted">不影响导出，也可以回到“题目”视图继续编辑。</span>
        </div>
      )}
      {data && state !== "error" && (
        <Suspense fallback={null}>
          <PdfPages data={data} onState={onRender} />
        </Suspense>
      )}
      {warnings.length > 0 && (
        <p className="notice notice--warn preview__warn">
          <Icon name="alert" size={16} />
          <span>{warnings.join("；")}</span>
        </p>
      )}
    </div>
  );
}
