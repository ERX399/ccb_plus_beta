# -- coding: utf-8 --
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp
from collections import deque
from astrbot.api import AstrBotConfig

import time as _time_module
import json
import random as _random_module
import os

DATA_FILE = "data/ccb.json"
LOG_FILE = "data/ccb_log.json"
DAILY_LIMIT_FILE = "data/ccb_daily_limit.json"
NODO_FILE = "data/plugin_data/ccb_plus_beta/ccbnodo.json"

a1 = "id"
a2 = "num"
a3 = "vol"
a4 = "ccb_by"
a5 = "max"


def get_avatar(user_id: str) -> bytes:
    return f"https://q4.qlogo.cn/headimg_dl?dst_uin={user_id}&spec=640"


def makeit(group_data, target_user_id):
    """判断目标记录是否已存在；兼容历史脏数据"""
    if not isinstance(group_data, list):
        return 2
    return 1 if any(isinstance(item, dict) and item.get(a1) == target_user_id for item in group_data) else 2


def _safe_int(value, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float, str)):
            return int(float(value))
    except Exception:
        pass
    return default


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float, str)):
            return float(value)
    except Exception:
        pass
    return default


def _safe_bool(value, default: bool = False) -> bool:
    """兼容 WebUI 可能传入的 bool / 数字 / 字符串布尔值"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "1", "yes", "y", "on", "启用", "开启"):
            return True
        if text in ("false", "0", "no", "n", "off", "禁用", "关闭"):
            return False
    return default


def _normalize_ccb_by(value) -> dict:
    """把 ccb_by 清洗为 {user_id: {count, first, max}} 格式"""
    if not isinstance(value, dict):
        return {}

    result = {}
    for uid, info in value.items():
        uid = str(uid).strip()
        if not uid:
            continue
        if isinstance(info, dict):
            result[uid] = {
                "count": _safe_int(info.get("count", 0), 0),
                "first": bool(info.get("first", False)),
                "max": bool(info.get("max", False))
            }
        else:
            # 兼容旧/坏数据：如 {"123": 5}
            result[uid] = {"count": _safe_int(info, 0), "first": False, "max": False}
    return result


def _normalize_group_data(group_data) -> list[dict]:
    """清洗群数据，过滤 int/str 等坏记录，避免 .get 崩溃"""
    if not isinstance(group_data, list):
        return []

    result = []
    for item in group_data:
        if not isinstance(item, dict):
            continue
        uid = str(item.get(a1, "")).strip()
        if not uid:
            continue
        item[a1] = uid
        item[a2] = _safe_int(item.get(a2, 0), 0)
        item[a3] = round(_safe_float(item.get(a3, 0), 0.0), 2)
        item[a4] = _normalize_ccb_by(item.get(a4, {}))
        item[a5] = round(_safe_float(item.get(a5, 0), 0.0), 2)
        result.append(item)
    return result


class DailyGroupLimiter:
    """模块：按群聊内每人统计每日 CCB 次数（带缓存优化）"""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self._cache = {}  # 内存缓存
        self._last_load = 0
        self._dirty = False
        self._cache_ttl = 30  # 缓存30秒

    def _today(self) -> str:
        return _time_module.strftime("%Y-%m-%d", _time_module.localtime())

    def _read(self) -> dict:
        """读取数据，优先使用缓存"""
        now = _time_module.time()
        # 如果缓存有效，直接返回
        if now - self._last_load < self._cache_ttl and self._cache:
            return self._cache
        # 否则从文件加载
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self._cache = data
                        self._last_load = now
                        return self._cache
        except Exception as e:
            logger.warning(f"read daily limit data error: {e}")
        # 如果文件读取失败但有缓存，返回缓存
        return self._cache if self._cache else {}

    def _write(self, data: dict):
        """写入数据到文件"""
        try:
            data_dir = os.path.dirname(self.file_path) or "."
            os.makedirs(data_dir, exist_ok=True)
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._cache = data
            self._last_load = _time_module.time()
            self._dirty = False
        except Exception as e:
            logger.warning(f"write daily limit data error: {e}")
            self._dirty = True

    def get_user_count(self, group_id: str, user_id: str) -> int:
        """获取用户当日使用次数"""
        try:
            data = self._read()
            today = self._today()
            today_data = data.get(today, {})
            if not isinstance(today_data, dict):
                return 0
            group_data = today_data.get(str(group_id), {})
            if not isinstance(group_data, dict):
                return 0
            return int(group_data.get(str(user_id), 0))
        except Exception:
            return 0

    def can_use(self, group_id: str, user_id: str, limit: int) -> tuple[bool, int]:
        """检查是否可以使用"""
        if limit <= 0:
            return True, 0
        used = self.get_user_count(group_id, user_id)
        return used < limit, max(0, limit - used)

    def increase(self, group_id: str, user_id: str, limit: int) -> int:
        """增加使用次数"""
        if limit <= 0:
            return 0
        try:
            data = self._read()
            today = self._today()
            if not isinstance(data.get(today), dict):
                data[today] = {}
            if not isinstance(data[today].get(str(group_id)), dict):
                data[today][str(group_id)] = {}
            group_data = data[today][str(group_id)]
            count = int(group_data.get(str(user_id), 0)) + 1
            group_data[str(user_id)] = count
            self._write(data)
            return count
        except Exception as e:
            logger.warning(f"increase daily limit error: {e}")
            return 0
@register("ccb_plus_beta", "ERX399", "和群友赛博sex的插件PLUS Beta：群聊白名单、群单独限制、默认白名单保护、管理清理、防CCB、显示设置、管理员折叠配置", "1.3.4-beta")
class ccb(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.window = max(1, _safe_int(config.get("yw_window"), 60))
        self.threshold = max(1, _safe_int(config.get("yw_threshold"), 5))
        self.ban_duration = max(1, _safe_int(config.get("yw_ban_duration"), 180))
        self.action_times = {}
        self.ban_list = {}
        self.yw_prob = max(0.0, min(1.0, _safe_float(config.get("yw_probability"), 0.1)))
        self.white_list = self._load_white_list()
        self.group_white_list = config.get("group_white_list", [])
        self.selfdo = _safe_bool(self.config.get("self_ccb", False), False)
        self._sync_default_white_list()
        self._save_white_list()
        self.crit_prob = max(0.0, min(1.0, _safe_float(self.config.get("crit_prob"), 0.2)))
        self.is_log = _safe_bool(self.config.get("is_log", False), False)

        # 管理员折叠配置（兼容旧版顶层配置）
        admin_settings = config.get("admin_settings", {}) or {}

        # 显示设置：新版位于 admin_settings.display_settings；兼容旧版顶层 display_settings
        display_settings = admin_settings.get("display_settings", config.get("display_settings", {}) or {}) or {}
        self.show_avatar = _safe_bool(display_settings.get("show_avatar", config.get("show_avatar", True)), True)
        self.use_forward_message = _safe_bool(display_settings.get("use_forward_message", config.get("use_forward_message", False)), False)
        self.forward_node_name = display_settings.get("forward_node_name", "CCB PLUS Beta")
        self.top_limit = min(100, max(1, _safe_int(
            display_settings.get("top_limit", config.get("top_limit", 10)),
            10
        )))
        self.admin_crit_multiplier_enabled = _safe_bool(admin_settings.get(
            "crit_multiplier_enabled",
            admin_settings.get("super_crit_enabled", config.get("super_crit_enabled", False))
        ), False)
        self.admin_crit_multiplier = _safe_float(
            admin_settings.get(
                "crit_multiplier",
                admin_settings.get("super_crit_multiplier", config.get("super_crit_multiplier", 5.0))
            ),
            5.0
        )

        self.admin_extra_crit_enabled = _safe_bool(admin_settings.get(
            "extra_crit_enabled",
            config.get("admin_extra_crit_enabled", False)
        ), False)
        self.admin_extra_crit_bonus = _safe_float(admin_settings.get(
            "extra_crit_bonus",
            config.get("admin_extra_crit_bonus", 0.3)
        ), 0.3)
        self.admin_min_volume = max(0.0, _safe_float(
            admin_settings.get("min_volume", config.get("admin_min_volume", 0)),
            0.0
        ))
        self.admin_exempt_yw = _safe_bool(admin_settings.get("exempt_yw", False), False)

        # 群聊单独限制配置模块
        self.group_configs = config.get("group_configs", []) or []
        self.daily_limiter = DailyGroupLimiter(DAILY_LIMIT_FILE)

    def _group_white_ids(self) -> list[str]:
        """获取有效群聊白名单，过滤 WebUI 可能产生的空项/空白项。"""
        return [str(g).strip() for g in (self.group_white_list or []) if str(g).strip()]

    def _check_group(self, group_id: str) -> bool:
        gl = self._group_white_ids()
        if not gl:
            return True
        return str(group_id).strip() in gl

    def _group_block_message(self) -> str:
        return "本群未开启 CCB 功能，请联系管理员在插件配置的群聊白名单中添加本群。"

    def _iter_group_configs(self):
        """兼容 AstrBot template_list 可能返回的 list/dict/嵌套模板结构"""
        def unwrap(item):
            if not isinstance(item, dict):
                return None
            # 常见结构：{"group_id": "...", "daily_ccb_limit": ...}
            if "group_id" in item or "daily_ccb_limit" in item:
                return item
            # 兼容模板包装：{"group_setting": {...}} / {"items": {...}} / {"value": {...}}
            for key in ("group_setting", "items", "value", "data", "config"):
                inner = item.get(key)
                if isinstance(inner, dict):
                    result = unwrap(inner)
                    if result:
                        return result
            return None

        cfg = self.group_configs or []
        if isinstance(cfg, list):
            for item in cfg:
                result = unwrap(item)
                if result:
                    yield result
        elif isinstance(cfg, dict):
            # 兼容 group_configs 本身就是直接配置：{"group_id": "...", ...}
            direct = unwrap(cfg)
            if direct:
                yield direct

            # 兼容 {"group_setting": [..]} / {"xxx": {...}} 等结构
            for item in cfg.values():
                if isinstance(item, list):
                    for sub_item in item:
                        result = unwrap(sub_item)
                        if result and result is not direct:
                            yield result
                else:
                    result = unwrap(item)
                    if result and result is not direct:
                        yield result

    def _get_group_daily_limit(self, group_id: str) -> int:
        """获取当前群每日 CCB 上限；无匹配配置或未启用则不限制"""
        gid = str(group_id).strip()
        for item in self._iter_group_configs():
            if not _safe_bool(item.get("enable", True), True):
                continue
            item_gid = str(item.get("group_id", "")).strip()
            if item_gid == gid:
                limit = _safe_int(item.get("daily_ccb_limit", 0), 0)
                return max(0, limit)
        return 0

    def _normalize_admin_ids(self, value) -> list[str]:
        """兼容 admin_qq/admin_list 的不同格式，统一转换为 QQ 号字符串列表"""
        if not value:
            return []
        if isinstance(value, (str, int)):
            raw_items = str(value).replace(",", "\n").replace("，", "\n").splitlines()
        elif isinstance(value, (list, tuple, set)):
            raw_items = value
        else:
            raw_items = [value]

        ids = []
        for item in raw_items:
            if isinstance(item, dict):
                item = item.get("qq") or item.get("id") or item.get("user_id") or item.get("admin_qq")
            uid = str(item).strip()
            if uid and uid not in ids:
                ids.append(uid)
        return ids

    def _get_admin_ids(self) -> list[str]:
        """获取 AstrBot 管理员 QQ；优先使用新版配置项 admin_qq，兼容旧 admin_list"""
        admin_ids = []
        for attr in ("admin_qq", "admin_list"):
            try:
                admin_ids.extend(self._normalize_admin_ids(getattr(self.context, attr, None)))
            except Exception:
                pass

        # 部分 AstrBot 版本会把配置挂在 context.config / context.core_config / context.astrbot_config
        for cfg_attr in ("config", "core_config", "astrbot_config"):
            try:
                cfg = getattr(self.context, cfg_attr, None)
                if isinstance(cfg, dict):
                    admin_ids.extend(self._normalize_admin_ids(cfg.get("admin_qq") or cfg.get("admin_list")))
                elif cfg is not None:
                    getter = getattr(cfg, "get", None)
                    if callable(getter):
                        admin_ids.extend(self._normalize_admin_ids(getter("admin_qq") or getter("admin_list")))
            except Exception:
                pass

        result = []
        for uid in admin_ids:
            if uid and uid not in result:
                result.append(uid)
        return result

    async def _is_admin(self, event: AstrMessageEvent) -> bool:
        try:
            if event.is_admin():
                return True
        except Exception:
            pass
        return str(event.get_sender_id()) in self._get_admin_ids()

    def _recalc_max(self, item: dict):
        if not isinstance(item, dict):
            return
        total_vol = _safe_float(item.get(a3, 0), 0.0)
        total_num = _safe_int(item.get(a2, 0), 0)
        ccb_by = _normalize_ccb_by(item.get(a4, {}))
        if not ccb_by or total_num <= 0:
            item[a5] = 0.0
            item[a4] = ccb_by
            return
        best_id = max(ccb_by.items(), key=lambda x: _safe_int(x[1].get("count", 0), 0))[0]
        best_val = round(total_vol / total_num, 2)
        for uid, info in ccb_by.items():
            if _safe_int(info.get("count", 0), 0) > 0:
                avg = round(total_vol / total_num, 2)
                if avg >= best_val:
                    best_val = avg
                    best_id = uid
        for uid in ccb_by:
            ccb_by[uid]["max"] = (uid == best_id)
        item[a5] = round(best_val, 2)
        item[a4] = ccb_by

    def read_data(self):
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.error(f"read error: {e}")
        return {}

    def _ensure_data_dir(self):
        """确保 data 目录存在，避免首次写入日志/数据失败"""
        data_dir = os.path.dirname(DATA_FILE) or "."
        os.makedirs(data_dir, exist_ok=True)

    def write_data(self, data):
        try:
            self._ensure_data_dir()
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"write error: {e}")

    def read_log(self):
        try:
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, "r", encoding="utf-8") as lf:
                    data = json.load(lf)
                    return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"read log error: {e}")
        return []

    def append_log(self, gid, eid, tid, dur, vol, extra: dict | None = None):
        try:
            logs = self.read_log()
            entry = {"group": gid, "executor": eid, "target": tid, "time": dur, "vol": str(round(float(vol), 2))}
            if isinstance(extra, dict):
                entry.update(extra)
            logs.append(entry)
            self._ensure_data_dir()
            with open(LOG_FILE, 'w', encoding='utf-8') as lf:
                json.dump(logs, lf, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"append_log error: {e}")

    def _build_group_log_records(self, group_id: str) -> tuple[list[dict], dict[str, int]]:
        """把完整日志聚合成与 ccb.json 类似的群统计结构"""
        gid = str(group_id)
        logs = self.read_log()
        target_map: dict[str, dict] = {}
        actor_actions: dict[str, int] = {}

        for entry in logs:
            if str(entry.get("group")) != gid:
                continue
            target_id = str(entry.get("target", "")).strip()
            executor_id = str(entry.get("executor", "")).strip()
            if not target_id or not executor_id:
                continue

            try:
                vol = float(entry.get("vol", 0) or 0)
            except Exception:
                vol = 0.0

            item = target_map.setdefault(target_id, {
                a1: target_id,
                a2: 0,
                a3: 0.0,
                a4: {},
                a5: 0.0,
                "_first_actor": None,
                "_max_actor": None
            })

            item[a2] = int(item.get(a2, 0)) + 1
            item[a3] = round(float(item.get(a3, 0)) + vol, 2)

            ccb_by = _normalize_ccb_by(item.get(a4, {}))
            executor_info = ccb_by.get(executor_id)
            if isinstance(executor_info, dict):
                executor_info["count"] = _safe_int(executor_info.get("count", 0), 0) + 1
                executor_info["first"] = bool(executor_info.get("first", False))
                executor_info["max"] = bool(executor_info.get("max", False))
                ccb_by[executor_id] = executor_info
            else:
                ccb_by[executor_id] = {"count": 1, "first": False, "max": False}

            if item.get("_first_actor") is None:
                item["_first_actor"] = executor_id
                ccb_by[executor_id]["first"] = True

            actor_actions[executor_id] = actor_actions.get(executor_id, 0) + 1

            current_max = float(item.get(a5, 0) or 0)
            if vol > current_max:
                item[a5] = round(vol, 2)
                item["_max_actor"] = executor_id

            item[a4] = ccb_by

        records = []
        for item in target_map.values():
            ccb_by = _normalize_ccb_by(item.get(a4, {}))
            max_actor = item.get("_max_actor")
            for actor_id in ccb_by:
                ccb_by[actor_id]["max"] = (actor_id == max_actor)
            item[a4] = ccb_by
            item.pop("_first_actor", None)
            item.pop("_max_actor", None)
            records.append(item)
        return records, actor_actions

    def _build_all_log_records(self) -> tuple[list[dict], dict[str, int], int]:
        """把完整日志聚合成全群总统计结构；返回 records、执行者次数、涉及群数量"""
        logs = self.read_log()
        target_map: dict[str, dict] = {}
        actor_actions: dict[str, int] = {}
        groups = set()

        for entry in logs:
            group_id = str(entry.get("group", "")).strip()
            target_id = str(entry.get("target", "")).strip()
            executor_id = str(entry.get("executor", "")).strip()
            if not group_id or not target_id or not executor_id:
                continue
            groups.add(group_id)

            vol = _safe_float(entry.get("vol", 0), 0.0)

            item = target_map.setdefault(target_id, {
                a1: target_id,
                a2: 0,
                a3: 0.0,
                a4: {},
                a5: 0.0,
                "_first_actor": None,
                "_max_actor": None
            })

            item[a2] = _safe_int(item.get(a2, 0), 0) + 1
            item[a3] = round(_safe_float(item.get(a3, 0), 0.0) + vol, 2)

            ccb_by = _normalize_ccb_by(item.get(a4, {}))
            executor_info = ccb_by.get(executor_id)
            if isinstance(executor_info, dict):
                executor_info["count"] = _safe_int(executor_info.get("count", 0), 0) + 1
                executor_info["first"] = bool(executor_info.get("first", False))
                executor_info["max"] = bool(executor_info.get("max", False))
                ccb_by[executor_id] = executor_info
            else:
                ccb_by[executor_id] = {"count": 1, "first": False, "max": False}

            if item.get("_first_actor") is None:
                item["_first_actor"] = executor_id
                ccb_by[executor_id]["first"] = True

            actor_actions[executor_id] = actor_actions.get(executor_id, 0) + 1

            current_max = _safe_float(item.get(a5, 0), 0.0)
            if vol > current_max:
                item[a5] = round(vol, 2)
                item["_max_actor"] = executor_id

            item[a4] = ccb_by

        records = []
        for item in target_map.values():
            ccb_by = _normalize_ccb_by(item.get(a4, {}))
            max_actor = item.get("_max_actor")
            for actor_id in ccb_by:
                ccb_by[actor_id]["max"] = (actor_id == max_actor)
            item[a4] = ccb_by
            item.pop("_first_actor", None)
            item.pop("_max_actor", None)
            records.append(item)

        return records, actor_actions, len(groups)

    def _get_group_records(self, group_id: str) -> tuple[list[dict], dict[str, int]]:

        """优先使用日志聚合；没有日志或日志不可用时回退主数据"""
        if self.is_log:
            records, actor_actions = self._build_group_log_records(group_id)
            if records:
                return records, actor_actions
        all_data = self.read_data()
        # 兼容群号 key 为字符串或整数，并确保类型正确
        group_data = all_data.get(str(group_id), [])
        if not group_data:
            try:
                group_data = all_data.get(int(group_id), [])
            except (ValueError, TypeError):
                pass
        # 确保返回的是清洗后的列表类型
        return _normalize_group_data(group_data), {}

    def _build_log_extra(self, group_data: list, target_user_id: str, executor_id: str, crit: bool = False) -> dict:
        """为完整日志补充当前统计快照，保留原日志字段并追加次数/累计等信息"""
        group_data = _normalize_group_data(group_data)
        target_record = next((r for r in group_data if isinstance(r, dict) and r.get(a1) == target_user_id), {}) or {}
        if not isinstance(target_record, dict):
            target_record = {}
        ccb_by = _normalize_ccb_by(target_record.get(a4, {}))
        executor_info = ccb_by.get(executor_id, {}) or {}
        if not isinstance(executor_info, dict):
            executor_info = {}

        executor_total_count = 0
        executor_target_count = _safe_int(executor_info.get("count", 0), 0)
        for rec in group_data:
            try:
                ccb_by = rec.get(a4, {}) or {}
                if isinstance(ccb_by, dict):
                    actor_info = ccb_by.get(executor_id, {}) or {}
                    if isinstance(actor_info, dict):
                        count = actor_info.get("count", 0) or 0
                        executor_total_count += int(count)
            except Exception:
                pass

        return {
            "target_count": _safe_int(target_record.get(a2, 0), 0),
            "target_total_vol": round(_safe_float(target_record.get(a3, 0), 0.0), 2),
            "target_max_vol": round(_safe_float(target_record.get(a5, 0), 0.0), 2),
            "executor_target_count": executor_target_count,
            "executor_total_count": executor_total_count,
            "is_first": bool(executor_info.get("first", False)),
            "is_max": bool(executor_info.get("max", False)),
            "crit": bool(crit)
        }

    def _nodo_file_path(self) -> str:
        """返回 ccbnodo 独立持久化文件路径。"""
        return NODO_FILE

    def _normalize_white_list(self, value) -> list[str]:
        """把配置/文件中的名单统一清洗成去重字符串列表。"""
        if isinstance(value, dict):
            value = value.get("white_list") if "white_list" in value else value.get("data", [])
        if isinstance(value, (str, int)):
            value = [value]
        if not isinstance(value, (list, tuple, set)):
            value = []

        result = []
        for item in value:
            uid = str(item).strip()
            if uid and uid not in result:
                result.append(uid)
        return result

    def _load_white_list(self) -> list[str]:
        """优先从 plugin_data/ccb_plus_beta/ccbnodo.json 读取名单；文件不存在时兼容旧配置。"""
        file_path = self._nodo_file_path()
        try:
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as f:
                    return self._normalize_white_list(json.load(f))
        except Exception as e:
            logger.warning(f"load ccbnodo file error: {e}")

        try:
            return self._normalize_white_list(self.config.get("white_list") or [])
        except Exception as e:
            logger.warning(f"load ccbnodo config fallback error: {e}")
            return []

    def _save_white_list(self):
        """保存 nodo 保护名单到独立文件，并同步旧配置项以兼容面板显示。"""
        self.white_list = self._normalize_white_list(self.white_list)

        try:
            file_path = self._nodo_file_path()
            os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
            tmp_path = f"{file_path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.white_list, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, file_path)
        except Exception as e:
            logger.warning(f"save ccbnodo file error: {e}")

        try:
            self.config["white_list"] = self.white_list
            self.config.save()
        except Exception as e:
            logger.warning(f"save white_list error: {e}")

    def _sync_default_white_list(self):
        """默认把 AstrBot 管理员加入 white_list，并写回配置以便面板显示"""
        changed = False
        for uid in self._get_admin_ids():
            if uid and uid not in self.white_list:
                self.white_list.append(uid)
                changed = True

        if changed:
            self._save_white_list()

    def _sync_event_bot_white_list(self, event: AstrMessageEvent):
        """事件到达后把机器人自身ID加入 white_list，并写回配置以便面板显示"""
        try:
            bot_id = str(event.get_self_id())
            if bot_id and bot_id not in self.white_list:
                self.white_list.append(bot_id)
                self._save_white_list()
        except Exception:
            pass

    def _get_target_user_id(self, event: AstrMessageEvent) -> str:
        """解析命令目标：优先取第一个非机器人 @，未 @ 时默认发送者"""
        self_id = str(event.get_self_id())
        return next(
            (str(seg.qq) for seg in event.get_messages()
             if isinstance(seg, Comp.At) and str(seg.qq) != self_id),
            str(event.get_sender_id())
        )
    async def _get_nickname(self, event: AstrMessageEvent, user_id: str) -> str:
        """获取用户昵称；获取失败时回退为 QQ 号"""
        nickname = str(user_id)
        if event.get_platform_name() == "aiocqhttp":
            try:
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                assert isinstance(event, AiocqhttpMessageEvent)
                info = await event.bot.api.call_action('get_stranger_info', user_id=user_id)
                nickname = info.get("nick", nickname)
            except Exception:
                pass
        return nickname

    def _get_top_count(self, event: AstrMessageEvent) -> int:
        """解析排行榜条数；支持 /ccbtop 20，最终限制在 1~100未提供时使用配置 top_limit"""
        count = self.top_limit
        try:
            raw = event.message_str.strip().split()
            if len(raw) >= 2:
                count = _safe_int(raw[1], self.top_limit)
        except Exception:
            pass
        return min(100, max(1, count))
    async def _send_rank_result(self, event: AstrMessageEvent, title: str, lines: list[str], count: int):
        """发送排行榜；超过 10 条强制走合并转发，失败则回退为 TOP10 普通消息"""
        msg = (title + "\n" + "\n".join(lines)).rstrip()
        force_forward = count > 10

        if (force_forward or self.use_forward_message) and event.get_platform_name() == "aiocqhttp":
            try:
                group_id = event.get_group_id()
                self_id = str(event.get_self_id())
                await event.bot.api.call_action(
                    "send_group_forward_msg",
                    group_id=group_id,
                    messages=[{
                        "type": "node",
                        "data": {
                            "name": self.forward_node_name,
                            "uin": self_id,
                            "content": [{"type": "text", "data": {"text": msg}}]
                        }
                    }]
                )
                return None
            except Exception as e:
                logger.warning(f"send rank forward message failed, fallback to TOP10 plain_result: {e}")

        if force_forward and len(lines) > 10:
            fallback_title = title.replace(f"TOP{len(lines)}", "TOP10")
            fallback_msg = (fallback_title + "\n" + "\n".join(lines[:10])).rstrip()
            return event.plain_result(fallback_msg)
        return event.plain_result(msg)

    def _record_max_value(self, record: dict) -> float:
        """获取单条统计记录的单次最大值；兼容旧数据缺失 max 的情况"""
        if not isinstance(record, dict):
            return 0.0
        raw_max = record.get(a5, None)
        if raw_max is not None:
            return _safe_float(raw_max, 0.0)
        total_num = _safe_int(record.get(a2, 0), 0)
        if total_num <= 0:
            return 0.0
        return round(_safe_float(record.get(a3, 0), 0.0) / total_num, 2)

    def _record_max_actor(self, record: dict) -> str | None:
        """获取单次最大记录生产者；没有 max 标记时回退为执行次数最多者"""
        ccb_by = _normalize_ccb_by(record.get(a4, {}) if isinstance(record, dict) else {})
        for actor_id, info in ccb_by.items():
            if info.get("max"):
                return actor_id
        if ccb_by:
            try:
                return max(ccb_by.items(), key=lambda x: _safe_int(x[1].get("count", 0), 0))[0]
            except Exception:
                return None
        return None

    def _ensure_actor_actions(self, group_data: list[dict], actor_actions: dict | None = None) -> dict[str, int]:
        """确保小南梁榜所需的执行者次数存在"""
        if actor_actions:
            return {str(k): _safe_int(v, 0) for k, v in actor_actions.items()}
        result: dict[str, int] = {}
        for record in group_data:
            ccb_by = _normalize_ccb_by(record.get(a4, {}) if isinstance(record, dict) else {})
            for actor_id, info in ccb_by.items():
                result[actor_id] = result.get(actor_id, 0) + _safe_int(info.get("count", 0), 0)
        return result
    def _rank_user_line(self, index: int, nick: str, uid: str, value_text: str) -> str:
        """格式化排行榜用户行；昵称和 QQ 分行，避免长昵称导致排版混乱。"""
        return f"{index}. {nick}\n- ({uid}) - {value_text}"

    async def _send_top_rank(self, event: AstrMessageEvent, records: list[dict], title_tpl: str, value_label: str = "次数"):
        count = self._get_top_count(event)
        top_items = sorted(records, key=lambda x: _safe_int(x.get(a2, 0), 0), reverse=True)[:count]
        lines = []
        for i, record in enumerate(top_items, 1):
            uid = str(record.get(a1, "未知"))
            nick = await self._get_nickname(event, uid)
            lines.append(self._rank_user_line(i, nick, uid, f"{value_label}：{_safe_int(record.get(a2, 0), 0)}"))
        return await self._send_rank_result(event, title_tpl.format(n=len(top_items)), lines, len(top_items))


    async def _send_vol_rank(self, event: AstrMessageEvent, records: list[dict], title_tpl: str, value_label: str = "累计注入"):
        count = self._get_top_count(event)
        top_items = sorted(records, key=lambda x: _safe_float(x.get(a3, 0), 0.0), reverse=True)[:count]
        lines = []
        for i, record in enumerate(top_items, 1):
            uid = str(record.get(a1, "未知"))
            nick = await self._get_nickname(event, uid)
            lines.append(self._rank_user_line(i, nick, uid, f"{value_label}：{_safe_float(record.get(a3, 0), 0.0):.2f}ml"))

        return await self._send_rank_result(event, title_tpl.format(n=len(top_items)), lines, len(top_items))

    async def _send_max_rank(self, event: AstrMessageEvent, records: list[dict], title_tpl: str):
        count = self._get_top_count(event)
        top_items = sorted(records, key=self._record_max_value, reverse=True)[:count]
        lines = []
        for i, record in enumerate(top_items, 1):
            uid = str(record.get(a1, "未知"))
            max_val = self._record_max_value(record)
            producer_id = self._record_max_actor(record)
            nick = await self._get_nickname(event, uid)
            producer_nick = await self._get_nickname(event, producer_id) if producer_id else "未知"
            if producer_id:
                value_text = f"单次最大：{max_val:.2f}ml；生产者：{producer_nick}({producer_id})"
            else:
                value_text = f"单次最大：{max_val:.2f}ml；生产者：{producer_nick}"
            lines.append(self._rank_user_line(i, nick, uid, value_text))

        return await self._send_rank_result(event, title_tpl.format(n=len(top_items)), lines, len(top_items))

    async def _send_xnn_rank(self, event: AstrMessageEvent, records: list[dict], actor_actions: dict | None, title_tpl: str):
        actor_actions = self._ensure_actor_actions(records, actor_actions)
        ranking = []
        for record in records:
            uid = str(record.get(a1, ""))
            if not uid:
                continue
            num = _safe_int(record.get(a2, 0), 0)
            vol = _safe_float(record.get(a3, 0), 0.0)
            actions = _safe_int(actor_actions.get(uid, 0), 0)
            ranking.append((uid, num * 1.0 + vol * 0.1 - actions * 0.5))

        count = self._get_top_count(event)
        top_items = sorted(ranking, key=lambda x: x[1], reverse=True)[:count]
        lines = []
        for i, (uid, xnn_val) in enumerate(top_items, 1):
            nick = await self._get_nickname(event, uid)
            lines.append(self._rank_user_line(i, nick, uid, f"XNN值：{xnn_val:.2f}"))

        return await self._send_rank_result(event, title_tpl.format(n=len(top_items)), lines, len(top_items))


    async def _send_ccb_result(self, event: AstrMessageEvent, texts: list[str], image_url: str | None = None):


        """发送CCB结果；支持普通消息、可选头像、可选合并转发"""
        chain = []
        for index, text in enumerate(texts):
            if text:
                chain.append(Comp.Plain(text))
            if index == 0 and self.show_avatar and image_url:
                chain.append(Comp.Image.fromURL(image_url))

        if self.use_forward_message and event.get_platform_name() == "aiocqhttp":
            try:
                group_id = event.get_group_id()
                self_id = str(event.get_self_id())
                nodes = []
                message = []
                for index, text in enumerate(texts):
                    if text:
                        message.append({"type": "text", "data": {"text": text}})
                    if index == 0 and self.show_avatar and image_url:
                        message.append({"type": "image", "data": {"file": image_url}})
                nodes.append({
                    "type": "node",
                    "data": {
                        "name": self.forward_node_name,
                        "uin": self_id,
                        "content": message
                    }
                })
                await event.bot.api.call_action(
                    "send_group_forward_msg",
                    group_id=group_id,
                    messages=nodes
                )
                return
            except Exception as e:
                logger.warning(f"send forward message failed, fallback to chain_result: {e}")

        yield event.chain_result(chain)

    # ── /ccb ─────────────────────────────────────────
    @filter.command("ccb")
    async def cmd_ccb(self, event: AstrMessageEvent):
        """对目标进行 CCB用法：/ccb [@目标]；未 @ 时默认自己"""
        try:
            group_id = str(event.get_group_id())
            if not self._check_group(group_id):
                # 关键修复：群聊未授权时静默返回，不yield任何消息
                return
            self._sync_event_bot_white_list(event)

            send_id = str(event.get_sender_id())
            self_id = str(event.get_self_id())
            actor_id = send_id
            now = _time_module.time()
            admin_exempt_yw = bool(self.admin_exempt_yw and await self._is_admin(event))

            # 关键修复：简化每日限制检查，避免文件操作卡住
            daily_limit = 0
            can_use, remain = True, 0
            if not admin_exempt_yw:
                try:
                    daily_limit = self._get_group_daily_limit(group_id)
                    if daily_limit > 0:
                        can_use, remain = self.daily_limiter.can_use(group_id, send_id, daily_limit)
                except Exception as e:
                    logger.warning(f"daily limit check error, fallback to unlimited: {e}")
                    daily_limit = 0
                    can_use, remain = True, 0

            if not can_use:
                yield event.plain_result(f"你今天在本群的 CCB 次数已达上限（{daily_limit}次），明天再来吧")
                return

            ban_end = self.ban_list.get(actor_id, 0)
            if now < ban_end and not admin_exempt_yw:
                remain = int(ban_end - now)
                m, s = divmod(remain, 60)
                yield event.plain_result(f"嘻嘻，你已经一滴不剩了，养胃还剩 {m}分{s}秒")
                return

            if not admin_exempt_yw:
                times = self.action_times.setdefault(actor_id, deque())
                while times and now - times[0] > self.window:
                    times.popleft()
                times.append(now)

                if len(times) > self.threshold:
                    self.ban_list[actor_id] = now + self.ban_duration
                    times.clear()
                    yield event.plain_result("冲得出来吗你就冲，再冲就给你折了")
                    return

            target_user_id = self._get_target_user_id(event)

            if target_user_id in self.white_list:
                nickname = await self._get_nickname(event, target_user_id)
                yield event.plain_result(f"{nickname} 的后门受保护，不能ccb😡")
                return

            if target_user_id == actor_id and not self.selfdo:
                yield event.plain_result("兄啊金箔怎么还能捅到自己的啊（恼")
                return

            duration = round(_random_module.uniform(1, 60), 2)
            V = round(_random_module.uniform(1, 100), 2)
            crit = False
            is_log = self.is_log
            is_admin_actor = await self._is_admin(event)
            crit_prob = float(self.crit_prob or 0)
            if self.admin_extra_crit_enabled and is_admin_actor:
                crit_prob += float(self.admin_extra_crit_bonus or 0)
            crit_prob = max(0.0, min(1.0, crit_prob))

            if _random_module.random() < crit_prob:
                mult = 2.0
                if self.admin_crit_multiplier_enabled and is_admin_actor:
                    mult = self.admin_crit_multiplier

                V = round(V * mult, 2)
                crit = True

            if is_admin_actor and self.admin_min_volume > 0:
                V = round(max(V, self.admin_min_volume), 2)

            pic = get_avatar(target_user_id)


            all_data = self.read_data()
            if not isinstance(all_data, dict):
                all_data = {}

            group_data = all_data.get(group_id, [])
            if not group_data:
                try:
                    group_data = all_data.get(int(group_id), [])
                except (ValueError, TypeError):
                    pass
            group_data = _normalize_group_data(group_data)
            all_data[group_id] = group_data

            mode = makeit(group_data, target_user_id)
            if mode == 1:
                try:
                    for item in group_data:
                        if item.get(a1) == target_user_id:
                            nickname = target_user_id
                            if event.get_platform_name() == "aiocqhttp":
                                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                                assert isinstance(event, AiocqhttpMessageEvent)
                                stranger_info = await event.bot.api.call_action(
                                    'get_stranger_info', user_id=target_user_id
                                )
                                nickname = stranger_info.get("nick", nickname)

                            try:
                                current_num = item.get(a2, 0)
                                if not isinstance(current_num, (int, float)):
                                    current_num = 0
                                item[a2] = int(current_num) + 1
                            except Exception:
                                item[a2] = 1
                        
                            try:
                                current_vol = item.get(a3, 0)
                                if not isinstance(current_vol, (int, float)):
                                    current_vol = 0
                                item[a3] = round(float(current_vol) + V, 2)
                            except Exception:
                                item[a3] = round(V, 2)

                            ccb_by = _normalize_ccb_by(item.get(a4, {}))
                            executor_info = ccb_by.get(send_id)
                            if isinstance(executor_info, dict):
                                executor_info["count"] = _safe_int(executor_info.get("count", 0), 0) + 1
                                executor_info["first"] = bool(executor_info.get("first", False))
                                executor_info["max"] = bool(executor_info.get("max", False))
                                ccb_by[send_id] = executor_info
                            else:
                                ccb_by[send_id] = {"count": 1, "first": False, "max": False}

                            raw_prev = item.get(a5, None)
                            prev_max = 0.0
                            if raw_prev is not None:
                                try:
                                    prev_max = float(raw_prev)
                                except (TypeError, ValueError):
                                    prev_max = 0.0
                            if prev_max == 0.0:
                                try:
                                    current_vol = item.get(a3, 0)
                                    current_num = item.get(a2, 0)
                                
                                    if not isinstance(current_vol, (int, float)):
                                        current_vol = 0
                                    if not isinstance(current_num, (int, float)):
                                        current_num = 0
                                
                                    total_vol = float(current_vol)
                                    total_num = int(current_num)
                                
                                    if total_num > 0:
                                        prev_max = round(total_vol / total_num, 2)
                                    else:
                                        prev_max = 0.0
                                except Exception:
                                    prev_max = 0.0

                            if float(V) > prev_max:
                                item[a5] = round(float(V), 2)
                                for k in list(ccb_by.keys()):
                                    if not isinstance(ccb_by.get(k), dict):
                                        ccb_by[k] = {"count": _safe_int(ccb_by.get(k), 0), "first": False, "max": False}
                                    ccb_by[k]["max"] = False
                                ccb_by.setdefault(send_id, {"count": 1, "first": False, "max": False})
                                ccb_by[send_id]["max"] = True
                            else:
                                for k in list(ccb_by.keys()):
                                    if not isinstance(ccb_by.get(k), dict):
                                        ccb_by[k] = {"count": _safe_int(ccb_by.get(k), 0), "first": False, "max": False}
                                    if "max" not in ccb_by[k]:
                                        ccb_by[k]["max"] = False

                            item[a4] = ccb_by

                            crit_text = "💥 暴击！"

                            if crit:
                                texts = [
                                    f"你和{nickname}发生了{duration}min长的ccb行为，向ta注入了 {crit_text}{V:.2f}ml的生命因子",
                                    f"这是ta的第{item[a2]}次"
                                ]
                            else:
                                texts = [
                                    f"你和{nickname}发生了{duration}min长的ccb行为，向ta注入了{V:.2f}ml的生命因子",
                                    f"这是ta的第{item[a2]}次"
                                ]
                            async for result in self._send_ccb_result(event, texts, pic):
                                yield result

                            if is_log:
                                try:
                                    self.append_log(
                                        group_id,
                                        send_id,
                                        target_user_id,
                                        duration,
                                        V,
                                        self._build_log_extra(group_data, target_user_id, send_id, crit)
                                    )
                                except Exception as e:
                                    logger.warning(f"log error: {e}")

                            all_data[group_id] = group_data
                            self.write_data(all_data)
                            self.daily_limiter.increase(group_id, send_id, daily_limit)

                            if (not admin_exempt_yw) and _random_module.random() < self.yw_prob:
                                self.ban_list[actor_id] = now + self.ban_duration
                                yield event.plain_result("💥你的牛牛炸膛了！满身疮痍，再起不能（悲")
                            return
                except Exception as e:
                    logger.error(f"error: {e}")
                    yield event.plain_result("对方拒绝了和你ccb")
                    return

            else:
                try:
                    nickname = target_user_id
                    if event.get_platform_name() == "aiocqhttp":
                        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                        assert isinstance(event, AiocqhttpMessageEvent)
                        stranger_info = await event.bot.api.call_action(
                            'get_stranger_info', user_id=target_user_id
                        )
                        nickname = stranger_info.get("nick", nickname)

                    if crit:
                        texts = [
                            f"你和{nickname}发生了{duration}min长的ccb行为，向ta注入了 💥 暴击！{V:.2f}ml的生命因子",
                            "这是ta的初体验"
                        ]
                    else:
                        texts = [
                            f"你和{nickname}发生了{duration}min长的ccb行为，向ta注入了{V:.2f}ml的生命因子",
                            "这是ta的初体验"
                        ]
                    async for result in self._send_ccb_result(event, texts, pic):
                        yield result

                    new_record = {
                        a1: target_user_id,
                        a2: 1,
                        a3: round(V, 2),
                        a4: {send_id: {"count": 1, "first": True, "max": True}},
                        a5: round(V, 2)
                    }
                    group_data.append(new_record)
                    all_data[group_id] = group_data
                    self.write_data(all_data)
                    self.daily_limiter.increase(group_id, send_id, daily_limit)

                    if is_log:
                        try:
                            self.append_log(
                                group_id,
                                send_id,
                                target_user_id,
                                duration,
                                V,
                                self._build_log_extra(group_data, target_user_id, send_id, crit)
                            )
                        except Exception as e:
                            logger.warning(f"log error: {e}")

                    if (not admin_exempt_yw) and _random_module.random() < self.yw_prob:
                        self.ban_list[actor_id] = now + self.ban_duration
                        yield event.plain_result("💥你的牛牛炸膛了！满身疮痍，再起不能（悲")
                    return
                except Exception as e:
                    logger.error(f"error: {e}")
                    yield event.plain_result("对方拒绝了和你ccb")
                    return

        except Exception as e:
            logger.error(f"error: {e}")
            yield event.plain_result("对方拒绝了和你ccb")
            return




    # ── /ccbtop ──────────────────────────────────────
    @filter.command("ccbtop")
    async def cmd_ccbtop(self, event: AstrMessageEvent):
        """查看当前群被 CCB 次数排行榜用法：/ccbtop [数量]，数量上限100"""

        group_id = str(event.get_group_id())
        if not self._check_group(group_id):
            return

        group_data, _ = self._get_group_records(group_id)
        if not group_data:
            yield event.plain_result("当前群暂无ccb记录")
            return
        result = await self._send_top_rank(event, group_data, "被ccb排行榜 TOP{n}：", "次数")
        if result:
            yield result


    # ── /ccbvol ─────────────────────────────────────
    @filter.command("ccbvol")
    async def cmd_ccbvol(self, event: AstrMessageEvent):
        """查看当前群累计注入量排行榜用法：/ccbvol [数量]，数量上限100"""

        group_id = str(event.get_group_id())
        if not self._check_group(group_id):
            return

        group_data, _ = self._get_group_records(group_id)
        if not group_data:
            yield event.plain_result("当前群暂无ccb记录")
            return
        result = await self._send_vol_rank(event, group_data, "被注入量排行榜 TOP{n}：", "累计注入")
        if result:
            yield result


    # ── /ccbinfo ────────────────────────────────────
    @filter.command("ccbinfo")
    async def cmd_ccbinfo(self, event: AstrMessageEvent):
        """查询某人的 CCB 统计信息用法：/ccbinfo [@目标]；未 @ 时查询自己"""
        group_id = str(event.get_group_id())
        if not self._check_group(group_id):
            return

        self_id = str(event.get_self_id())
        target_user_id = self._get_target_user_id(event)

        group_data, _ = self._get_group_records(group_id)

        record = next((r for r in group_data if isinstance(r, dict) and r.get(a1) == target_user_id), None)
        if not record:
            yield event.plain_result("该用户暂无ccb记录")
            return

        try:
            current_num = record.get(a2, 0)
            if not isinstance(current_num, (int, float)):
                current_num = 0
            total_num = int(current_num)
        except Exception:
            total_num = 0
            
        try:
            current_vol = record.get(a3, 0)
            if not isinstance(current_vol, (int, float)):
                current_vol = 0
            total_vol = float(current_vol)
        except Exception:
            total_vol = 0.0

        raw_max = record.get(a5, None)
        max_val = 0.0
        try:
            if raw_max is not None:
                if isinstance(raw_max, (int, float)):
                    max_val = float(raw_max)
                else:
                    max_val = 0.0
            else:
                if total_num > 0:
                    max_val = round(total_vol / total_num, 2)
                else:
                    max_val = 0.0
        except Exception:
            max_val = 0.0

        cb_total = 0
        try:
            for rec in group_data:
                by = rec.get(a4, {}) or {}
                info = by.get(target_user_id)
                if info:
                    cb_total += int(info.get("count", 0))
        except Exception:
            cb_total = 0

        ccb_by = _normalize_ccb_by(record.get(a4, {}))
        first_actor = None
        for actor_id, info in ccb_by.items():
            if info.get("first"):
                first_actor = actor_id
                break
        if not first_actor and ccb_by:
            first_actor = max(ccb_by.items(), key=lambda x: x[1].get("count", 0))[0]

        first_nick = first_actor or "未知"
        if first_actor and event.get_platform_name() == "aiocqhttp":
            try:
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                assert isinstance(event, AiocqhttpMessageEvent)
                stranger_info = await event.bot.api.call_action(
                    'get_stranger_info', user_id=first_actor
                )
                first_nick = stranger_info.get("nick", first_actor)
            except:
                pass

        msg = (
            f"【{record.get(a1)}】({target_user_id})\n"
            f"• 破壁人：{first_nick}({first_actor})\n"
            f"• 北朝：{total_num}\n"
            f"• 朝壁：{cb_total}\n"
            f"• 诗经：{total_vol:.2f}ml\n"
            f"• 马克思：{max_val:.2f}ml"
        )
        yield event.plain_result(msg)

    # ── /ccbmax ─────────────────────────────────────
    @filter.command("ccbmax")
    async def cmd_ccbmax(self, event: AstrMessageEvent):
        """查看当前群单次最大注入排行榜用法：/ccbmax [数量]，数量上限100"""

        group_id = str(event.get_group_id())
        if not self._check_group(group_id):
            return

        group_data, _ = self._get_group_records(group_id)
        if not group_data:
            yield event.plain_result("当前群暂无ccb记录")
            return
        result = await self._send_max_rank(event, group_data, "单次最大注入排行榜 TOP{n}：")
        if result:
            yield result



    # ── /xnn ────────────────────────────────────────
    @filter.command("xnn")
    async def cmd_xnn(self, event: AstrMessageEvent):
        """查看当前群小南梁排行榜用法：/xnn [数量]，数量上限100"""
        group_id = str(event.get_group_id())
        if not self._check_group(group_id):
            return

        group_data, actor_actions = self._get_group_records(group_id)
        if not group_data:
            yield event.plain_result("当前群暂无ccb记录")
            return

        result = await self._send_xnn_rank(event, group_data, actor_actions, "💎 小南梁 TOP{n} 💎")
        if result:
            yield result


    # ── 全群日志汇总榜：/ccb占位-all ──────────────────
    @filter.command("ccbtop-all")
    async def cmd_ccbtop_all(self, event: AstrMessageEvent):
        """查看全部群日志汇总的被 CCB 次数排行榜用法：/ccbtop-all [数量]，数量上限100"""
        group_data, _, group_count = self._build_all_log_records()
        if not group_data:
            yield event.plain_result("暂无完整日志记录，无法汇总全部群数据")
            return
        result = await self._send_top_rank(event, group_data, f"全群被ccb日志汇总 TOP{{n}}（{group_count}群）：", "总次数")
        if result:
            yield result


    @filter.command("ccbvol-all")
    async def cmd_ccbvol_all(self, event: AstrMessageEvent):
        """查看全部群日志汇总的累计注入量排行榜用法：/ccbvol-all [数量]，数量上限100"""
        group_data, _, group_count = self._build_all_log_records()
        if not group_data:
            yield event.plain_result("暂无完整日志记录，无法汇总全部群数据")
            return
        result = await self._send_vol_rank(event, group_data, f"全群注入量日志汇总 TOP{{n}}（{group_count}群）：", "总注入")
        if result:
            yield result


    @filter.command("ccbmax-all")
    async def cmd_ccbmax_all(self, event: AstrMessageEvent):
        """查看全部群日志汇总的单次最大注入排行榜用法：/ccbmax-all [数量]，数量上限100"""
        group_data, _, group_count = self._build_all_log_records()
        if not group_data:
            yield event.plain_result("暂无完整日志记录，无法汇总全部群数据")
            return
        result = await self._send_max_rank(event, group_data, f"全群单次最大日志汇总 TOP{{n}}（{group_count}群）：")
        if result:
            yield result


    @filter.command("xnn-all")
    async def cmd_xnn_all(self, event: AstrMessageEvent):
        """查看全部群日志汇总的小南梁排行榜用法：/xnn-all [数量]，数量上限100"""
        group_data, actor_actions, group_count = self._build_all_log_records()
        if not group_data:
            yield event.plain_result("暂无完整日志记录，无法汇总全部群数据")
            return

        result = await self._send_xnn_rank(event, group_data, actor_actions, f"💎 全群小南梁日志汇总 TOP{{n}}（{group_count}群） 💎")
        if result:
            yield result

    # ── /ccbclear (管理员) ───────────────────────────
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("ccbclear")
    async def cmd_ccbclear(self, event: AstrMessageEvent):
        """管理员指令：清除目标的被 CCB 与 CCB 他人记录。用法：/ccbclear [@目标]；未 @ 时默认自己。"""
        if not await self._is_admin(event):
            yield event.plain_result("只有 AstrBot 管理员才能使用此命令")
            return

        group_id = str(event.get_group_id())
        target_user_id = self._get_target_user_id(event)
        target_nick = await self._get_nickname(event, target_user_id)

        all_data = self.read_data()

        group_data = all_data.get(group_id, [])

        before_len = len(group_data)
        group_data = [r for r in group_data if r.get(a1) != target_user_id]
        removed_self = before_len - len(group_data)

        removed_from_others = 0
        modified_list = []
        for rec in group_data:
            ccb_by = rec.get(a4, {}) or {}
            if target_user_id in ccb_by:
                removed_from_others += int(ccb_by[target_user_id].get("count", 0))
                del ccb_by[target_user_id]
                rec[a4] = ccb_by
                modified_list.append(rec)

        for rec in modified_list:
            rec[a2] = sum(info.get("count", 0) for info in (rec.get(a4, {}) or {}).values())
            self._recalc_max(rec)

        all_data[group_id] = group_data if group_data else all_data.pop(group_id, None) or group_data
        if not group_data:
            all_data.pop(group_id, None)
        self.write_data(all_data)

        removed_log = 0
        try:
            logs = self.read_log()
            new_logs = []
            for entry in logs:
                same_group = str(entry.get("group")) == group_id
                related_user = str(entry.get("target")) == target_user_id or str(entry.get("executor")) == target_user_id
                if same_group and related_user:
                    removed_log += 1
                    continue
                new_logs.append(entry)
            if removed_log:
                self._ensure_data_dir()
                with open(LOG_FILE, "w", encoding="utf-8") as lf:
                    json.dump(new_logs, lf, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"clear log error: {e}")

        msg = (
            f"🧹 已清除 {target_nick} 的 CCB 记录：\n"
            f"• 删除自身被CCB记录：{removed_self} 条\n"
            f"• 移除朝壁他人记录：{removed_from_others} 次\n"
            f"• 移除完整日志记录：{removed_log} 条\n"
            f"• 相关数据已重新校准"
        )
        yield event.plain_result(msg)
    async def _send_ccbnodo_list(self, event: AstrMessageEvent):
        """查看当前 nodo 保护名单（排除管理员和bot），以合并转发发送。"""
        admin_ids = set(self._get_admin_ids())
        try:
            bot_id = str(event.get_self_id())
        except Exception:
            bot_id = ""

        display_list = [uid for uid in self.white_list if uid not in admin_ids and uid != bot_id]

        if not display_list:
            yield event.plain_result("当前 nodo 保护名单为空（已排除管理员和bot）")
            return

        lines = []
        for uid in display_list:
            nick = await self._get_nickname(event, uid)
            lines.append(f"{nick}({uid})")

        msg = "🛡️ Nodo 保护名单：\n" + "\n".join(lines)

        if event.get_platform_name() == "aiocqhttp":
            try:
                group_id = event.get_group_id()
                self_id = str(event.get_self_id())
                await event.bot.api.call_action(
                    "send_group_forward_msg",
                    group_id=group_id,
                    messages=[{
                        "type": "node",
                        "data": {
                            "name": self.forward_node_name,
                            "uin": self_id,
                            "content": [{"type": "text", "data": {"text": msg}}]
                        }
                    }]
                )
                return
            except Exception as e:
                logger.warning(f"send nodo list forward message failed, fallback: {e}")
        yield event.plain_result(msg)

    # ── /ccbnodo (所有人) ────────────────────────────
    @filter.command("ccbnodo")
    async def cmd_ccbnodo(self, event: AstrMessageEvent):
        """切换目标防被 CCB 状态。用法：/ccbnodo [@目标] 或 /ccbnodo list；普通用户只能切换自己。"""
        raw_parts = (event.message_str or "").strip().split()
        if len(raw_parts) >= 2 and raw_parts[1].lower() == "list":
            async for result in self._send_ccbnodo_list(event):
                yield result
            return

        target_user_id = self._get_target_user_id(event)
        sender_id = str(event.get_sender_id())
        is_admin = await self._is_admin(event)

        if not is_admin and target_user_id != sender_id:
            yield event.plain_result("非管理员只能切换自己的防CCB保护")
            return

        target_nick = await self._get_nickname(event, target_user_id)
        if target_user_id in self.white_list:
            self.white_list.remove(target_user_id)
            self._save_white_list()
            yield event.plain_result(f"已解除 {target_nick} 的防CCB保护，现在可以对其CCB了")
        else:
            self.white_list.append(target_user_id)
            self._save_white_list()
            yield event.plain_result(f"已将 {target_nick} 加入防CCB保护名单，任何人都不能对其CCB")
