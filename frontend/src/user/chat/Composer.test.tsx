import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Composer } from "./Composer";

const photo = () => new File([new Uint8Array(10)], "page.jpg", { type: "image/jpeg" });

describe("输入框的照片上传", () => {
  it("没有传照片相关属性时不显示上传按钮", () => {
    render(<Composer value="" onChange={() => {}} onSend={() => {}} />);
    expect(screen.queryByRole("button", { name: "上传练习册照片" })).toBeNull();
  });

  it("选了照片：显示缩略图、可逐张移除；只有照片也能发送", async () => {
    globalThis.URL.createObjectURL = vi.fn(() => "blob:x");
    globalThis.URL.revokeObjectURL = vi.fn();
    const onSend = vi.fn();
    const onRemove = vi.fn();
    render(
      <Composer
        value=""
        onChange={() => {}}
        onSend={onSend}
        photos={[photo()]}
        onAddPhotos={() => {}}
        onRemovePhoto={onRemove}
      />,
    );
    expect(screen.getByRole("img", { name: /第 1 张照片/ })).toBeTruthy();
    const send = screen.getByRole("button", { name: "发送" });
    expect((send as HTMLButtonElement).disabled).toBe(false);
    await userEvent.click(send);
    expect(onSend).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "移除第 1 张照片" }));
    expect(onRemove).toHaveBeenCalledWith(0);
    expect(screen.getByPlaceholderText(/照这页再出 5 道/)).toBeTruthy();
  });

  it("文件选择、粘贴图片都会交给 onAddPhotos", async () => {
    const onAdd = vi.fn();
    render(
      <Composer
        value=""
        onChange={() => {}}
        onSend={() => {}}
        photos={[]}
        onAddPhotos={onAdd}
        onRemovePhoto={() => {}}
      />,
    );
    await userEvent.upload(screen.getByTestId("photo-input"), photo());
    expect(onAdd).toHaveBeenCalledTimes(1);
    expect(onAdd.mock.calls[0]![0][0].name).toBe("page.jpg");
    const box = screen.getByLabelText("输入您的需求");
    const paste = new Event("paste", { bubbles: true, cancelable: true }) as Event & {
      clipboardData: { files: File[] };
    };
    paste.clipboardData = { files: [photo()] };
    box.dispatchEvent(paste);
    expect(onAdd).toHaveBeenCalledTimes(2);
    expect(paste.defaultPrevented).toBe(true);
  });

  it("任务进行中不能再选照片，也不能发送", () => {
    render(
      <Composer
        value="x"
        busy
        onChange={() => {}}
        onSend={() => {}}
        photos={[]}
        onAddPhotos={() => {}}
        onRemovePhoto={() => {}}
      />,
    );
    const attach = screen.getByRole("button", { name: "上传练习册照片" }) as HTMLButtonElement;
    expect(attach.disabled).toBe(true);
    expect(screen.getByRole("button", { name: "停止" })).toBeTruthy();
  });
});
