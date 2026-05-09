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

a1 = "id"
a2 = "num"
a3 = "vol"
a4 = "ccb_by"
a5 = "max"


def get_avatar(user_id: str) -> bytes:
    return f"https://q4.qlogo.cn/headimg_dl?dst_uin={user_id}&spec=640"


def makeit(group_data, target_user_id):
    return 1 if any(item.get(a1) == target_user_id for item in group_data) else 2


@register("ccb", "Koikokokokoro", "和群友赛博sex的插件PLUS：群聊白名单、管理清理、防CCB、管理员配置超级暴击、管理员额外暴击", "1.2.1")
class ccb(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.window = config.get("yw_window")
        self.threshold = config.get("yw_threshold")
        self.ban_duration = config.get("yw_ban_duration")
        self.action_times = {}
        self.ban_list = {}
        self.yw_prob = config.get("yw_probability")
        self.white_list = config.get("white_list")
        self.group_white_list = config.get("group_white_list", [])
        self.selfdo = self.config.get("self_ccb", False)
        self.crit_prob = self.config.get("crit_prob")
        self.is_log = self.config.get("is_log")
        self.super_crit_enabled = config.get("super_crit_enabled", False)
        self.super_crit_multiplier = config.get("super_crit_multiplier", 5.0)
        # 管理员额外暴击率
        self.admin_extra_crit_enabled = config.get("admin_extra_crit_enabled", False)
        self.admin_extra_crit_bonus = config.get("admin_extra_crit_bonus", 0.3)
        self.admin_extra_crit_state = {}

    def _check_group(self, group_id: str) -> bool:
        gl = [str(g) for g in self.group_white_list]
        if not gl:
            return True
        return str(group_id) in gl

    async def _is_admin(self, event: AstrMessageEvent) -> bool:
        try:
            return event.is_admin()
        except Exception:
            pass
        try:
            admins = self.context.admin_list
            return str(event.get_sender_id()) in [str(a) for a in admins]
        except Exception:
            pass
        return False

    def _recalc_max(self, item: dict):
        total_vol = float(item.get(a3, 0))
        total_num = int(item.get(a2, 0))
        ccb_by = item.get(a4, {}) or {}
        if not ccb_by or total_num <= 0:
            item[a5] = 0.0
            return
        best_id = max(ccb_by.items(), key=lambda x: x[1].get("count", 0))[0]
        best_val = round(total_vol / total_num, 2)
        for uid, info in ccb_by.items():
            if info.get("count", 0) > 0:
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
                with open(DATA_FILE, "r") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"read error: {e}")
        return {}

    def write_data(self, data):
        try:
            with open(DATA_FILE, "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"write error: {e}")

    def append_log(self, gid, eid, tid, dur, vol):
        try:
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, 'r', encoding='utf-8') as lf:
                    try:
                        logs = json.load(lf)
                        if not isinstance(logs, list):
                            logs = []
                    except Exception:
                        logs = []
            else:
                logs = []
            entry = {"group": gid, "executor": eid, "target": tid, "time": dur, "vol": str(round(float(vol), 2))}
            logs.append(entry)
            with open(LOG_FILE, 'w', encoding='utf-8') as lf:
                json.dump(logs, lf, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"append_log error: {e}")

    def _save_white_list(self):
        try:
            self.config["white_list"] = self.white_list
            self.config.save()
        except Exception as e:
            logger.warning(f"save white_list error: {e}")

    def _get_target_user_id(self, event: AstrMessageEvent) -> str:
        """解析命令目标：优先取第一个非机器人 @，未 @ 时默认发送者。"""
        self_id = str(event.get_self_id())
        return next(
            (str(seg.qq) for seg in event.get_messages()
             if isinstance(seg, Comp.At) and str(seg.qq) != self_id),
            str(event.get_sender_id())
        )

    async def _get_nickname(self, event: AstrMessageEvent, user_id: str) -> str:
        """获取用户昵称；获取失败时回退为 QQ 号。"""
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

    # ── 指令组 ────────────────────────────────────────
    @filter.command_group("ccb")
    def ccb_group(self):
        """CCB 指令组。包含 CCB、排行、查询、管理清理、防CCB与管理员超级暴击等功能。"""
        pass

    # ── /ccb ─────────────────────────────────────────
    @ccb_group.command("ccb")
    async def cmd_ccb(self, event: AstrMessageEvent):
        """对目标进行 CCB。用法：/ccb ccb [@目标]；未 @ 时默认自己。"""
        group_id = str(event.get_group_id())
        if not self._check_group(group_id):
            return

        send_id = str(event.get_sender_id())
        self_id = str(event.get_self_id())
        actor_id = send_id
        now = _time_module.time()

        ban_end = self.ban_list.get(actor_id, 0)
        if now < ban_end:
            remain = int(ban_end - now)
            m, s = divmod(remain, 60)
            yield event.plain_result(f"嘻嘻，你已经一滴不剩了，养胃还剩 {m}分{s}秒")
            return

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
            stranger_info = await event.bot.api.call_action(
                'get_stranger_info', user_id=target_user_id
            )
            nickname = stranger_info.get("nick", target_user_id)
            yield event.plain_result(f"{nickname} 的后门被后户之神霸占了，不能ccb（悲")
            return

        if target_user_id == actor_id and not self.selfdo:
            yield event.plain_result("兄啊金箔怎么还能捅到自己的啊（恼）")
            return

        duration = round(_random_module.uniform(1, 60), 2)
        V = round(_random_module.uniform(1, 100), 2)
        crit = False
        is_log = self.is_log

        crit_prob = float(self.crit_prob or 0)
        if (
            self.admin_extra_crit_enabled
            and await self._is_admin(event)
            and self.admin_extra_crit_state.get(send_id, False)
        ):
            crit_prob += float(self.admin_extra_crit_bonus or 0)
        crit_prob = max(0.0, min(1.0, crit_prob))

        if _random_module.random() < crit_prob:
            mult = 2.0
            if self.super_crit_enabled and await self._is_admin(event):
                mult = float(self.super_crit_multiplier)
            V = round(V * mult, 2)
            crit = True

        pic = get_avatar(target_user_id)

        all_data = self.read_data()
        group_data = all_data.get(group_id, [])

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

                        item[a2] = int(item.get(a2, 0)) + 1
                        item[a3] = round(float(item.get(a3, 0)) + V, 2)

                        ccb_by = item.get(a4, {}) or {}
                        if send_id in ccb_by:
                            ccb_by[send_id]["count"] = ccb_by[send_id].get("count", 0) + 1
                            ccb_by[send_id]["first"] = ccb_by[send_id].get("first", False)
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
                                total_vol = float(item.get(a3, 0))
                                total_num = int(item.get(a2, 0))
                                if total_num > 0:
                                    prev_max = round(total_vol / total_num, 2)
                            except Exception:
                                prev_max = 0.0

                        if float(V) > prev_max:
                            item[a5] = round(float(V), 2)
                            for k in ccb_by:
                                ccb_by[k]["max"] = False
                            ccb_by[send_id]["max"] = True
                        else:
                            for k in ccb_by:
                                if "max" not in ccb_by[k]:
                                    ccb_by[k]["max"] = False

                        item[a4] = ccb_by

                        crit_text = "💥 暴击！"
                        if crit and mult > 2.0:
                            crit_text = f"💥 超级暴击！(x{mult})"

                        if crit:
                            chain = [
                                Comp.Plain(f"你和{nickname}发生了{duration}min长的ccb行为，向ta注入了 {crit_text}{V:.2f}ml的生命因子"),
                                Comp.Image.fromURL(pic),
                                Comp.Plain(f"这是ta的第{item[a2]}次")
                            ]
                        else:
                            chain = [
                                Comp.Plain(f"你和{nickname}发生了{duration}min长的ccb行为，向ta注入了{V:.2f}ml的生命因子"),
                                Comp.Image.fromURL(pic),
                                Comp.Plain(f"这是ta的第{item[a2]}次")
                            ]
                        yield event.chain_result(chain)

                        if is_log:
                            try:
                                self.append_log(group_id, send_id, target_user_id, duration, V)
                            except Exception as e:
                                logger.warning(f"log error: {e}")

                        all_data[group_id] = group_data
                        self.write_data(all_data)

                        if _random_module.random() < self.yw_prob:
                            self.ban_list[actor_id] = now + self.ban_duration
                            yield event.plain_result("💥你的牛牛炸膛了！满身疮痍，再起不能（悲）")
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

                chain = [
                    Comp.Plain(f"你和{nickname}发生了{duration}min长的ccb行为，向ta注入了{V:.2f}ml的生命因子"),
                    Comp.Image.fromURL(pic),
                    Comp.Plain("这是ta的初体验")
                ]
                yield event.chain_result(chain)

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

                if is_log:
                    try:
                        self.append_log(group_id, send_id, target_user_id, duration, V)
                    except Exception as e:
                        logger.warning(f"log error: {e}")

                if _random_module.random() < self.yw_prob:
                    self.ban_list[actor_id] = now + self.ban_duration
                    yield event.plain_result("💥你的牛牛炸膛了！满身疮痍，再起不能（悲）")
                return
            except Exception as e:
                logger.error(f"error: {e}")
                yield event.plain_result("对方拒绝了和你ccb")
                return

    # ── /ccbtop ──────────────────────────────────────
    @ccb_group.command("ccbtop")
    async def cmd_ccbtop(self, event: AstrMessageEvent):
        """查看当前群被 CCB 次数排行榜 TOP5。用法：/ccb ccbtop"""
        group_id = str(event.get_group_id())
        if not self._check_group(group_id):
            return

        group_data = self.read_data().get(group_id, [])
        if not group_data:
            yield event.plain_result("当前群暂无ccb记录")
            return

        top5 = sorted(group_data, key=lambda x: int(x.get(a2, 0)), reverse=True)[:5]
        msg = "被ccb排行榜 TOP5：\\n"
        for i, r in enumerate(top5, 1):
            uid = r[a1]
            nick = uid
            if event.get_platform_name() == "aiocqhttp":
                try:
                    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                    stranger_info = await event.bot.api.call_action('get_stranger_info', user_id=uid)
                    nick = stranger_info.get("nick", nick)
                except:
                    pass
            msg += f"{i}. {nick} - 次数：{r[a2]}\\n"
        yield event.plain_result(msg)

    # ── /ccbvol ─────────────────────────────────────
    @ccb_group.command("ccbvol")
    async def cmd_ccbvol(self, event: AstrMessageEvent):
        """查看当前群累计注入量排行榜 TOP5。用法：/ccb ccbvol"""
        group_id = str(event.get_group_id())
        if not self._check_group(group_id):
            return

        group_data = self.read_data().get(group_id, [])
        if not group_data:
            yield event.plain_result("当前群暂无ccb记录")
            return

        top5 = sorted(group_data, key=lambda x: float(x.get(a3, 0)), reverse=True)[:5]
        msg = "被注入量排行榜 TOP5：\\n"
        for i, r in enumerate(top5, 1):
            uid = r[a1]
            nick = uid
            if event.get_platform_name() == "aiocqhttp":
                try:
                    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                    stranger_info = await event.bot.api.call_action('get_stranger_info', user_id=uid)
                    nick = stranger_info.get("nick", nick)
                except:
                    pass
            msg += f"{i}. {nick} - 累计注入：{float(r[a3]):.2f}ml\\n"
        yield event.plain_result(msg)

    # ── /ccbinfo ────────────────────────────────────
    @ccb_group.command("ccbinfo")
    async def cmd_ccbinfo(self, event: AstrMessageEvent):
        """查询某人的 CCB 统计信息。用法：/ccb ccbinfo [@目标]；未 @ 时查询自己。"""
        group_id = str(event.get_group_id())
        if not self._check_group(group_id):
            return

        self_id = str(event.get_self_id())
        target_user_id = self._get_target_user_id(event)

        all_data = self.read_data()
        group_data = all_data.get(group_id, [])

        record = next((r for r in group_data if r.get(a1) == target_user_id), None)
        if not record:
            yield event.plain_result("该用户暂无ccb记录")
            return

        total_num = int(record.get(a2, 0))
        total_vol = float(record.get(a3, 0))

        raw_max = record.get(a5, None)
        max_val = 0.0
        try:
            if raw_max is not None:
                max_val = float(raw_max)
            else:
                if total_num > 0:
                    max_val = round(total_vol / total_num, 2)
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

        ccb_by = record.get(a4, {})
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
            f"【{record.get(a1)} 】\n"
            f"• 破壁人：{first_nick}\n"
            f"• 北朝：{total_num}\n"
            f"• 朝壁：{cb_total}\n"
            f"• 诗经：{total_vol:.2f}ml\n"
            f"• 马克思：{max_val:.2f}ml"
        )
        yield event.plain_result(msg)

    # ── /ccbmax ─────────────────────────────────────
    @ccb_group.command("ccbmax")
    async def cmd_ccbmax(self, event: AstrMessageEvent):
        """查看当前群单次最大注入排行榜 TOP5。用法：/ccb ccbmax"""
        group_id = str(event.get_group_id())
        if not self._check_group(group_id):
            return

        group_data = self.read_data().get(group_id, [])
        if not group_data:
            yield event.plain_result("当前群暂无ccb记录")
            return

        entries = []
        for r in group_data:
            raw_max = r.get(a5, None)
            max_val = 0.0
            try:
                if raw_max is not None:
                    max_val = float(raw_max)
                else:
                    total_vol = float(r.get(a3, 0))
                    total_num = int(r.get(a2, 0))
                    if total_num > 0:
                        max_val = round(total_vol / total_num, 2)
            except Exception:
                max_val = 0.0
            entries.append((r, float(max_val)))

        entries.sort(key=lambda x: x[1], reverse=True)
        top5 = entries[:5]

        msg = "单次最大注入排行榜 TOP5：\\n"
        for i, (r, max_val) in enumerate(top5, 1):
            uid = r.get(a1)
            producer_id = None
            ccb_by = r.get(a4, {}) or {}
            for actor_id, info in ccb_by.items():
                if info.get("max"):
                    producer_id = actor_id
                    break
            if not producer_id and ccb_by:
                try:
                    producer_id = max(ccb_by.items(), key=lambda x: x[1].get("count", 0))[0]
                except Exception:
                    producer_id = None

            nick = uid
            producer_nick = producer_id or "未知"
            if event.get_platform_name() == "aiocqhttp":
                try:
                    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                    assert isinstance(event, AiocqhttpMessageEvent)
                    try:
                        stranger_info = await event.bot.api.call_action('get_stranger_info', user_id=uid)
                        nick = stranger_info.get("nick", nick)
                    except Exception:
                        pass
                    if producer_id:
                        try:
                            p_info = await event.bot.api.call_action('get_stranger_info', user_id=producer_id)
                            producer_nick = p_info.get("nick", producer_nick)
                        except Exception:
                            pass
                except Exception:
                    pass

            msg += f"{i}. {nick} - 单次最大：{max_val:.2f}ml（{producer_nick}）\\n"

        yield event.plain_result(msg)

    # ── /xnn ────────────────────────────────────────
    @ccb_group.command("xnn")
    async def cmd_xnn(self, event: AstrMessageEvent):
        """查看当前群小南梁排行榜 TOP5。用法：/ccb xnn"""
        w_num = 1.0
        w_vol = 0.1
        w_action = 0.5

        group_id = str(event.get_group_id())
        if not self._check_group(group_id):
            return

        all_data = self.read_data()
        group_data = all_data.get(group_id, [])
        if not group_data:
            yield event.plain_result("当前群暂无ccb记录")
            return

        actor_actions = {}
        for record in group_data:
            ccb_by = record.get(a4, {})
            for actor_id, info in ccb_by.items():
                actor_actions[actor_id] = actor_actions.get(actor_id, 0) + info.get("count", 0)

        ranking = []
        for record in group_data:
            uid = record.get(a1)
            num = int(record.get(a2, 0))
            vol = float(record.get(a3, 0))
            actions = actor_actions.get(uid, 0)
            xnn_value = num * w_num + vol * w_vol - actions * w_action
            ranking.append((uid, xnn_value))

        ranking.sort(key=lambda x: x[1], reverse=True)
        top5 = ranking[:5]

        msg = "💎 小南梁 TOP5 💎\\n"
        for idx, (uid, xnn_val) in enumerate(ranking[:5], 1):
            nick = uid
            if event.get_platform_name() == "aiocqhttp":
                try:
                    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                    assert isinstance(event, AiocqhttpMessageEvent)
                    info = await event.bot.api.call_action("get_stranger_info", user_id=uid)
                    nick = info.get("nick", nick)
                except:
                    pass
            msg += f"{idx}. {nick} - XNN值：{xnn_val:.2f} \\n"

        yield event.plain_result(msg)

    # ── /ccbclear (管理员) ───────────────────────────
    @ccb_group.command("ccbclear")
    async def cmd_ccbclear(self, event: AstrMessageEvent):
        """管理员指令：清除目标的被 CCB 与 CCB 他人记录。用法：/ccb ccbclear [@目标]；未 @ 时默认自己。"""
        if not await self._is_admin(event):
            yield event.plain_result("只有 AstrBot 管理员才能使用此命令")
            return

        group_id = str(event.get_group_id())
        self_id = str(event.get_self_id())
        sender_id = str(event.get_sender_id())

        target_user_id = self._get_target_user_id(event)

        target_nick = target_user_id
        if event.get_platform_name() == "aiocqhttp":
            try:
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                assert isinstance(event, AiocqhttpMessageEvent)
                stranger_info = await event.bot.api.call_action(
                    'get_stranger_info', user_id=target_user_id
                )
                target_nick = stranger_info.get("nick", target_user_id)
            except Exception:
                pass

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

        msg = (
            f"🧹 已清除 {target_nick} 的 CCB 记录：\n"
            f"• 删除自身被CCB记录：{removed_self} 条\n"
            f"• 移除朝壁他人记录：{removed_from_others} 次\n"
            f"• 相关数据已重新校准"
        )
        yield event.plain_result(msg)

    # ── /ccbnodo (管理员) ────────────────────────────
    @ccb_group.command("ccbnodo")
    async def cmd_ccbnodo(self, event: AstrMessageEvent):
        """管理员指令：切换目标防被 CCB 状态。用法：/ccb ccbnodo [@目标]；未 @ 时默认自己。"""
        if not await self._is_admin(event):
            yield event.plain_result("只有 AstrBot 管理员才能使用此命令")
            return

        self_id = str(event.get_self_id())
        sender_id = str(event.get_sender_id())

        target_user_id = self._get_target_user_id(event)

        target_nick = target_user_id
        if event.get_platform_name() == "aiocqhttp":
            try:
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                assert isinstance(event, AiocqhttpMessageEvent)
                stranger_info = await event.bot.api.call_action(
                    'get_stranger_info', user_id=target_user_id
                )
                target_nick = stranger_info.get("nick", target_user_id)
            except Exception:
                pass

        if target_user_id in self.white_list:
            self.white_list.remove(target_user_id)
            self._save_white_list()
            yield event.plain_result(f"已解除 {target_nick} 的防CCB保护，现在可以对其CCB了")
        else:
            self.white_list.append(target_user_id)
            self._save_white_list()
            yield event.plain_result(f"已将 {target_nick} 加入防CCB保护名单，任何人都不能对其CCB")

    # ── /ccbadmincrit (管理员) ───────────────────────
    @ccb_group.command("ccbadmincrit")
    async def cmd_ccbadmincrit(self, event: AstrMessageEvent):
        """管理员指令：切换自己的额外暴击率状态。用法：/ccb ccbadmincrit；需在配置中启用 admin_extra_crit_enabled。"""
        if not await self._is_admin(event):
            yield event.plain_result("只有 AstrBot 管理员才能使用此命令")
            return
        if not self.admin_extra_crit_enabled:
            yield event.plain_result("管理员额外暴击率功能未在插件配置中启用")
            return

        sender_id = str(event.get_sender_id())
        current = self.admin_extra_crit_state.get(sender_id, False)
        new_state = not current
        self.admin_extra_crit_state[sender_id] = new_state
        total_prob = float(self.crit_prob or 0) + (float(self.admin_extra_crit_bonus or 0) if new_state else 0)
        total_prob = max(0.0, min(1.0, total_prob))
        yield event.plain_result(
            f"管理员额外暴击率已{'开启' if new_state else '关闭'}（当前总暴击率：{total_prob:.2%}）"
        )
