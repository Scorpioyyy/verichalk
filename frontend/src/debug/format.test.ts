import { describe, expect, it } from "vitest";
import { midTruncate } from "./format";

describe("midTruncate", () => {
  it("长 ID 保留头尾、中间省略；短的原样", () => {
    expect(midTruncate("run_01M48QPHKNJF0Z90R9SBEAB0FH")).toBe("run_01M4…EAB0FH");
    expect(midTruncate("run_abc")).toBe("run_abc");
  });
});
