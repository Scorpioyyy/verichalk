/** 拍照上传的前端校验：和后端的限制一致（至多 4 张、JPG / PNG / WebP、每张不超过 12MB），提前给出教师能看懂的提示。 */
export const MAX_PHOTOS = 4;
export const MAX_PHOTO_MB = 12;
export const PHOTO_TYPES = ["image/jpeg", "image/png", "image/webp"];

export interface PhotoCheck {
  files: File[];
  error: string | null;
}

/** 把新选的文件并入已有的照片：不合格的丢掉并说明原因，超出张数的丢掉。 */
export function acceptPhotos(current: File[], incoming: File[]): PhotoCheck {
  const files = [...current];
  let error: string | null = null;
  for (const f of incoming) {
    if (!PHOTO_TYPES.includes(f.type)) {
      error = "暂只支持 JPG / PNG / WebP 图片。";
      continue;
    }
    if (f.size > MAX_PHOTO_MB * 2 ** 20) {
      error = `图片不能超过 ${MAX_PHOTO_MB}MB，请压缩后重试。`;
      continue;
    }
    if (files.length >= MAX_PHOTOS) {
      error = `一次最多上传 ${MAX_PHOTOS} 张照片。`;
      continue;
    }
    files.push(f);
  }
  return { files, error };
}

/** 从拖放 / 粘贴事件里取出图片文件（其他类型的文件忽略）。 */
export function imagesFrom(list: FileList | File[] | null | undefined): File[] {
  return Array.from(list ?? []).filter((f) => f.type.startsWith("image/"));
}
