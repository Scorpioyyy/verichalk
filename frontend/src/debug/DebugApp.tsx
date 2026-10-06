import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api, getDebugToken, setDebugToken } from "@/shared/api/client";
import { Icon } from "@/shared/ui/Icon";
import { Badcases } from "./Badcases";
import { RunDetail } from "./RunDetail";
import { RunList } from "./RunList";
import "./debug.css";

/** 调试台外壳：令牌门、顶栏、路由。仅开发者使用，与用户端互不导入（只依赖 shared）。 */
export default function DebugApp() {
  const [gate, setGate] = useState<"checking" | "open" | "locked">("checking");
  const [error, setError] = useState("");

  async function probe() {
    try {
      await api.debug.runs({ limit: 1 });
      setGate("open");
      setError("");
    } catch (e) {
      const status = (e as { status?: number }).status;
      setGate("locked");
      if (status && status !== 403) setError((e as Error).message);
    }
  }
  useEffect(() => {
    void probe();
  }, []);

  if (gate === "checking") return <div className="dbg-boot">连接中……</div>;
  if (gate === "locked") return <TokenGate error={error} onSubmit={() => void probe()} />;

  return (
    <div className="dbg">
      <header className="dbg-top">
        <span className="dbg-brand">
          <Icon name="bug" size={18} /> VeriChalk 调试台
        </span>
        <nav className="dbg-nav" aria-label="调试台导航">
          <NavLink to="/debug" end>
            <Icon name="list" size={15} /> 运行
          </NavLink>
          <NavLink to="/debug/badcases">
            <Icon name="flag" size={15} /> Badcase
          </NavLink>
        </nav>
        <span className="dbg-spacer" />
        <a className="dbg-link" href="/">
          <Icon name="external" size={14} /> 用户端
        </a>
      </header>
      <Routes>
        <Route index element={<RunList />} />
        <Route path="runs/:runId" element={<RunDetail />} />
        <Route path="badcases" element={<Badcases />} />
        <Route path="*" element={<RunList />} />
      </Routes>
    </div>
  );
}

function TokenGate({ error, onSubmit }: { error: string; onSubmit: () => void }) {
  const [token, setToken] = useState(getDebugToken());
  return (
    <div className="dbg-gate">
      <form
        className="dbg-gate__card"
        onSubmit={(e) => {
          e.preventDefault();
          setDebugToken(token.trim());
          onSubmit();
        }}
      >
        <h1>调试台</h1>
        <p className="muted">
          调试数据含用户内容，需要令牌（环境变量
          VERICHALK_DEBUG_TOKEN）。本机开发未配置令牌时可直接访问。
        </p>
        <label className="field">
          <span className="field__label">令牌</span>
          <input
            className="input"
            type="password"
            autoFocus
            value={token}
            onChange={(e) => setToken(e.target.value)}
            autoComplete="off"
          />
        </label>
        {error && <p className="notice notice--bad">{error}</p>}
        {getDebugToken() && !error && <p className="notice notice--bad">令牌不对，请重试。</p>}
        <button type="submit" className="btn btn--primary" disabled={!token.trim()}>
          进入
        </button>
      </form>
    </div>
  );
}
