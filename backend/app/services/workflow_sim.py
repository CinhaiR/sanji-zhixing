"""本地「工作流」：DeepSeek + 知识库问答检索 + 提示词装配

对应讯飞工作流的三步链路，但全部在本项目内实现：

    ① 接收用户输入
    ② 在知识库中检索相关问答（knowledge_txt/三晋智行_全量知识库（问答对）.txt）
    ③ 参考检索到的问答，按 prompts/讯飞大模型节点提示词.md 的提示词作答

为什么自建：讯飞工作流那侧的授权/编排问题不受我们控制，而这条链路
完全可控，且地理准确性可以复用本项目的规则校验。

检索策略（不依赖 embedding，零额外依赖）：
1. 城市优先：问题中提到某府邑时，先取该城的汇总问答（景点/特产）
2. 词面相关：以「问题+答案」为语料，用中文二元组 + 英文数字词做词面打分
3. 兜底：仍不足则补该城任意问答
"""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.core.config import BACKEND_DIR, settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 数据加载


# 山西 11 个地级市（按长度降序，避免「晋中/晋城」类前缀误配）
CITY_NAMES = [
    "大同", "朔州", "忻州", "太原", "晋中", "吕梁",
    "阳泉", "长治", "晋城", "临汾", "运城",
]
# 景点类问法
_ATTRACTION_MARK = ("景点", "在哪里", "是什么地方", "值得去", "木塔", "石窟", "寺", "庙", "宫", "楼", "关", "古城", "大院", "瀑布", "山")
# 特产类问法
_FOOD_MARK = ("特产", "好吃的", "美食", "吃什么", "是什么", "凉粉", "牛肉", "羊肉", "面", "饼", "糕", "蒜", "醋", "汤", "碗")


def infer_city(answer: str) -> str:
    """从答案文本里推断归属地。

    语料中每条答案的首句都点明了归属，形如：
      「平遥牛肉是山西晋中的特产，不是朔州、大同的特产。」
      「应县木塔（佛宫寺释迦塔）位于山西朔州，是……」
    因此可直接抽取，无需依赖文件里的标题行 —— 这样无论知识库文件
    是「带标题的导出格式」还是「纯问答对」，都能正确归属。
    """
    if not answer:
        return ""
    # 优先「是山西X的」/「位于山西X」
    m = re.search(r"是山西(" + "|".join(CITY_NAMES) + r")", answer)
    if m:
        return m.group(1)
    m = re.search(r"位于山西(" + "|".join(CITY_NAMES) + r")", answer)
    if m:
        return m.group(1)
    # 退而求其次：取首个出现的城市名（长名优先）
    for c in CITY_NAMES:
        if c in answer:
            return c
    return ""


def infer_kind(pair_questions: List[str], answer: str) -> str:
    """推断条目类型：attraction 景点 / food 特产 / city 府邑概况 / unknown"""
    blob = " ".join(pair_questions) + answer
    if any(k in blob for k in ("有哪些景点", "有什么好玩", "必去的景点", "在哪里", "值得去")):
        return "attraction"
    if any(k in blob for k in ("特产", "好吃的", "美食", "吃什么")):
        return "food"
    if any(k in blob for k in ("怎样的地方", "有什么特色", "有哪些地级市")):
        return "city"
    # 兜底：按「是XX的特产」句式判断
    if "的特产" in answer:
        return "food"
    if "位于山西" in answer or "又称" in answer:
        return "attraction"
    return "unknown"


class QAPair:
    __slots__ = ("questions", "answer", "section", "header", "kind", "raw")

    def __init__(
        self,
        questions: List[str],
        answer: str,
        section: str = "",
        header: str = "",
        kind: str = "unknown",
    ):
        self.questions = questions          # 等价问法（同一答案的多种问句）
        self.answer = answer
        self.section = section              # 所属分节，如「二、景点问答」
        self.header = header                # 归属地：优先取文件标题，缺失则从答案推断
        self.kind = kind
        self.raw = ""

    @property
    def text(self) -> str:
        return " ".join(self.questions) + " " + self.answer


def _resolve(path_str: str) -> Path:
    """配置里的路径以项目根目录为基准（backend 的上一级）"""
    p = Path(path_str)
    if p.is_absolute():
        return p
    root = BACKEND_DIR.parent
    if (root / p).exists():
        return root / p
    return BACKEND_DIR / p


