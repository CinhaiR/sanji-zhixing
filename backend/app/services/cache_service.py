"""行程生成缓存

两级结构：
- 内存 LRU：进程内热点命中，零 IO
- 数据库 TTL：跨进程 / 跨重启持久，避免重复消耗大模型额度

缓存 key = 归一化表单参数 + 知识库指纹。
知识库一更新指纹即变，旧缓存自然失效，不会返回与当前知识库冲突的行程。
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.knowledge import GenerationCacheModel


class LRUCache:
    """线程安全的简单 LRU（带 TTL）"""

    def __init__(self, maxsize: int = 256, ttl: int = 3600):
        self.maxsize = maxsize
        self.ttl = ttl
        self._data: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            hit = self._data.get(key)
            if not hit:
                return None
            ts, value = hit
            if ts + self.ttl < datetime.now().timestamp():
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = (datetime.now().timestamp(), value)
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


_memory = LRUCache(
    maxsize=256,
    ttl=max(settings.cache_ttl_seconds, 60),
)


def _norm(value: Any) -> Any:
    """归一化：列表排序去重，字符串去空白，避免同一语义产生不同 key"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple, set)):
        return sorted({_norm(v) for v in value})
    if isinstance(value, dict):
        return {k: _norm(value[k]) for k in sorted(value)}
    return value


def build_cache_key(form: Any, fingerprint: str, extra: Optional[Dict[str, Any]] = None) -> str:
    """由表单 + 知识库指纹 + 生成参数生成缓存 key"""
    if hasattr(form, "model_dump"):
        payload = form.model_dump()
    elif isinstance(form, dict):
        payload = dict(form)
    else:
        payload = {"raw": str(form)}
    payload = _norm(payload)
    payload["_fingerprint"] = fingerprint
    payload["_model"] = settings.deepseek_model
    payload["_rag"] = settings.enable_rag
    # 输出结构版本：行程由「每活动带餐饮」改为「日级 localSpecialties + rationale」，
    # 递增此值可让旧格式缓存自然失效，无需清库。
    payload["_fmt"] = 2
    if extra:
        payload["_extra"] = _norm(extra)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class GenerationCache:
    """行程生成结果缓存门面"""

    def __init__(self, enabled: Optional[bool] = None, backend: Optional[str] = None):
        self.enabled = settings.enable_generation_cache if enabled is None else enabled
        self.backend = (backend or settings.cache_backend).lower()

    @property
    def active(self) -> bool:
        return self.enabled and self.backend != "off"

    def get(self, db: Session, key: str) -> Optional[Dict[str, Any]]:
        if not self.active:
            return None

        if self.backend in ("memory", "db"):
            cached = _memory.get(key)
            if cached is not None:
                return cached

        if self.backend != "memory":
            row = db.scalars(
                select(GenerationCacheModel).where(GenerationCacheModel.key == key)
            ).first()
            if row:
                if row.expires_at and row.expires_at < datetime.now():
                    db.delete(row)
                    db.commit()
                    return None
                try:
                    payload = json.loads(row.payload)
                except (json.JSONDecodeError, TypeError):
                    db.delete(row)
                    db.commit()
                    return None
                row.hits = (row.hits or 0) + 1
                db.commit()
                payload["_cache_hits"] = row.hits
                _memory.set(key, payload)
                return payload
        return None

    def set(
        self,
        db: Session,
        key: str,
        plan: Any,
        source: str = "llm",
        provider: str = "",
    ) -> None:
        if not self.active:
            return
        if hasattr(plan, "model_dump"):
            payload = plan.model_dump()
        else:
            payload = plan
        _memory.set(key, payload)

        if self.backend == "memory":
            return

        expires = datetime.now() + timedelta(seconds=settings.cache_ttl_seconds)
        try:
            row = db.scalars(
                select(GenerationCacheModel).where(GenerationCacheModel.key == key)
            ).first()
            if row:
                row.payload = json.dumps(payload, ensure_ascii=False)
                row.source = source
                row.provider = provider
                row.expires_at = expires
            else:
                db.add(
                    GenerationCacheModel(
                        key=key,
                        payload=json.dumps(payload, ensure_ascii=False),
                        source=source,
                        provider=provider,
                        expires_at=expires,
                    )
                )
            db.commit()
        except Exception as e:  # pragma: no cover - 缓存失败不应影响主流程
            db.rollback()
            print(f"[缓存] 写入失败（已忽略）：{e}")

    def invalidate(
        self, db: Session, key: Optional[str] = None, expired_only: bool = False
    ) -> int:
        """清理缓存：指定 key / 仅清理过期 / 全量"""
        count = 0
        if key:
            _memory._data.pop(key, None)
            if self.backend != "memory":
                count = (
                    db.execute(
                        delete(GenerationCacheModel).where(GenerationCacheModel.key == key)
                    ).rowcount
                    or 0
                )
                db.commit()
            return count
        _memory.clear()
        if self.backend != "memory":
            if expired_only:
                stmt = delete(GenerationCacheModel).where(
                    GenerationCacheModel.expires_at < datetime.now()
                )
            else:
                stmt = delete(GenerationCacheModel)
            count = db.execute(stmt).rowcount or 0
            db.commit()
        return count

    def stats(self, db: Session) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "enabled": self.enabled,
            "backend": self.backend,
            "ttl_seconds": settings.cache_ttl_seconds,
            "memory_entries": len(_memory),
            "db_entries": 0,
            "total_hits": 0,
            "expired_entries": 0,
        }
        if self.backend != "memory":
            try:
                out["db_entries"] = (
                    db.scalar(select(func.count()).select_from(GenerationCacheModel)) or 0
                )
                out["total_hits"] = (
                    db.scalar(select(func.coalesce(func.sum(GenerationCacheModel.hits), 0))) or 0
                )
                out["expired_entries"] = (
                    db.scalar(
                        select(func.count())
                        .select_from(GenerationCacheModel)
                        .where(GenerationCacheModel.expires_at < datetime.now())
                    )
                    or 0
                )
            except Exception:  # pragma: no cover
                db.rollback()
        return out


cache = GenerationCache()
