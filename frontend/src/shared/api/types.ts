/**
 * 契约类型：全部来自后端 OpenAPI 生成的 `schema.gen.ts`（`python scripts/gen_types.py`），这里只做命名与"响应一定带全字段"的修正。
 *
 * 后端响应总会带上带默认值的字段（FastAPI 不做 exclude_unset），但生成的类型把它们标成可选；
 * `Full<T>` 把响应类型里的可选字段还原为必有，避免到处写 `?? []`。
 */
import type { components, operations } from "./schema.gen";

type S = components["schemas"];

export type Full<T> = T extends (infer U)[]
  ? Full<U>[]
  : T extends object
    ? { [K in keyof T]-?: Full<Exclude<T[K], undefined>> }
    : T;

// ---- 试卷 ----
export type Paper = Full<S["Paper"]>;
export type Section = Full<S["Section"]>;
export type Item = Full<S["Item"]>;
export type ItemKind = S["ItemKind"];
export type Tier = S["Tier"];
export type FigureSpec = Full<S["FigureSpec"]>;
export type Verification = Full<S["Verification"]>;
export type CheckResult = Full<S["CheckResult"]>;
export type VerifyStatus = Verification["status"];
export type Revision = Full<S["Revision"]>;
export type PaperDiff = Full<S["PaperDiff"]>;
export type ItemChange = Full<S["ItemChange"]>;

// ---- 会话与运行 ----
export type SessionState = Full<S["SessionState"]>;
export type Message = Full<S["Message"]>;
export type Run = Full<S["Run"]>;
export type RunView = Full<S["RunView"]>;
export type RunStatus = Run["status"];
export type TurnAccepted = Full<S["TurnAccepted"]>;
export type PaperUpdate = Full<S["PaperUpdate"]>;
export type PaperHistory = Full<S["PaperHistory"]>;
export type Health = Full<S["HealthOut"]>;
export type KPRef = Full<S["KPRef"]>;
export type KPDetail = Full<S["KPDetail"]>;
export type ErrorInfo = Full<S["ErrorInfo"]>;

// ---- 需求理解 ----
export type Understanding = Full<S["Understanding"]>;
export type Chip = Full<S["Chip"]>;
export type ClarifyRequest = Full<S["ClarifyRequest"]>;

// ---- 导出 ----
export type ExportOptions = Full<S["ExportOptions"]>;
export type ExportFormat = ExportOptions["format"];
export type ExportVersion = ExportOptions["version"];
export type AnswerPlacement = ExportOptions["answers"];

// ---- 补丁 ----
export type Op = S["PaperPatchBody"]["ops"][number];

// ---- 事件 ----
type DebugEvents =
  operations["debug_events_api_debug_runs__run_id__events_get"]["responses"][200]["content"]["application/json"];
export type AppEvent = Full<DebugEvents[number]>;
export type EventType = AppEvent["type"];
export type EventOf<T extends EventType> = Extract<AppEvent, { type: T }>;

// ---- 调试台 ----
export type DebugRunItem = Full<S["DebugRunItem"]>;
export type DebugRunDetail = Full<S["DebugRunDetail"]>;
export type RunMetrics = Full<S["RunMetrics"]>;
export type AggregateMetrics = Full<S["AggregateMetrics"]>;
export type LLMCallRecord = Full<S["LLMCallRecord"]>;
export type RetrievalPayload = Full<S["RetrievalPayload"]>;
export type GraphNode = Full<S["GraphNode"]>;
export type GraphEdge = Full<S["GraphEdge"]>;
export type Badcase = Full<S["Badcase"]>;
export type BadcaseIn = S["BadcaseIn"];
export type RootCause = BadcaseIn["root_cause"];
export type Severity = NonNullable<BadcaseIn["severity"]>;
