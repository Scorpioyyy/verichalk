import { useEffect, useRef, useState } from "react";
import type { PDFDocumentLoadingTask, PDFDocumentProxy } from "pdfjs-dist";

interface Props {
  data: ArrayBuffer;
  /** 渲染完成（或失败）后回调，界面据此撤掉"正在生成"状态 */
  onState?: (s: "ready" | "error") => void;
}

/**
 * 把 PDF 逐页画成画布，宽度跟随容器。不用浏览器内置的 PDF 查看器：
 * 手机浏览器通常不在页面里显示 PDF，各浏览器的缩放行为也不一致；自己画才能保证"看到的就是导出的那份"。
 * pdf.js 体积不小，所以只在打开"试卷"视图时才加载。
 */
export function PdfPages({ data, onState }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [pages, setPages] = useState(0);
  const docRef = useRef<PDFDocumentProxy | null>(null);
  const taskRef = useRef<PDFDocumentLoadingTask | null>(null);

  // 容器宽度
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(Math.floor(el.clientWidth)));
    ro.observe(el);
    setWidth(Math.floor(el.clientWidth));
    return () => ro.disconnect();
  }, []);

  // 载入文档
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
        const worker = (await import("pdfjs-dist/legacy/build/pdf.worker.min.mjs?url")).default;
        pdfjs.GlobalWorkerOptions.workerSrc = worker;
        const task = pdfjs.getDocument({ data: data.slice(0) });
        const doc = await task.promise;
        if (!live) {
          void task.destroy();
          return;
        }
        void taskRef.current?.destroy();
        taskRef.current = task;
        docRef.current = doc;
        setPages(doc.numPages);
      } catch (e) {
        console.warn("试卷预览：PDF 载入失败", e);
        if (live) onState?.("error");
      }
    })();
    return () => {
      live = false;
    };
  }, [data, onState]);

  // 画页（宽度变化时按新宽度重画）
  useEffect(() => {
    const doc = docRef.current;
    const el = box.current;
    if (!doc || !el || width < 50 || pages === 0) return;
    let live = true;
    (async () => {
      try {
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const canvases = Array.from(el.querySelectorAll("canvas"));
        for (let i = 1; i <= doc.numPages; i++) {
          const page = await doc.getPage(i);
          const base = page.getViewport({ scale: 1 });
          const scale = width / base.width;
          const vp = page.getViewport({ scale: scale * dpr });
          const canvas = canvases[i - 1];
          if (!canvas || !live) return;
          canvas.width = Math.floor(vp.width);
          canvas.height = Math.floor(vp.height);
          canvas.style.width = `${width}px`;
          canvas.style.height = `${Math.floor(base.height * scale)}px`;
          const ctx = canvas.getContext("2d");
          if (!ctx) continue;
          await page.render({ canvasContext: ctx, viewport: vp, canvas }).promise;
        }
        if (live) onState?.("ready");
      } catch (e) {
        console.warn("试卷预览：PDF 绘制失败", e);
        if (live) onState?.("error");
      }
    })();
    return () => {
      live = false;
    };
  }, [width, pages, onState]);

  useEffect(
    () => () => {
      void taskRef.current?.destroy();
      taskRef.current = null;
      docRef.current = null;
    },
    [],
  );

  return (
    <div ref={box} className="pdf" data-testid="pdf-pages">
      {Array.from({ length: pages }, (_, i) => (
        <canvas key={i} className="pdf__page" aria-label={`第 ${i + 1} 页`} role="img" />
      ))}
    </div>
  );
}
