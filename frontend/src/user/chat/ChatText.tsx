import { Rich } from "@/shared/math/Rich";

/** 对话文本：保留换行，"- " 开头的连续行成列表，行内公式用 KaTeX。 */
export function ChatText({ text }: { text: string }) {
  const lines = text.split("\n");
  const out: React.ReactNode[] = [];
  let bullets: string[] = [];
  const flush = (key: string) => {
    if (bullets.length) {
      out.push(
        <ul key={key}>
          {bullets.map((b, i) => (
            <li key={i}>
              <Rich text={b} inline />
            </li>
          ))}
        </ul>,
      );
      bullets = [];
    }
  };
  lines.forEach((line, i) => {
    const m = /^\s*[-•]\s+(.*)$/.exec(line);
    if (m) {
      bullets.push(m[1]!);
      return;
    }
    flush(`ul${i}`);
    if (line.trim()) {
      out.push(
        <p key={i}>
          <Rich text={line} inline />
        </p>,
      );
    }
  });
  flush("ul-end");
  return <div className="chat-text">{out}</div>;
}
