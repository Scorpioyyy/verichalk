import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { makeItem } from "@/test/fixtures";
import { ItemCard } from "./ItemCard";

vi.mock("./useKpNames", () => ({ useKpNames: () => ["小数乘整数（四下）"] }));

const base = { no: 3, teacher: true, figureUrl: (id: string) => `/fig/${id}` };

describe("ItemCard", () => {
  it("显示题号、题型、状态、选项字母与答案；用教师语言", () => {
    render(<ItemCard item={makeItem()} {...base} />);
    const card = screen.getByTestId("item");
    expect(card).toHaveAttribute("data-status", "verified");
    expect(within(card).getByText("选择题")).toBeInTheDocument();
    expect(within(card).getByText("已核验")).toBeInTheDocument();
    expect(within(card).getByText("3 分")).toBeInTheDocument();
    expect(card.querySelectorAll(".item__opts li")).toHaveLength(4);
    expect(within(card).getByText("答案")).toBeInTheDocument();
    expect(within(card).getByText(/涉及：小数乘整数（四下）/)).toBeInTheDocument();
    // 不外露内部词
    expect(card.textContent).not.toMatch(/verified|consolidate|kp_|undefined|null/);
  });

  it("学生版不显示答案与解析", () => {
    render(<ItemCard item={makeItem()} {...base} teacher={false} />);
    expect(screen.queryByText("答案")).not.toBeInTheDocument();
    expect(screen.queryByText("查看解析")).not.toBeInTheDocument();
  });

  it("解析默认折叠，点击展开", async () => {
    render(<ItemCard item={makeItem()} {...base} />);
    expect(document.querySelector(".item__solution")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "查看解析" }));
    expect(document.querySelector(".item__solution")).not.toBeNull();
    expect(screen.getByRole("button", { name: "收起解析" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("需复核：直接给出原因，不必展开", () => {
    const item = makeItem({
      verification: {
        status: "needs_review",
        checks: [{ name: "quality", status: "warn", detail: "题面有无关的干扰语句", evidence: {} }],
      },
    });
    render(<ItemCard item={item} {...base} />);
    expect(screen.getByText(/需要您确认：题面有无关的干扰语句/)).toBeInTheDocument();
    expect(screen.getByText("需复核")).toBeInTheDocument();
  });

  it("核验明细：未知检查名不外露，通过的项不显示细节", async () => {
    const item = makeItem({
      verification: {
        status: "verified",
        checks: [
          { name: "program", status: "pass", detail: "程序结果 3", evidence: {} },
          { name: "mystery_internal", status: "pass", detail: "", evidence: {} },
        ],
      },
    });
    render(<ItemCard item={item} {...base} />);
    await userEvent.click(screen.getByRole("button", { name: /已核验/ }));
    const list = screen.getByLabelText("核验明细");
    expect(within(list).getByText("程序验算")).toBeInTheDocument();
    expect(within(list).getByText("其他检查")).toBeInTheDocument();
    expect(list.textContent).not.toContain("mystery_internal");
    expect(list.textContent).not.toContain("程序结果 3"); // 通过项不啰嗦
  });

  it("后台正在核验时显示转圈的状态", () => {
    render(
      <ItemCard
        item={makeItem({ verification: { status: "pending", checks: [] } })}
        {...base}
        reviewing
      />,
    );
    expect(screen.getByText("正在核验")).toBeInTheDocument();
  });

  it("草稿（逐题送达）没有编辑类按钮", () => {
    render(<ItemCard item={makeItem()} {...base} draft />);
    expect(screen.queryByRole("button", { name: "编辑" })).not.toBeInTheDocument();
  });

  it("操作按钮回调；到顶 / 到底时移动按钮禁用", async () => {
    const onEdit = vi.fn();
    const onMove = vi.fn();
    render(
      <ItemCard
        item={makeItem()}
        {...base}
        canMoveUp={false}
        canMoveDown
        onEdit={onEdit}
        onMove={onMove}
      />,
    );
    expect(screen.getByRole("button", { name: "上移" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "下移" }));
    await userEvent.click(screen.getByRole("button", { name: "编辑" }));
    expect(onMove).toHaveBeenCalledWith(1);
    expect(onEdit).toHaveBeenCalled();
  });
});
