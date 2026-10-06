import { describe, expect, it } from "vitest";
import { parseBlocks, parseInline, plainText } from "./parse";

describe("parseInline", () => {
  it("识别行内公式、填空线与普通文字", () => {
    expect(parseInline("计算：$1.2+3.4=$____")).toEqual([
      { t: "text", v: "计算：" },
      { t: "math", src: "1.2+3.4=", display: false },
      { t: "blank" },
    ]);
  });

  it("独立公式", () => {
    expect(parseInline("$$\\frac{1}{2}$$")).toEqual([
      { t: "math", src: "\\frac{1}{2}", display: true },
    ]);
  });

  it("美元金额不当公式：结尾 $ 后面紧跟数字，或开头 $ 后面是空白", () => {
    expect(parseInline("价格是 $5 和 $6 元")).toEqual([{ t: "text", v: "价格是 $5 和 $6 元" }]);
    expect(parseInline("a $ b $ c")).toEqual([{ t: "text", v: "a $ b $ c" }]);
  });

  it("转义的 $ 是普通字符", () => {
    expect(parseInline("花了 \\$5")).toEqual([{ t: "text", v: "花了 $5" }]);
  });

  it("公式里的下划线与星号不被当作标记", () => {
    const out = parseInline("$a_1 * b_2$");
    expect(out).toEqual([{ t: "math", src: "a_1 * b_2", display: false }]);
  });

  it("两个下划线不是填空线，三个及以上才是", () => {
    expect(parseInline("a__b")).toEqual([{ t: "text", v: "a__b" }]);
    expect(parseInline("____")).toEqual([{ t: "blank" }]);
    expect(parseInline("_______")).toEqual([{ t: "blank" }]);
  });

  it("插图引用", () => {
    expect(parseInline("如图 ![数轴](fig:f1) 所示")).toEqual([
      { t: "text", v: "如图 " },
      { t: "fig", id: "f1", alt: "数轴" },
      { t: "text", v: " 所示" },
    ]);
  });

  it("粗体、斜体、行内代码", () => {
    expect(parseInline("**重点**和*强调*和`x`")).toEqual([
      { t: "strong", c: [{ t: "text", v: "重点" }] },
      { t: "text", v: "和" },
      { t: "em", c: [{ t: "text", v: "强调" }] },
      { t: "text", v: "和" },
      { t: "code", v: "x" },
    ]);
  });

  it("乘号 * 两侧有空格时不成对", () => {
    expect(parseInline("3 * 4 * 5")).toEqual([{ t: "text", v: "3 * 4 * 5" }]);
  });
});

describe("parseBlocks", () => {
  it("单个换行视为空格，空行分段（与 Pandoc 一致）", () => {
    const b = parseBlocks("第一行\n第二行\n\n第二段");
    expect(b).toHaveLength(2);
    expect(b[0]).toEqual({ t: "p", c: [{ t: "text", v: "第一行 第二行" }] });
  });

  it("列表", () => {
    const b = parseBlocks("- 甲\n- 乙");
    expect(b[0]).toMatchObject({ t: "ul" });
    expect((b[0] as { items: unknown[] }).items).toHaveLength(2);
    expect(parseBlocks("1. 甲\n2. 乙")[0]).toMatchObject({ t: "ol" });
  });

  it("竖线表格", () => {
    const b = parseBlocks("| 月份 | 销量 |\n|---|---|\n| 一月 | 10 |\n| 二月 | 12 |");
    expect(b).toHaveLength(1);
    const t = b[0] as { t: string; head: unknown[]; rows: unknown[][] };
    expect(t.t).toBe("table");
    expect(t.head).toHaveLength(2);
    expect(t.rows).toHaveLength(2);
  });

  it("表格单元格里 \\| 不分列", () => {
    const b = parseBlocks("| a | b |\n|---|---|\n| $|x|$ | 1 |");
    expect((b[0] as { rows: unknown[][] }).rows[0]).toHaveLength(2);
  });

  it("空文本不产生块", () => {
    expect(parseBlocks("")).toEqual([]);
    expect(parseBlocks("  \n \n")).toEqual([]);
  });
});

describe("plainText", () => {
  it("去掉定界符与标记并截断", () => {
    expect(plainText("计算 $1+2$ ____ **好**")).toBe("计算 1+2 ＿＿ 好");
    expect(plainText("x".repeat(100), 10)).toBe("xxxxxxxxxx…");
    expect(plainText("见 ![图](fig:a)")).toBe("见 （图）");
  });
});
