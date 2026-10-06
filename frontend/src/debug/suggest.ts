/** 针对"这一次运行"的推荐提问：只读前端已有的运行模型（事件），不调用模型。
 * 越能直接指出这次运行里的异常，排得越靠前；什么异常都没有时退回到"最慢的阶段"。 */
import { fmtMs } from "./format";
import type { RunModel } from "./model";

const MAX = 3;

const squash = (s: string, n: number) => {
  const t = s.replace(/\s+/g, " ").trim();
  return t.length > n ? `${t.slice(0, n)}…` : t;
};

export function suggestForRun(m: RunModel): string[] {
  const out: string[] = [];
  const add = (q: string) => {
    if (out.length < MAX) out.push(q);
  };

  if (m.status === "failed") add("这次运行为什么失败了？根因在哪一步？");

  // 没有通过核验的题：点出题面开头和没过的那项检查
  for (const it of m.items) {
    const last = it.statuses.at(-1);
    if (!last || (last.status !== "needs_review" && last.status !== "rejected")) continue;
    const name = it.item ? `题目「${squash(it.item.stem, 14)}」` : `第 ${it.order ?? "?"} 题`;
    const failed =
      last.checks.find((c) => c.status === "fail") ?? last.checks.find((c) => c.status === "warn");
    const what = last.status === "rejected" ? "被拒" : "需复核";
    add(
      failed ? `${name}为什么${what}？${failed.name} 检查是怎么没过的？` : `${name}为什么${what}？`,
    );
  }

  // 模型调用出错或重试（序号与"模型调用"页签、分析助手用的清单序号一致，从 1 起）
  m.calls.forEach((c, i) => {
    if (c.record.error)
      add(`第 ${i + 1} 次模型调用（${c.record.purpose || c.record.role}）为什么出错了？`);
    else if (c.record.retries > 0)
      add(
        `第 ${i + 1} 次模型调用（${c.record.purpose || c.record.role}）为什么重试了 ${c.record.retries} 次？`,
      );
  });

  // 兜底：最慢的阶段
  const stages = [...m.spans.values()].filter((s) => s.kind === "stage" && s.durationMs);
  const slow = stages.sort((a, b) => (b.durationMs ?? 0) - (a.durationMs ?? 0))[0];
  const total = (m.t1 - m.t0) * 1000;
  if (slow && slow.durationMs && total > 0) {
    const share = Math.min(100, Math.round((slow.durationMs / total) * 100));
    add(
      `为什么「${slow.name}」阶段最慢（${fmtMs(slow.durationMs)}，占 ${share}%）？有什么优化空间？`,
    );
  }
  return out;
}
