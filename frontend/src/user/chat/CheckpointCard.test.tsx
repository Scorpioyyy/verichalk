import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { CheckpointInfo } from "@/shared/events/reducer";
import { CheckpointCard } from "./CheckpointCard";

const clarify: CheckpointInfo = {
  id: "cp1",
  kind: "clarify",
  prompt: "想给几年级出题？",
  options: [
    { id: "g3", label: "三年级" },
    { id: "g4", label: "四年级" },
  ],
  payload: {},
};

const blueprint: CheckpointInfo = {
  id: "cp2",
  kind: "blueprint",
  prompt:
    "我先按您的要求排了一份细目表，您看看是否合适：\n- 选择题：3 道，每题 3 分，小计 9 分\n共 5 道题，满分 100 分，建议用时 40 分钟\n可以直接开始出题，也可以告诉我怎么调整。",
  options: [],
  payload: {
    plan: { total_score: 100, duration_min: 40, notes: ["题量按时长估计"] },
    table: [
      { title: "选择题", kind: "choice", count: 3, score_each: 3, subtotal: 9 },
      { title: "解决问题", kind: "application", count: 2, score_each: 45, subtotal: 91 },
    ],
  },
};

describe("澄清", () => {
  it("点选项回答；也可以说“你来定”或自由输入", async () => {
    const onAnswer = vi.fn();
    render(<CheckpointCard checkpoint={clarify} onAnswer={onAnswer} />);
    await userEvent.click(screen.getByRole("button", { name: "四年级" }));
    expect(onAnswer).toHaveBeenLastCalledWith({ option: "g4" });
    await userEvent.click(screen.getByRole("button", { name: "你来定" }));
    expect(onAnswer).toHaveBeenLastCalledWith({ text: "你来定" });
    await userEvent.type(screen.getByLabelText("补充说明"), "五年级下册{Enter}");
    expect(onAnswer).toHaveBeenLastCalledWith({ text: "五年级下册" });
  });

  it("空输入不能提交", () => {
    render(<CheckpointCard checkpoint={clarify} onAnswer={vi.fn()} />);
    expect(screen.getByRole("button", { name: "确定" })).toBeDisabled();
  });
});

describe("蓝图", () => {
  it("用表格展示细目表，不重复提示语里的文字版", () => {
    render(<CheckpointCard checkpoint={blueprint} onAnswer={vi.fn()} />);
    const table = screen.getByRole("table");
    expect(within(table).getByRole("row", { name: /选择题.*3.*3 分.*9 分/ })).toBeInTheDocument();
    expect(within(table).getByRole("row", { name: /合计.*5.*100 分/ })).toBeInTheDocument();
    expect(screen.queryByText(/- 选择题：3 道/)).not.toBeInTheDocument();
    expect(screen.getByText(/我先按您的要求排了一份细目表，您看看是否合适$/)).toBeInTheDocument();
    expect(screen.getByText("题量按时长估计")).toBeInTheDocument();
  });

  it("确认 / 调整", async () => {
    const onAnswer = vi.fn();
    render(<CheckpointCard checkpoint={blueprint} onAnswer={onAnswer} />);
    await userEvent.click(screen.getByRole("button", { name: "就这样，开始出题" }));
    expect(onAnswer).toHaveBeenLastCalledWith({ option: "confirm" });
    await userEvent.click(screen.getByRole("button", { name: "我想调整" }));
    await userEvent.type(screen.getByLabelText("怎么调整"), "选择题多两道{Enter}");
    expect(onAnswer).toHaveBeenLastCalledWith({ option: "adjust", text: "选择题多两道" });
  });
});

describe("样题", () => {
  it("展示样题，确认或重出", async () => {
    const onAnswer = vi.fn();
    const cp: CheckpointInfo = {
      id: "cp3",
      kind: "samples",
      prompt: "先出了几道样题",
      options: [],
      payload: {
        items: [
          {
            id: "a",
            kind: "choice",
            stem: "$1+1=$（　）",
            options: ["1", "2"],
            answer: "B",
            status: "verified",
          },
        ],
      },
    };
    render(<CheckpointCard checkpoint={cp} onAnswer={onAnswer} />);
    expect(screen.getByText(/1\. 选择题/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "风格不对，重出样题" }));
    expect(onAnswer).toHaveBeenLastCalledWith({ option: "redo" });
    await userEvent.click(screen.getByRole("button", { name: "合适，继续出全卷" }));
    expect(onAnswer).toHaveBeenLastCalledWith({ option: "confirm" });
  });
});
