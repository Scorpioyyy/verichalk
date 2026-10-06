import { describe, expect, it } from "vitest";
import { MAX_PHOTOS, acceptPhotos, imagesFrom } from "./photos";

const img = (name = "a.jpg", type = "image/jpeg", size = 1000) =>
  new File([new Uint8Array(size)], name, { type });

describe("acceptPhotos", () => {
  it("合格的图片并入已有的照片", () => {
    const r = acceptPhotos(
      [img("a.jpg")],
      [img("b.png", "image/png"), img("c.webp", "image/webp")],
    );
    expect(r.error).toBeNull();
    expect(r.files.map((f) => f.name)).toEqual(["a.jpg", "b.png", "c.webp"]);
  });

  it("不是 JPG / PNG / WebP 的丢掉并说明原因，其余照常收下", () => {
    const r = acceptPhotos(
      [],
      [img("n.txt", "text/plain"), img("g.gif", "image/gif"), img("a.jpg")],
    );
    expect(r.files.map((f) => f.name)).toEqual(["a.jpg"]);
    expect(r.error).toBe("暂只支持 JPG / PNG / WebP 图片。");
  });

  it("最多 4 张，超出的丢掉", () => {
    const five = Array.from({ length: 5 }, (_, i) => img(`${i}.jpg`));
    const r = acceptPhotos([], five);
    expect(r.files).toHaveLength(MAX_PHOTOS);
    expect(r.error).toBe("一次最多上传 4 张照片。");
  });

  it("超过 12MB 的丢掉", () => {
    const big = img("big.jpg", "image/jpeg", 12 * 2 ** 20 + 1);
    const r = acceptPhotos([], [big]);
    expect(r.files).toEqual([]);
    expect(r.error).toContain("12MB");
  });
});

describe("imagesFrom", () => {
  it("只取图片，容忍空值", () => {
    const got = imagesFrom([img("a.jpg"), img("n.pdf", "application/pdf")]);
    expect(got.map((f) => f.name)).toEqual(["a.jpg"]);
    expect(imagesFrom(null)).toEqual([]);
  });
});