class _KnowledgeIndex:
    """问答对索引，按文件 mtime 自动失效"""

    def __init__(self) -> None:
        self.pairs: List[QAPair] = []
        self.by_city: Dict[str, List[QAPair]] = {}
        self.cities: List[str] = []
        self._mtime: Optional[float] = None
        self._lock = threading.Lock()

    # ---- 加载

    def load(self, force: bool = False) -> None:
        path = _resolve(settings.agent_kb_file)
        if not path.exists():
            self.pairs, self.by_city, self.cities = [], {}, []
            return
        mtime = path.stat().st_mtime
        with self._lock:
            if not force and self._mtime == mtime and self.pairs:
                return
            pairs = self._parse(path.read_text(encoding="utf-8"))
            by_city: Dict[str, List[QAPair]] = {}
            cities: List[str] = []
            for p in pairs:
                if p.header:
                    if p.header not in by_city:
                        by_city[p.header] = []
                        cities.append(p.header)
                    by_city[p.header].append(p)
            self.pairs = pairs
            self.by_city = by_city
            self.cities = cities
            self._mtime = mtime

    @staticmethod
    def _parse(text: str) -> List[QAPair]:
        """解析导出文件的固定格式：

            ==== 分隔线 ====
            一、山西与各府邑总览
            ---------------- 分隔线 ----------------
            产地：朔州
            ----------------
            问：A／B／C
            答：……

        注意：`府邑：X` / `产地：X` 与紧随其后的第一条问答对常处于同一块
        （中间只隔着分隔线），因此必须先取标题再解析问答，且标题要沿用给后续块。
        """
        pairs: List[QAPair] = []
        section = ""
        header = ""

        blocks = re.split(r"\n\s*\n", text)
        for block in blocks:
            b = block.strip()
            if not b:
                continue

            # 去掉纯分隔线行，避免误入标题解析
            body_lines = [
                ln
                for ln in b.splitlines()
                if ln.strip() and not re.fullmatch(r"[-=─]{3,}", ln.strip())
            ]
            if not body_lines:
                continue
            body = "\n".join(body_lines)

            # 分节标题：一、二、…（可能带有前后缀）
            m_sec = re.search(r"^[一二三四五六七八九十]+、\s*(.+)$", body, re.MULTILINE)
            if m_sec:
                section = m_sec.group(1).strip()

            # 府邑/产地标题：先更新 header，再继续解析本块内的问答对
            m_hdr = re.search(r"^(?:府邑|产地)：\s*(.+?)\s*$", body, re.MULTILINE)
            if m_hdr:
                header = m_hdr.group(1).strip()

            if "问：" not in body:
                continue

            # 本块可能含多于一个问答对
            for chunk in re.split(r"\n(?=\s*问：)", body):
                chunk = chunk.strip()
                if not chunk.startswith("问："):
                    continue
                lines = [ln for ln in chunk.splitlines() if ln.strip()]
                if len(lines) < 2 or not lines[1].startswith("答："):
                    continue
                qs = [
                    q.strip()
                    for q in re.split(r"[／/]", lines[0][len("问：") :])
                    if q.strip()
                ]
                ans = lines[1][len("答：") :].strip()
                if not qs or not ans:
                    continue
                # 归属地：优先用文件标题；文件无标题时从答案推断
                hdr = header or infer_city(ans)
                kind = infer_kind(qs, ans)
                pair = QAPair(qs, ans, section, hdr, kind)
                pair.raw = chunk
                pairs.append(pair)
        return pairs

    # ---- 城市

    def city_hits(self, text: str) -> List[str]:
        found: List[str] = []
        for c in self.cities:
            if c and c in text and c not in found:
                found.append(c)
        # 页面顺序即导入顺序；长名优先避免「晋中/晋城」类误配
        found.sort(key=len, reverse=True)
        return found

    # ---- 检索

    def search(self, query: str, top: int = 12) -> List[QAPair]:
        self.load()
        if not self.pairs:
            return []

        q_tokens = _tokenize(query)
        if not q_tokens:
            return []

        hits_city = self.city_hits(query)
        scored: List[Tuple[float, int, QAPair]] = []
        for idx, p in enumerate(self.pairs):
            score = _score(q_tokens, p)
            if p.header and p.header in hits_city:
                score += 6.0  # 同城加权：地名与物产的归属是这套语料的核心
            if score <= 0:
                continue
            scored.append((score, -idx, p))

        scored.sort(key=lambda x: (-x[0], x[1]))
        picked = [p for _, _, p in scored[:top]]

        # 兜底：问题点名了某府邑但没召回到该城内容时，补该城汇总问答
        if hits_city:
            have = {id(p) for p in picked}
            for city in hits_city:
                if any(p.header == city for p in picked):
                    continue
                for p in self.by_city.get(city, [])[:3]:
                    if id(p) not in have:
                        picked.append(p)
                        have.add(id(p))
        return picked[: max(top, 1) + 3]

    def city_summary_pairs(self, city: str) -> List[QAPair]:
        self.load()
        return self.by_city.get(city, [])

    @property
    def size(self) -> int:
        self.load()
        return len(self.pairs)


