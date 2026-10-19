"""验证码与注册限流服务

- 图形验证码：随机字符 + PIL 生成图片（base64 返回），内存存储带过期时间
- 注册限流：同 IP 在时间窗口内最多注册 N 次，防止批量刷号
"""
from __future__ import annotations

import base64
import io
import random
import string
import time
from typing import Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont

# 验证码字符集：去掉易混淆的 0/O、1/I/l
_CAPTCHA_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CAPTCHA_LENGTH = 4
_CAPTCHA_TTL = 300  # 5 分钟有效

# 注册限流：同 IP 每分钟最多 3 次
_RATE_LIMIT_WINDOW = 60  # 秒
_RATE_LIMIT_MAX = 3

# 内存存储（生产环境可换 Redis）
_captchas: Dict[str, Dict] = {}
_ip_registrations: Dict[str, List[float]] = {}


def _cleanup_expired() -> None:
    """清理过期的验证码与限流记录（懒清理）"""
    now = time.time()
    expired_keys = [k for k, v in _captchas.items() if v["expire_at"] < now]
    for k in expired_keys:
        _captchas.pop(k, None)

    for ip in list(_ip_registrations.keys()):
        _ip_registrations[ip] = [
            t for t in _ip_registrations[ip] if now - t < _RATE_LIMIT_WINDOW
        ]
        if not _ip_registrations[ip]:
            _ip_registrations.pop(ip, None)


def generate_captcha() -> Dict[str, str]:
    """生成图形验证码。

    返回 {captcha_id, image_base64}，图片为 PNG 的 base64（不含 data: 前缀）。
    """
    _cleanup_expired()
    text = "".join(random.choice(_CAPTCHA_CHARS) for _ in range(_CAPTCHA_LENGTH))
    captcha_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=16))

    # 生成图片
    width, height = 120, 44
    img = Image.new("RGB", (width, height), (246, 239, 226))  # 宣纸底色
    draw = ImageDraw.Draw(img)

    # 干扰线
    for _ in range(4):
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        x2 = random.randint(0, width)
        y2 = random.randint(0, height)
        color = (
            random.randint(150, 200),
            random.randint(140, 180),
            random.randint(120, 160),
        )
        draw.line([(x1, y1), (x2, y2)], fill=color, width=1)

    # 干扰点
    for _ in range(40):
        x = random.randint(0, width)
        y = random.randint(0, height)
        draw.point((x, y), fill=(random.randint(100, 180),) * 3)

    # 绘制字符
    try:
        font = ImageFont.truetype("arial.ttf", 26)
    except (OSError, IOError):
        font = ImageFont.load_default()

    for i, ch in enumerate(text):
        x = 14 + i * 26
        y = random.randint(4, 10)
        color = (
            random.randint(40, 90),
            random.randint(30, 70),
            random.randint(30, 70),
        )
        # 轻微旋转
        char_img = Image.new("RGBA", (30, 40), (255, 255, 255, 0))
        char_draw = ImageDraw.Draw(char_img)
        char_draw.text((2, 2), ch, fill=color, font=font)
        char_img = char_img.rotate(random.randint(-20, 20), expand=1)
        img.paste(char_img, (x, y), char_img)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    image_base64 = base64.b64encode(buf.getvalue()).decode("ascii")

    _captchas[captcha_id] = {
        "text": text,
        "expire_at": time.time() + _CAPTCHA_TTL,
    }
    return {"captcha_id": captcha_id, "image": image_base64}


def verify_captcha(captcha_id: str, captcha_text: str) -> bool:
    """校验验证码（不区分大小写），校验后立即失效（一次性）"""
    _cleanup_expired()
    item = _captchas.pop(captcha_id, None)
    if not item:
        return False
    if item["expire_at"] < time.time():
        return False
    return captcha_text.strip().upper() == item["text"]


def check_rate_limit(ip: str) -> bool:
    """检查该 IP 是否还能注册。返回 True 表示允许，False 表示已达上限。"""
    _cleanup_expired()
    now = time.time()
    records = _ip_registrations.get(ip, [])
    records = [t for t in records if now - t < _RATE_LIMIT_WINDOW]
    if len(records) >= _RATE_LIMIT_MAX:
        _ip_registrations[ip] = records
        return False
    records.append(now)
    _ip_registrations[ip] = records
    return True


def get_client_ip(request) -> str:
    """从请求中提取客户端 IP（兼容反向代理）"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
