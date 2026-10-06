import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { HistoryMenu } from "./HistoryMenu";

const recent = [
  { id: "ses_1", title: "四下小数加减法", ts: 1_700_000_000_000 },
  { id: "ses_2", title: "期末复习", ts: 1_700_100_000_000 },
];

function setup() {
  const fns = { onOpen: vi.fn(), onRename: vi.fn(), onDelete: vi.fn() };
  render(<HistoryMenu recent={recent} currentId="ses_1" {...fns} />);
  return fns;
}

describe("历史对话菜单", () => {
  it("点一行打开对话", async () => {
    const f = setup();
    await userEvent.click(screen.getByRole("menuitem", { name: /期末复习/ }));
    expect(f.onOpen).toHaveBeenCalledWith("ses_2");
  });

  it("重命名：回车确认；清空或不改不提交；Esc 取消", async () => {
    const f = setup();
    await userEvent.click(screen.getByRole("button", { name: "重命名“期末复习”" }));
    const box = screen.getByRole("textbox", { name: "对话标题" });
    await userEvent.clear(box);
    await userEvent.type(box, "六年级期末{Enter}");
    expect(f.onRename).toHaveBeenCalledWith("ses_2", "六年级期末");

    await userEvent.click(screen.getByRole("button", { name: "重命名“四下小数加减法”" }));
    await userEvent.type(screen.getByRole("textbox", { name: "对话标题" }), "{Escape}");
    expect(f.onRename).toHaveBeenCalledTimes(1);

    await userEvent.click(screen.getByRole("button", { name: "重命名“四下小数加减法”" }));
    await userEvent.clear(screen.getByRole("textbox", { name: "对话标题" }));
    await userEvent.keyboard("{Enter}");
    expect(f.onRename).toHaveBeenCalledTimes(1);
  });

  it("删除要再确认一次；取消不删", async () => {
    const f = setup();
    await userEvent.click(screen.getByRole("button", { name: "删除“期末复习”" }));
    expect(f.onDelete).not.toHaveBeenCalled();
    expect(screen.getByText(/不可恢复/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(f.onDelete).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "删除“期末复习”" }));
    await userEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(f.onDelete).toHaveBeenCalledWith("ses_2");
  });

  it("没有历史时给出提示", () => {
    render(
      <HistoryMenu
        recent={[]}
        currentId={null}
        onOpen={() => {}}
        onRename={() => {}}
        onDelete={() => {}}
      />,
    );
    expect(screen.getByText("还没有历史对话")).toBeTruthy();
  });
});