index = _KnowledgeIndex()


# ---------------------------------------------------------------- 词法工具

_STOP = set("的了是在有和与及或我你他她它们这那什么怎么样哪些哪个请问一下可以吗呢啊哦嗯")


def _tokenize(text: str) -> List[str]:
    """中文按二元组切分 + 英文/数字按词切分。

    二元组对中文检索效果好且实现简单（无需分词器）：
    「平遥牛肉」→ 平遥/遥牛/牛肉
    """
    if not text:
        return []
    tokens: List[str] = []
    # 英文与数字
    for w in re.findall(r"[A-Za-z0-9]+", text):
        if len(w) >= 2:
            tokens.append(w.lower())
    # 中文二元组
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        seg = "".join(ch for ch in seg if ch not in _STOP)
        if not seg:
            continue
        if len(seg) == 1:
            tokens.append(seg)
        else:
            tokens.extend(seg[i : i + 2] for i in range(len(seg) - 1))
    return tokens


def _tokenize_weighted(pair: QAPair) -> Dict[str, float]:
    """把问答对转成词→权重。问句权重高于答案（问句更接近用户表述）"""
    weights: Dict[str, float] = {}
    for q in pair.questions:
        for t in _tokenize(q):
            weights[t] = max(weights.get(t, 0.0), 1.6)
    for t in _tokenize(pair.answer):
        weights[t] = max(weights.get(t, 0.0), 1.0)
    return weights


_wcache: Dict[int, Dict[str, float]] = {}


def _score(q_tokens: List[str], pair: QAPair) -> float:
    key = id(pair)
    w = _wcache.get(key)
    if w is None:
        w = _tokenize_weighted(pair)
        _wcache[key] = w
    score = 0.0
    for t in q_tokens:
        v = w.get(t)
        if v:
            score += v
    # 归一化，避免长答案凭长度占优
    if score:
        score = score / (1.0 + 0.015 * max(0, len(pair.answer) - 60))
    return score


# ---------------------------------------------------------------- 提示词

_PROMPT_CACHE: Dict[str, str] = {}


def load_prompts() -> Tuple[str, str]:
    """从提示词文件里取出「系统提示词」与「用户提示词（版本 B）」两个代码块。

    文件是给人看的说明文档，这里只提取其中标注为 text 的代码块。
    """
    path = _resolve(settings.agent_prompt_file)
    if not path.exists():
        return _FALLBACK_SYSTEM, _FALLBACK_USER

    key = str(path)
    mtime = str(path.stat().st_mtime)
    cached = _PROMPT_CACHE.get(key + ":mtime")
    if cached == mtime and key + ":sys" in _PROMPT_CACHE:
        return _PROMPT_CACHE[key + ":sys"], _PROMPT_CACHE[key + ":usr"]

    text = path.read_text(encoding="utf-8")
    blocks = re.findall(r"```text\n(.*?)```", text, re.DOTALL)

    system = ""
    user = ""
    for b in blocks:
        if "你是「驿路谋士」" in b and not system:
            system = b
        elif "行者所问" in b and "版本" not in b and not user:
            user = b
    # 用户提示词优先取「版本 B」（单路检索，本文实现只注入一份检索结果）
    for b in blocks:
        if "【方志检索到的资料】" in b:
            user = b
            break

    if not system:
        system = _FALLBACK_SYSTEM
    if not user:
        user = _FALLBACK_USER

    _PROMPT_CACHE[key + ":mtime"] = mtime
    _PROMPT_CACHE[key + ":sys"] = system
    _PROMPT_CACHE[key + ":usr"] = user
    return system, user


