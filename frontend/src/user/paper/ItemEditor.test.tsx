import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Op } from "@/shared/api/types";
import { makeItem } from "@/test/fixtures";
import { ItemEditor } from "./ItemEditor";

const setup = (over = {}) => {
  const onSave = vi.fn<(ops: Op[]) => Promise<boolean>>().mockResolvedValue(true);
  const onCancel = vi.fn();
  render(
    <ItemEditor
      item={makeItem(over)}
      no={2}
      figureUrl={() => ""}
      onSave={onSave}
      onCancel={onCancel}
    />,
  );
  return { onSave, onCancel };
};

describe("ItemEditor", () => {
  it("没改动时不能保存", () => {
    setup();
    expect(screen.getByRole("button", { name: /保存/ })).toBeDisabled();
  });

  it("只提交真正改动的字段；改了答案要作废旧的精确值", async () => {
    const { onSave, onCancel } = setup({ answer_value: "3" });
    await userEvent.clear(screen.getByLabelText(/^答案/));
    await userEvent.type(screen.getByLabelText(/^答案/), "C");
    await userEvent.click(screen.getByRole("button", { name: /保存/ }));
    expect(onSave).toHaveBeenCalledTimes(1);
    const ops = onSave.mock.calls[0]![0] as { op: string; field: string; value: unknown }[];
    expect(ops.map((o) => o.field)).toEqual(["answer", "answer_value"]);
    expect(ops[0]!.value).toBe("C");
    expect(ops[1]!.value).toBeNull();
    expect(onCancel).toHaveBeenCalled(); // 保存成功后退出编辑
  });

  it("保存失败时留在编辑状态，输入不丢", async () => {
    const onSave = vi.fn().mockResolvedValue(false);
    const onCancel = vi.fn();
    render(
      <ItemEditor
        item={makeItem()}
        no={1}
        figureUrl={() => ""}
        onSave={onSave}
        onCancel={onCancel}
      />,
    );
    await userEvent.type(screen.getByLabelText("题干"), "补充");
    await userEvent.click(screen.getByRole("button", { name: /保存/ }));
    expect(onCancel).not.toHaveBeenCalled();
    expect((screen.getByLabelText("题干") as HTMLTextAreaElement).value).toContain("补充");
  });

  it("题干不能为空；分值要合法", async () => {
    setup();
    await userEvent.clear(screen.getByLabelText("题干"));
    expect(screen.getByRole("button", { name: /保存/ })).toBeDisabled();
    expect(screen.getByText("题干不能为空")).toBeInTheDocument();
  });

  it("分值不合法时给出提示并禁止保存", async () => {
    setup();
    await userEvent.type(screen.getByLabelText("题干"), "x");
    await userEvent.clear(screen.getByLabelText("分值"));
    await userEvent.type(screen.getByLabelText("分值"), "abc");
    expect(screen.getByText(/分值请填/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /保存/ })).toBeDisabled();
  });

  it("选项逐个可改，提交整组选项", async () => {
    const { onSave } = setup();
    const b = screen.getByLabelText("选项 B");
    await userEvent.clear(b);
    await userEvent.type(b, "3.0");
    await userEvent.click(screen.getByRole("button", { name: /保存/ }));
    const ops = onSave.mock.calls[0]![0] as { field: string; value: unknown }[];
    expect(ops).toHaveLength(1);
    expect(ops[0]).toMatchObject({ field: "options", value: ["30", "3.0", "0.3", "3.5"] });
  });

  it("插入符号按钮把片段放进光标处", async () => {
    setup({ stem: "ab" });
    const ta = screen.getByLabelText("题干") as HTMLTextAreaElement;
    ta.focus();
    ta.setSelectionRange(1, 1);
    await userEvent.click(screen.getByRole("button", { name: "×" }));
    expect(ta.value).toBe("a$\\times$b");
  });
});
