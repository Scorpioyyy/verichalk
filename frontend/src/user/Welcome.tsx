import type { RefObject } from "react";
import { Icon } from "@/shared/ui/Icon";
import { Composer } from "./chat/Composer";
import type { ComposerHandle } from "./chat/Composer";

interface Props {
  draft: string;
  setDraft: (v: string) => void;
  onSend: () => void;
  composerRef: RefObject<ComposerHandle | null>;
}

const EXAMPLES = [
  { tag: "单点练习", text: "四年级下册小数加减法，出 5 道，有点难度" },
  { tag: "跨知识点综合", text: "出 3 道超市购物情境、要用到小数和统计图的综合题" },
  { tag: "整份试卷", text: "出一份四年级下册第三单元的单元测试，40 分钟，满分 100" },
  { tag: "期末复习", text: "期末复习，把乘法分配律和简便运算串起来，出 6 道" },
];

/** 空状态：一句话说清能做什么，给几个可以直接改着用的例子。 */
export function Welcome({ draft, setDraft, onSend, composerRef }: Props) {
  return (
    <main className="welcome">
      <div className="welcome__inner">
        <h1 className="welcome__title">今天想出什么题？</h1>
        <p className="welcome__sub">
          说一句话就行：年级、范围、题量、难度。每道题都会经过核验，答案可靠、不超出学生学过的内容。
        </p>
        <Composer
          ref={composerRef}
          large
          value={draft}
          onChange={setDraft}
          onSend={onSend}
          placeholder="例如：四年级下册小数加减法，出 5 道，有点难度"
        />
        <ul className="examples" aria-label="试试这些">
          {EXAMPLES.map((e) => (
            <li key={e.text}>
              <button
                type="button"
                className="example"
                onClick={() => {
                  setDraft(e.text);
                  composerRef.current?.focus();
                }}
              >
                <span className="example__tag">{e.tag}</span>
                <span className="example__text">{e.text}</span>
              </button>
            </li>
          ))}
        </ul>
        <ul className="trust" aria-label="特点">
          <li>
            <Icon name="shield" size={16} /> 程序验算 + 独立解题，两路核验
          </li>
          <li>
            <Icon name="check" size={16} /> 按学到的课时把关，不超纲
          </li>
          <li>
            <Icon name="download" size={16} /> 一键导出 PDF / Word
          </li>
        </ul>
      </div>
    </main>
  );
}
