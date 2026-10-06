import type { Item } from "@/shared/api/types";

export function makeItem(over: Partial<Item> = {}): Item {
  return {
    id: "itm_TEST000000000000000000",
    kind: "choice",
    stem: "计算：$0.6\\times5=$（　）",
    options: ["30", "3", "0.3", "3.5"],
    answer: "B",
    answer_value: null,
    solution: "$0.6\\times5=3$，所以选 B。",
    kp_ids: [],
    difficulty: 3,
    tier: "consolidate",
    score: 3,
    figures: [],
    provenance: {
      source: "novel",
      archetype_ids: [],
      reference_ids: [],
      run_id: null,
      model: null,
    },
    verification: { status: "verified", checks: [] },
    rev: 1,
    ...over,
  } as Item;
}