_FALLBACK_SYSTEM = "你是「驿路谋士」，三晋（山西）文旅讲解使者。只答山西文旅话题，只依资料作答，不得编造。"

_FALLBACK_USER = """【方志检索到的资料】
{context}

【行者所问】
{question}

请依上方方志资料作答。资料未收录就说明「现有方志未收录」，不要编造。"""


# ---------------------------------------------------------------- 上下文渲染


def format_context(pairs: List[QAPair], max_chars: int = 6000) -> str:
    """把检索到的问答对渲染为提示词片段。

    每条都保留「问／答」结构 —— 问答对本身是最贴近用户提问的语料，
    加上文件头的回答原则，模型即可据此作答。
    """
    if not pairs:
        return "（本次未检索到相关方志条目）"
    out: List[str] = []
    used = 0
    for p in pairs:
        block = "问：" + "／".join(p.questions) + "\n答：" + p.answer
        if used + len(block) > max_chars and out:
            break
        out.append(block)
        used += len(block)
    return "\n\n".join(out)


def build_user_prompt(question: str, pairs: List[QAPair]) -> str:
    """把「检索结果 + 用户问题」装配进提示词模板。

    提示词文件是给人看的文档，变量名按讯飞节点写成 {{input}} / {{AGENT_USER_INPUT}}，
    这里做兼容替换；若模板结构与预期不符（未能注入上下文），则退回内置模板，
    避免出现「资料没传进去、模型只能凭空作答」的情况。
    """
    _, user_tpl = load_prompts()
    context = format_context(pairs)

    text = user_tpl
    context_injected = False
    question_injected = False

    # {{input}} / {{input2}} → 检索到的资料
    new = re.sub(r"\{\{\s*input2?\s*\}\}", lambda _m: context, text)
    if new != text:
        context_injected = True
        text = new
    # {{AGENT_USER_INPUT}} → 用户问题
    new = re.sub(r"\{\{\s*AGENT_USER_INPUT\s*\}\}", lambda _m: question, text)
    if new != text:
        question_injected = True
        text = new
    # 通用占位符
    if "{context}" in text:
        text = text.replace("{context}", context)
        context_injected = True
    if "{question}" in text:
        text = text.replace("{question}", question)
        question_injected = True

    if not (context_injected and question_injected):
        return _FALLBACK_USER.format(context=context, question=question)
    return text


def retrieve(question: str, top: Optional[int] = None) -> Tuple[List[QAPair], List[str]]:
    """检索入口，返回 (问答对, 命中的府邑)"""
    index.load()
    n = top or settings.agent_retrieval_top
    pairs = index.search(question, top=n)
    cities = index.city_hits(question)
    # 用 logger 而非 print：避免检索细节混进服务日志噪音
    logger.info(
        "知识库检索：命中府邑=%s 召回=%d 条 问题=%r",
        cities or "无",
        len(pairs),
        question[:40],
    )
    return pairs, cities


# ---------------------------------------------------------------- 输出清洗

_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_HEAD = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_MD_FENCE = re.compile(r"```[a-zA-Z]*\n?")
_MD_ITALIC = re.compile(r"(?<!\*)\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)")


def clean_reply(text: str) -> str:
    """去掉模型偶发的 Markdown 标记。

    提示词已要求输出纯文本，但模型仍可能自作主张加粗；前端不渲染 Markdown，
    若不清洗就会把 `**晋中**` 原样显示给用户。
    """
    if not text:
        return ""
    out = _MD_BOLD.sub(r"\1", text)
    out = _MD_HEAD.sub("", out)
    out = _MD_FENCE.sub("", out)
    out = _MD_ITALIC.sub(r"\1", out)
    # 表格分隔行（|---|---|）无意义，移除
    out = re.sub(r"^\s*\|?[\s\-:|]+\|?\s*$", "", out, flags=re.MULTILINE)
    return out.strip()
