import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { PerceivedItem, ReferenceSet } from "@/shared/api/types";
import { PerceptionCard, PerceptionConfirm } from "./PerceptionCards";

const it_ = (id: string, text: string, over: Partial<PerceivedItem> = {}): PerceivedItem => ({
  id,
  attachment_id: "att_1",
  no: "1",
  instruction: "化简各数。",
  text,
  kind: "calc",
  has_figure: false,
  figure_desc: "",
  topic: "小数的性质",
  difficulty: 1,
  confidence: 0.95,
  uncertain: "",
  kp_ids: [],
  kp_names: [],
  ...over,
});

function refs(items: PerceivedItem[], over: Partial<ReferenceSet> = {}): ReferenceSet {
  return {
    pages: [
      {
        attachment_id: "att_1",
        filename: "a.jpg",
        verdict: "worksheet",
        reason: "",
        title: "小数的性质",
        items,
        quality: null,
        kp_ids: [],
      },
    ],
    context: {
      grade: 4,
      semester: "b",
      lesson_id: null,
      kp_ids: [],
      kp_names: [],
      difficulty: [1, 2],
      kinds: ["calc"],
      summary: "四年级下册 · 小数的性质",
    },
    usable: true,
    message: "",
    needs_confirm: false,
    ...over,
  } as ReferenceSet;
}

const six = Array.from({ length: 6 }, (_, i) => it_(`r1.${i + 1}`, `0.${i + 1}0=`));

describe("识别卡片", () => {
  it("显示题数与学段，默认只列前 4 道，可展开", async () => {
    render(<PerceptionCard refs={refs(six)} />);
    expect(screen.getByText("读到了", { exact: false }).textContent).toContain("6");
    expect(screen.getByText("四年级下册 · 小数的性质")).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
    await userEvent.click(screen.getByRole("button", { name: /展开全部 6 道/ }));
    expect(screen.getAllByRole("listitem")).toHaveLength(6);
  });

  it("看不清的题标出原因；有几道需核对", () => {
    const items = [it_("r1.1", "0.6050=", { uncertain: "像 0.6050 也像 0.6060" })];
    render(<PerceptionCard refs={refs(items)} />);
    expect(screen.getByText("1 道需核对")).toBeTruthy();
    expect(screen.getByText("像 0.6050 也像 0.6060")).toBeTruthy();
  });

  it("不可用的照片不显示卡片；画质差才提示拍摄建议", () => {
    const { container } = render(<PerceptionCard refs={refs([], { usable: false })} />);
    expect(container.firstChild).toBeNull();
    const r = refs(six);
    r.pages[0]!.quality = {
      width: 1,
      height: 1,
      orig_width: 1,
      orig_height: 1,
      sharpness: 1,
      brightness: 1,
      contrast: 1,
      hints: ["照片有些模糊，建议稳住手机、对好焦再拍一张"],
      poor: true,
    };
    render(<PerceptionCard refs={r} />);
    expect(screen.getByText(/照片有些模糊/)).toBeTruthy();
  });
});

describe("识别结果核对（检查点）", () => {
  const flagged = [
    it_("r1.1", "0.70="),
    it_("r1.2", "0.6050=", { uncertain: "像 0.6050 也像 0.6060" }),
    it_("r1.3", "3.800="),
  ];

  it("有看不清的题时只先展示它们；没改动就直接确认", async () => {
    const onAnswer = vi.fn();
    render(<PerceptionConfirm prompt="请核对" refs={refs(flagged)} onAnswer={onAnswer} />);
    expect(screen.getAllByRole("textbox")).toHaveLength(1);
    expect(screen.getByText("像 0.6050 也像 0.6060")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "没问题，开始出题" }));
    expect(onAnswer).toHaveBeenCalledWith({ option: "confirm", items: [] });
  });

  it("改字与去掉题只提交有变化的；展开全部后可改其他题", async () => {
    const onAnswer = vi.fn();
    render(<PerceptionConfirm prompt="请核对" refs={refs(flagged)} onAnswer={onAnswer} />);
    const box = screen.getByRole("textbox");
    await userEvent.clear(box);
    await userEvent.type(box, "0.6060=");
    await userEvent.click(screen.getByRole("button", { name: /查看并修改全部 3 道/ }));
    expect(screen.getAllByRole("textbox")).toHaveLength(3);
    const rows = screen.getAllByRole("listitem");
    await userEvent.click(within(rows[2]!).getByRole("button", { name: "去掉这道题" }));
    await userEvent.click(screen.getByRole("button", { name: "按修改后的开始出题" }));
    expect(onAnswer).toHaveBeenCalledWith({
      option: "confirm",
      items: [
        { id: "r1.2", text: "0.6060=" },
        { id: "r1.3", remove: true },
      ],
    });
  });

  it("全部去掉后不能确认", async () => {
    const onAnswer = vi.fn();
    const one = refs([it_("r1.1", "0.70=", { uncertain: "糊" })]);
    render(<PerceptionConfirm prompt="请核对" refs={one} onAnswer={onAnswer} />);
    await userEvent.click(screen.getByRole("button", { name: "去掉这道题" }));
    const go = screen.getByRole("button", { name: /开始出题/ }) as HTMLButtonElement;
    expect(go.disabled).toBe(true);
    expect(screen.getByText("至少保留一道题")).toBeTruthy();
  });
});
