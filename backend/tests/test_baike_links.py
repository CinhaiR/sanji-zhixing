"""百度百科「词条直链」表与知识库的一致性测试。

`frontend/src/data/baikeLinks.ts` 以**景点名称**为键。
一旦知识库改名而没同步这张表，直链就会静默失效（回退成按名称检索，
可能又跳到同名异地的词条）—— 所以这里做跨语言校验。

背景：这些词条同名多义项（「雁门关」有多个义项、「南禅寺」无锡那座更知名），
只按名称检索不可靠，必须用 `/item/<词条名>/<词条ID>` 直链。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SEED = ROOT / "backend" / "data" / "knowledge_seed.json"
LINK_TS = ROOT / "frontend" / "src" / "data" / "baikeLinks.ts"

# 逐条匹配   '词条名':\n    'https://...'
_ENTRY = re.compile(r"'([^']+)':\s*\n\s*'(https://[^']+)'")


def load_links() -> dict:
    if not LINK_TS.exists():
        return {}
    return dict(_ENTRY.findall(LINK_TS.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def names() -> set:
    data = json.loads(SEED.read_text(encoding="utf-8"))
    return {a["name"] for a in data["attractions"]}


def test_link_table_not_empty():
    assert load_links(), "词条直链表为空或解析失败"


def test_every_link_key_exists_in_knowledge_base(names: set):
    """直链表的每个键都必须是知识库中真实存在的景点名。"""
    orphans = [k for k in load_links() if k not in names]
    assert not orphans, (
        f"以下直链的键在知识库中不存在（多半是改名后忘了同步）：{orphans}"
    )


def test_links_are_canonical_item_urls():
    """必须是规范的 `/item/<词条名>/<词条ID>`，不带 fromModule 等跟踪参数。"""
    bad = []
    for key, url in load_links().items():
        if not re.search(r"^https://baike\.baidu\.com/item/.+/\d+$", url):
            bad.append(f"{key} → {url}")
    assert not bad, f"以下链接不是规范的词条直链：{bad}"


def test_link_name_matches_key():
    """URL 里的词条名应与键（景点名）一致，避免复制错链接。"""
    import urllib.parse

    bad = []
    for key, url in load_links().items():
        m = re.search(r"/item/([^/]+)/\d+$", url)
        if not m:
            continue
        lemma = urllib.parse.unquote(m.group(1))
        if lemma != key:
            bad.append(f"{key} → 词条名却是「{lemma}」")
    assert not bad, f"键与词条名不一致：{bad}"


@pytest.mark.parametrize(
    "name",
    ["青莲寺", "神头岭纪念碑", "和顺县八路军石拐会议纪念园", "南禅寺", "雁门关", "冠山书院"],
)
def test_user_verified_lemmas_present(name: str, names: set):
    """用户逐个核对过的词条：名称已对齐词条本名，且配了直链。"""
    assert name in names, f"知识库中缺少景点「{name}」"
    assert name in load_links(), f"「{name}」缺少词条直链（同名多义项，必须直链）"
