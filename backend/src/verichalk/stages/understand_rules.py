"""规则解析：不调用模型的意图解析（评测基线，同时是模型不可用时的降级方案）。

纯函数：同一输入永远得到同一输出。能力有限（口语化、省略、多意图都会出错），这正是模型版要超越的基线。
"""

from __future__ import annotations

import re

from ..domain.brief import PaperSpec, SourceMode
from ..domain.paper import ItemKind, Tier
from ..domain.understanding import EditIntent, RawParse, Route

_CN = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_GRADE_RE = re.compile(r"([一二三四五六1-6])\s*年\s*[级纪]\s*(?:的)?\s*(?:(上|下)\s*(?:册|学期)?)?")
_GRADE_SEM_ABBR = re.compile(r"(?<![一-龥])([一二三四五六])\s*([上下])(?![一-龥]{0,1}面)")
_SEM_ONLY = re.compile(r"(上|下)\s*(?:册|学期)")
_UNIT_RE = re.compile(r"第\s*([一二三四五六七八九十1-9])\s*单元")
_COUNT_RE = re.compile(r"(?<![\d.])(\d{1,3})\s*(?:道|个|题|到)")
_COUNT_CN_RE = re.compile(r"([一二两三四五六七八九十]{1,3})\s*(?:道|个)")
_DURATION_RE = re.compile(r"(\d{1,3})\s*分钟")
_SCORE_RE = re.compile(r"(?:满分|总分)\s*(\d{1,3})")

_DIFF = [
    (re.compile(r"有点难度|有些难度|稍有难度"), [3, 5]),
    (re.compile(r"拔高|挑战|偏难|难度大|难一点|难些|难题|更难|难度高"), [4, 5]),
    (re.compile(r"简单|基础|容易|入门|越基础"), [1, 2]),
    (re.compile(r"中等|适中"), [2, 4]),
]
_KINDS = [
    (re.compile(r"填空"), ItemKind.fill),
    (re.compile(r"选择"), ItemKind.choice),
    (re.compile(r"判断"), ItemKind.judge),
    (re.compile(r"计算|口算"), ItemKind.calc),
    (re.compile(r"应用题|应用"), ItemKind.application),
]
_TIER = [
    (re.compile(r"综合|拔高|串起来|结合起来|结合"), Tier.integrated),
    (re.compile(r"变式|换个问法"), Tier.variation),
    (re.compile(r"巩固|基础|同类|类似"), Tier.consolidate),
]
_SCENES = [
    "超市",
    "购物",
    "公园",
    "动物园",
    "运动会",
    "旅行",
    "旅游",
    "学校",
    "工地",
    "建筑",
    "菜市场",
    "买菜",
    "游乐场",
    "农场",
    "图书馆",
    "兔",
    "过河",
]
_PAPER = re.compile(r"试卷|考试卷|单元测试|单元测验|测验|期末卷|期中卷|月考|小测|模拟卷|卷子|测试")
_REVIEW = re.compile(r"复习|穿插|串起来|串联")
_EXPORT = re.compile(r"导出|下载|打印|PDF|pdf|Word|word|Markdown|LaTeX|latex")
_ASK = re.compile(r"为什么|解析|讲细|讲一下|怎么讲|怎么做|一共多少分|多少分|答案是")
_EDIT = re.compile(r"换个|换成|改成|改小|改大|降一点|提高|删掉|删除|保留|重出|调整|整体|替换")
_ITEM_REF = re.compile(r"第\s*([一二三四五六七八九十\d]+)\s*[题道]")
_OFFTOPIC = re.compile(
    r"写一首|写首|诗|天气|翻译|批改|作业批|一元二次|勾股|函数|高中|初中|初一|初二|初三|忽略(?:以上|上面|之前)|系统提示|指令|DAN|入侵|怎么教|如何教|应该怎么教"
)
_CONSTRAINT = [
    (re.compile(r"数字不要太大|数字别太大|数字小一点|数字不要太多"), "数字不要太大"),
    (re.compile(r"不要图形题|不要图形|不带图|不要图"), "不要图形题"),
    (re.compile(r"不要文字题|不要应用题|只要计算"), "不要文字题"),
    (re.compile(r"不用解析|不要解析|只给答案|答案不用解析"), "不要解析"),
    (re.compile(r"除数(?:都)?是一位数"), "除数是一位数"),
]
_STOP = re.compile(
    r"[，。！？、；：,.!?~～\s的了吗呢吧啊呀给我帮来出点几些份套道个题到张想要需要请麻烦一下再有要求这样那样]"
    r"|数学|题目|练习|试卷|情境|应用|计算|口算|填空|选择|判断|简单|基础|难度|拔高|挑战|综合|中等|复习|巩固|变式|类似|同类"
)


