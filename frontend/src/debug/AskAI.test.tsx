import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, type ChatEvent, type ChatTurn } from "@/shared/api/client";
import { AskAI } from "./AskAI";
import { parseBlocks } from "./markdown";

afterEach(() => vi.restoreAllMocks());

function fakeChat(script: ChatEvent[]) {
  return vi.spyOn(api.debug, "chat").mockImplementation(async (_id: string, _m: ChatTurn[], o) => {
    for (const e of script) o.onEvent(e);
  });
}

describe("极简 Markdown", () => {
  it("识别标题、列表、表格与代码块", () => {
    const blocks = parseBlocks(
      "## 结论\n\n- 一\n- 二\n\n1. 甲\n2. 乙\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```\nx = 1\n```\n",
    );
    expect(blocks.map((b) => b.t)).toEqual(["h", "ul", "ol", "table", "code"]);
  });

  it("流式中途的未闭合代码块不会丢内容", () => {
    expect(parseBlocks("```\nabc")).toEqual([{ t: "code", text: "abc" }]);
  });
});

describe("AI 分析面板", () => {
  it("点建议问题：展示查询过程与回答，并把完整历史带给后端", async () => {
    const chat = fakeChat([
      { type: "tool", name: "get_stage_output", label: "查看阶段快照", arguments: {} },
      { type: "delta", text: "这次运行**一切正常**，" },
      { type: "delta", text: "共 3 次模型调用。" },
      { type: "done" },
    ]);
    render(<AskAI runId="run_1" ready />);
    await userEvent.click(screen.getByRole("button", { name: /总结这次运行/ }));
    expect(await screen.findByText("一切正常")).toBeInTheDocument();
    expect(screen.getByText(/查看阶段快照/)).toBeInTheDocument();
    expect(chat.mock.calls[0]?.[1]).toEqual([
      { role: "user", content: expect.stringContaining("总结这次运行") },
    ]);

    await userEvent.type(screen.getByRole("textbox", { name: "向分析助手提问" }), "成本呢{Enter}");
    await waitFor(() => expect(chat).toHaveBeenCalledTimes(2));
    const sent = chat.mock.calls[1]?.[1] ?? [];
    expect(sent.map((m) => m.role)).toEqual(["user", "assistant", "user"]);
    expect(sent[1]?.content).toBe("这次运行**一切正常**，共 3 次模型调用。");
  });

  it("出错时给出提示，不影响继续提问；运行未就绪时不能发送", async () => {
    fakeChat([{ type: "error", message: "分析助手出错：X" }]);
    const { rerender } = render(<AskAI runId="run_1" ready={false} />);
    expect(screen.getByRole("textbox", { name: "向分析助手提问" })).toBeDisabled();
    rerender(<AskAI runId="run_1" ready />);
    await userEvent.type(screen.getByRole("textbox", { name: "向分析助手提问" }), "hi{Enter}");
    expect(await screen.findByText("分析助手出错：X")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "向分析助手提问" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "清空对话" }));
    expect(screen.getByText("问问这次运行")).toBeInTheDocument();
  });
});

describe("推荐提问", () => {
  it("针对这次运行的推荐与常见问题分开显示；点推荐就发问", async () => {
    const chat = fakeChat([{ type: "delta", text: "好" }, { type: "done" }]);
    render(<AskAI runId="run_1" ready specific={["为什么「produce」阶段最慢？"]} />);
    expect(screen.getByText("针对这次运行")).toBeInTheDocument();
    expect(screen.getByText("常见问题")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "为什么「produce」阶段最慢？" }));
    await waitFor(() => expect(chat).toHaveBeenCalled());
    expect(chat.mock.calls[0]?.[1]?.[0]?.content).toBe("为什么「produce」阶段最慢？");
  });

  it("没有针对这次运行的推荐时只显示常见问题", () => {
    render(<AskAI runId="run_1" ready />);
    expect(screen.queryByText("针对这次运行")).toBeNull();
    expect(screen.getByText("常见问题")).toBeInTheDocument();
  });
});

describe("流式输出时的滚动", () => {
  it("默认跟着往下滚；教师自己滚动后，这一轮不再自动滚；下一轮重新跟随", async () => {
    const scroll = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    // offsetParent 在 jsdom 里恒为 null：让面板视为可见
    vi.spyOn(HTMLElement.prototype, "offsetParent", "get").mockReturnValue(document.body);
    let emit: (e: ChatEvent) => void = () => {};
    let finish: () => void = () => {};
    vi.spyOn(api.debug, "chat").mockImplementation(
      (_id: string, _m: ChatTurn[], o) =>
        new Promise<void>((resolve) => {
          emit = o.onEvent;
          finish = resolve;
        }),
    );
    render(<AskAI runId="run_1" ready />);
    await userEvent.type(screen.getByRole("textbox", { name: "向分析助手提问" }), "q{Enter}");
    emit({ type: "delta", text: "第一段" });
    await waitFor(() => expect(screen.getByText("第一段")).toBeInTheDocument());
    const followed = scroll.mock.calls.length;
    expect(followed).toBeGreaterThan(0);

    window.dispatchEvent(new Event("wheel"));
    scroll.mockClear();
    emit({ type: "delta", text: "第二段" });
    await waitFor(() => expect(screen.getByText("第一段第二段")).toBeInTheDocument());
    expect(scroll).not.toHaveBeenCalled();

    emit({ type: "done" });
    finish();
    await waitFor(() => expect(screen.getByRole("button", { name: /发送/ })).toBeInTheDocument());
    await userEvent.type(screen.getByRole("textbox", { name: "向分析助手提问" }), "again{Enter}");
    await waitFor(() => expect(scroll).toHaveBeenCalled());
  });
});
