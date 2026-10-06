import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Understanding } from "@/shared/api/types";
import { ChipsBar } from "./ChipsBar";

const u = {
  route: "generate",
  chips: [
    { key: "grade", label: "年级", value: "四年级下册", origin: "user" },
    { key: "count", label: "题量", value: "5 道", origin: "default" },
    { key: "difficulty", label: "难度", value: "中等", origin: "inferred" },
    { key: "scenes", label: "情境", value: "超市", origin: "default" },
  ],
} as unknown as Understanding;

describe("本次假设芯片", () => {
  it("显示每条假设，默认 / 推断的用虚线区分并有提示", () => {
    render(<ChipsBar understanding={u} onApply={vi.fn()} onPrefill={vi.fn()} />);
    expect(screen.getByRole("button", { name: /年级 四年级下册/ })).toHaveClass("chip--user");
    expect(screen.getByRole("button", { name: /题量 5 道/ })).toHaveClass("chip--default");
    expect(screen.getByRole("button", { name: /题量/ })).toHaveAttribute(
      "title",
      expect.stringContaining("点击可调整"),
    );
  });

  it("调题量：步进后一键重出", async () => {
    const onApply = vi.fn();
    render(<ChipsBar understanding={u} onApply={onApply} onPrefill={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /题量/ }));
    await userEvent.click(screen.getByRole("button", { name: "加一道" }));
    await userEvent.click(screen.getByRole("button", { name: "加一道" }));
    await userEvent.click(screen.getByRole("button", { name: "按此重出" }));
    expect(onApply).toHaveBeenCalledWith("题量改成 7 道，重新出");
  });

  it("题量范围 1～30", async () => {
    render(<ChipsBar understanding={u} onApply={vi.fn()} onPrefill={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /题量/ }));
    for (let i = 0; i < 8; i++)
      await userEvent.click(screen.getByRole("button", { name: "减一道" }));
    expect(screen.getByRole("status")).toHaveTextContent("1 道");
  });

  it("调难度", async () => {
    const onApply = vi.fn();
    render(<ChipsBar understanding={u} onApply={onApply} onPrefill={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /难度/ }));
    await userEvent.click(screen.getByRole("button", { name: /4 · 较难/ }));
    expect(onApply).toHaveBeenCalledWith("难度改成较难，重新出");
  });

  it("其他假设：把话放进输入框让教师补全", async () => {
    const onPrefill = vi.fn();
    render(<ChipsBar understanding={u} onApply={vi.fn()} onPrefill={onPrefill} />);
    await userEvent.click(screen.getByRole("button", { name: /情境/ }));
    await userEvent.click(screen.getByRole("button", { name: "在输入框里告诉我" }));
    expect(onPrefill).toHaveBeenCalledWith("情境改成");
  });

  it("运行中不可调整；Esc 关闭弹层；没有芯片时不渲染", async () => {
    const { rerender, container } = render(
      <ChipsBar understanding={u} disabled onApply={vi.fn()} onPrefill={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: /题量/ })).toBeDisabled();
    rerender(<ChipsBar understanding={u} onApply={vi.fn()} onPrefill={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /题量/ }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    rerender(
      <ChipsBar understanding={{ ...u, chips: [] }} onApply={vi.fn()} onPrefill={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
