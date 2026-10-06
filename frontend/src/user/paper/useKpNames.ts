import { useEffect, useState } from "react";
import { api } from "@/shared/api/client";
import type { KPRef } from "@/shared/api/types";
import { kpLabel } from "@/shared/labels";

const cache = new Map<string, KPRef | null>();
const inflight = new Set<string>();
const listeners = new Set<() => void>();

async function load(ids: string[]): Promise<void> {
  const missing = ids.filter((i) => !cache.has(i) && !inflight.has(i));
  if (!missing.length) return;
  missing.forEach((i) => inflight.add(i));
  try {
    const refs = await api.kpRefs(missing);
    for (const r of refs) cache.set(r.id, r);
    for (const i of missing) if (!cache.has(i)) cache.set(i, null);
  } catch {
    /* 名称只是辅助信息：取不到就不显示 */
  } finally {
    missing.forEach((i) => inflight.delete(i));
    listeners.forEach((fn) => fn());
  }
}

/** 知识点 ID → 教师可读名称（"小数加减法（四下）"）。拿不到的 ID 不显示，绝不把 ID 本身展示出来。 */
export function useKpNames(ids: string[]): string[] {
  const [, force] = useState(0);
  const key = ids.join("|");
  useEffect(() => {
    const fn = () => force((n) => n + 1);
    listeners.add(fn);
    void load(ids);
    return () => {
      listeners.delete(fn);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return ids
    .map((i) => cache.get(i))
    .filter((r): r is KPRef => !!r)
    .map(kpLabel);
}
