"""移动端导航可达性的静态守卫。

背景（用户实际报的 bug）：手机上打开「驿路谋士」后**没有任何导航栏**，
无法进入其它功能页 —— 因为 `App.vue` 里写了

    const showBottomBar = computed(() => activeName.value !== 'agent')

即「驿路谋士页隐藏底部标签栏」。而移动端的顶部导航是 `hidden lg:flex`（桌面专用），
汉堡菜单又早已移除，于是那一页成了死胡同：进得来、出不去。

这类 bug 的共性是「某个页面拿掉了全局导航」，用运行时测试要起浏览器才测得到，
这里改用**静态断言**把两条底线固化下来：

1. `showBottomBar` 不得按路由做条件隐藏（导航必须常驻）；
2. 路由表里每个需要登录的页面，都必须在导航项 `navItems` 里有一个入口
   （防止以后新增页面却忘了放进导航，又造出一个死胡同）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

FRONTEND_SRC = Path(__file__).resolve().parent.parent.parent / "frontend" / "src"
APP_VUE = FRONTEND_SRC / "App.vue"
ROUTER = FRONTEND_SRC / "router" / "index.ts"


@pytest.fixture(scope="module")
def app_src() -> str:
    assert APP_VUE.exists(), f"找不到 {APP_VUE}"
    return APP_VUE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def router_src() -> str:
    assert ROUTER.exists(), f"找不到 {ROUTER}"
    return ROUTER.read_text(encoding="utf-8")


# ---------------------------------------------------------------- 底线 1


def test_bottom_bar_not_hidden_per_route(app_src: str):
    """底部导航不得按路由隐藏 —— 它是移动端唯一的主导航入口。

    用「整行判定」而不是正则抠表达式：`computed(() => x)` 里的括号会让
    `\\(([^)]*)\\)` 只捕获到第一个 `(`，从而永远匹配不到 `activeName`（写这测试时真踩过）。
    """
    line = next((l for l in app_src.splitlines() if "const showBottomBar" in l), None)
    assert line, "找不到 showBottomBar 的定义"
    assert "activeName" not in line and "route" not in line, (
        f"showBottomBar 依赖了当前路由 —— 这会让某个页面没有导航入口。\n"
        f"    问题行：{line.strip()}\n"
        f"    移动端顶部导航是 hidden lg:flex（桌面专用），底部栏一藏，用户就出不去了。\n"
        f"    若某页嫌导航占地方，应当由该页扣掉 --bottom-bar-h 的高度，而不是藏掉导航。"
    )


def test_bottom_bar_rendered_for_all_pages(app_src: str):
    """底部标签栏的渲染条件里不得出现路由名。

    只看绑定 `showBottomBar` 的那一行 —— 不能按「第一个 <nav>」去找，
    页面里桌面导航的 `<nav class="hidden lg:flex ...">` 排在底部栏之前。
    """
    vif_lines = [
        l.strip() for l in app_src.splitlines()
        if "showBottomBar" in l and "v-if" in l
    ]
    assert vif_lines, '底部标签栏应当绑在 v-if="showBottomBar" 上'
    assert len(vif_lines) == 1, f"出现多处底部栏渲染条件：{vif_lines}"
    line = vif_lines[0]
    tail = line.split("showBottomBar", 1)[1]
    assert "'" not in tail and '"' in tail, f"渲染条件里出现了路由名判断：{line}"


# ---------------------------------------------------------------- 底线 2


def _route_entries(router_src: str) -> list[dict]:
    """从路由表里抽出 {name, path, public} 列表（正则解析，够用且无依赖）。"""
    out = []
    for block in re.finditer(r"\{\s*path:\s*'([^']+)'[\s\S]{0,400}?\}", router_src):
        text = block.group(0)
        path = block.group(1)
        name = re.search(r"name:\s*'([^']+)'", text)
        is_public = "public: true" in text
        if name:
            out.append({"path": path, "name": name.group(1), "public": is_public})
    return out


def _nav_item_names(app_src: str) -> set:
    m = re.search(r"const navItems\s*=\s*\[([\s\S]*?)\]", app_src)
    assert m, "找不到 navItems 定义"
    return set(re.findall(r"name:\s*'([^']+)'", m.group(1)))


def test_every_page_has_a_nav_entry(app_src: str, router_src: str):
    """除登录页外，每个路由都必须出现在 navItems 里，否则又是一个死胡同。"""
    nav = _nav_item_names(app_src)
    routes = _route_entries(router_src)
    assert routes, "没能从路由表解析出任何路由（正则可能需要更新）"

    missing = [
        r["name"]
        for r in routes
        if not r["public"] and r["name"] not in nav
    ]
    assert not missing, (
        f"以下页面没有导航入口，移动端进去就出不来：{missing}。"
        "请把它们加入 App.vue 的 navItems（并确认底部标签栏放得下）。"
    )


def test_nav_items_all_exist_in_router(app_src: str, router_src: str):
    """反过来：navItems 里的每一项都必须是真实路由，避免点了没反应。"""
    nav = _nav_item_names(app_src)
    routes = {r["name"] for r in _route_entries(router_src)}
    bogus = sorted(nav - routes)
    assert not bogus, f"navItems 里有不存在的路由：{bogus}"


def test_bottom_bar_items_cover_all_nav_items(app_src: str):
    """底部标签栏不得只放部分导航项（那会让剩下的页面在移动端无入口）。"""
    nav = _nav_item_names(app_src)
    m = re.search(r"const bottomItems\s*=\s*(.+)", app_src)
    assert m, "找不到 bottomItems 定义"
    expr = m.group(1).strip()
    if expr.startswith("navItems"):
        return  # 直接复用全部导航项，最稳
    # 若是 filter(...includes([...])) 形式，则提取白名单并比对
    listed = set(re.findall(r"'([^']+)'", expr))
    assert listed >= nav, (
        f"bottomItems 只包含 {sorted(listed)}，缺少 {sorted(nav - listed)} —— "
        "移动端没有顶部导航，底部栏必须放全"
    )