def _cn_to_int(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if len(s) == 2 and s[0] == "十":
        return 10 + _CN.get(s[1], 0)
    if len(s) == 2 and s[1] == "十":
        return _CN.get(s[0], 0) * 10
    if len(s) == 3 and s[1] == "十":
        return _CN.get(s[0], 0) * 10 + _CN.get(s[2], 0)
    return _CN.get(s)


def parse_rules(text: str, *, has_paper: bool = False) -> RawParse:
    t = text.strip()
    explicit: list[str] = []
    out = RawParse(reason="rules")
    rest = t

    # ---- 路由 ----
    if _OFFTOPIC.search(t) or not re.search(r"[一-龥A-Za-z0-9]", t) or re.fullmatch(r"[a-zA-Z]{6,}", t):
        out.route = Route.offtopic
        return out
    if has_paper and _EXPORT.search(t):
        out.route = Route.export
        return out
    if has_paper and _ASK.search(t) and not _EDIT.search(t):
        out.route = Route.ask
        return out
    if has_paper and _EDIT.search(t) and not re.search(r"再来|再出", t):
        item = _ITEM_REF.search(t)
        out.route = Route.edit
        out.edit = EditIntent(target=f"item:{_cn_to_int(item.group(1))}" if item else "all", instruction=t)
        return out
    if (_PAPER.search(t) and not re.search(r"\d+\s*道", t)) or re.search(
        r"(单元测试|期末|期中|月考|模拟卷|卷子)", t
    ):
        out.route = Route.paper
        out.action = "paper"

    # ---- 年级 / 学期 / 单元 ----
    m = _GRADE_RE.search(t)
    if m:
        out.grade = _cn_to_int(m.group(1))
        explicit.append("grade")
        if m.group(2):
            out.semester = "a" if m.group(2) == "上" else "b"
            explicit.append("semester")
        rest = rest.replace(m.group(0), " ")
    else:
        m2 = _GRADE_SEM_ABBR.search(t)
        if m2:
            out.grade = _cn_to_int(m2.group(1))
            out.semester = "a" if m2.group(2) == "上" else "b"
            explicit += ["grade", "semester"]
            rest = rest.replace(m2.group(0), " ")
    if out.semester is None:
        m3 = _SEM_ONLY.search(t)
        if m3:
            out.semester = "a" if m3.group(1) == "上" else "b"
            explicit.append("semester")
            rest = rest.replace(m3.group(0), " ")
    mu = _UNIT_RE.search(t)
    if mu:
        n = _cn_to_int(mu.group(1))
        if n:
            out.unit_ordinals = [n]
            explicit.append("unit")
        rest = rest.replace(mu.group(0), " ")

    # ---- 题量 / 时长 / 总分 ----
    paper = PaperSpec()
    md, ms = _DURATION_RE.search(t), _SCORE_RE.search(t)
    if md:
        paper.duration_min = int(md.group(1))
        rest = rest.replace(md.group(0), " ")
    if ms:
        paper.total_score = float(ms.group(1))
        rest = rest.replace(ms.group(0), " ")
    if md or ms:
        out.paper = paper
    mc = _COUNT_RE.search(rest) or None
    if mc and int(mc.group(1)) > 0:
        out.count = int(mc.group(1))
        explicit.append("count")
        rest = rest.replace(mc.group(0), " ")
    else:
        mcn = _COUNT_CN_RE.search(rest)
        if mcn and _cn_to_int(mcn.group(1)):
            out.count = _cn_to_int(mcn.group(1))
            explicit.append("count")
            rest = rest.replace(mcn.group(0), " ")
    if re.search(r"\d+\s*道", t) and re.search(r"\b0\s*道", t):
        out.count = None  # "0 道"不是有效题量

    # ---- 难度 / 题型 / 档位 / 来源 / 情境 / 限制 ----
    for pat, rng in _DIFF:
        if pat.search(t):
            out.difficulty = list(rng)
            explicit.append("difficulty")
            break
    kinds = []
    for pat, k in _KINDS:
        if pat.search(t) and k not in kinds:
            kinds.append(k)
    if kinds:
        out.kinds = kinds
        explicit.append("kinds")
    for pat, tier in _TIER:
        if pat.search(t):
            out.tier = tier
            explicit.append("tier")
            break
    if re.search(r"课本上(?:那种)?的?基础题|口算卡|和课本一样|跟课本一样", t):
        out.source = SourceMode.template
        explicit.append("source")
    elif re.search(r"有创意|创新|原创|不要课本上的原题|不要原题", t):
        out.source = SourceMode.novel
        explicit.append("source")
    scenes = [s for s in _SCENES if s in t]
    if scenes:
        out.scenes = scenes
        explicit.append("scenes")
    cons = [label for pat, label in _CONSTRAINT if pat.search(t)]
    if cons:
        out.constraints = cons
        explicit.append("constraints")
    if _REVIEW.search(t):
        out.action = "review"
    if _PAPER.search(t) and out.route == Route.paper:
        out.action = "paper"

    # ---- 主题：去掉已解析的部分和套话后剩下的内容 ----
    for pat, _ in [*_DIFF, *_KINDS, *_TIER]:
        rest = pat.sub(" ", rest)
    for c in cons:
        rest = rest.replace(c, " ")
    for s in scenes:
        rest = rest.replace(s, " ")
    rest = _PAPER.sub(" ", rest)
    rest = _REVIEW.sub(" ", rest)
    rest = re.sub(r"期末|期中|月考|单元|总复习|寒假|暑假|毕业|考试|满分|分钟|第\s*\d+", " ", rest)
    rest = _STOP.sub("", rest)
    rest = re.sub(r"[a-zA-Z0-9]", "", rest)
    if len(rest) >= 2:
        out.topics = [rest]
    out.explicit = explicit
    return out
