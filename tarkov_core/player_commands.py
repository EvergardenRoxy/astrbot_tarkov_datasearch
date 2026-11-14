"""Player profiles from Atlas (the default source): one live lookup of one mode per query, drawn as a picture.

A name is settled by the plugin's shared name record, then the tarkov.dev name list, then Atlas' own search. When
Atlas fails (rather than answering "no such player"), the tarkov.dev snapshot stands in, labelled as such.
"""
import asyncio

from astrbot.api import logger
from astrbot.api.message_components import Image, Plain

from .atlas_assets import AtlasAssets, level_badge
from .atlas_card import build as build_atlas_card
from .atlas_client import NICKNAME as ATLAS_NICKNAME
from .atlas_image import render_atlas_image
from .atlas_players import AtlasNotFound, UserQuota, can_fall_back, failure_text
from .matching_image import ATLAS_URL
from .modes import label_of, page_url, path_of
from .names import SUGGESTIONS
from .snapshot_commands import ACCOUNT_ID, CACHE_NOTE_SECONDS, MODE_WORDS, ambiguous, pick
from .transport import NotFound, QueryError

# Every user's hourly allowance: Atlas queries, and how many of them may be refreshes.
QUERIES_PER_HOUR = 10
REFRESHES_PER_HOUR = 3


class PlayerCommands:
    def __init__(self, app, snapshot, weekly):
        self.app, self.snapshot, self.weekly = app, snapshot, weekly
        self.quota = UserQuota(lambda: (QUERIES_PER_HOUR, REFRESHES_PER_HOUR,
                                        app.config.int('user_query_cooldown_seconds'),
                                        app.config.int('user_refresh_cooldown_seconds')))
        self.assets = AtlasAssets(app.root/'atlas-assets', app.public_feed, proxy=app.proxy, client=app.http)

    async def start(self):
        """The two one-shot settings of the picture cache."""
        for setting, action, done in (('atlas_assets_clear', self.assets.clear, 'Tarkov Atlas pictures cleared: %s files'),
                                      ('atlas_assets_update', self.assets.prefetch, 'Tarkov Atlas common icons downloaded: %s')):
            try:
                if self.app.config.consume(setting):
                    logger.info(done, await action())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning('Tarkov %s failed: %s', setting, type(exc).__name__)

    # ---- what the help and the replies say about the rules ----

    def cache_minutes(self):
        return max(1, self.app.config.int('profile_cache_minutes'))

    def refresh_rule(self):
        gap = self.app.config.int('user_refresh_cooldown_seconds')
        return f'跳过缓存重新查询；每人每小时 {REFRESHES_PER_HOUR} 次，两次至少间隔 {max(1, round(gap/60))} 分钟'

    def source_rule(self):
        return (f'数据来自 Atlas（{ATLAS_URL}），按需实时查询，不用先在 tarkov.dev 搜索；查过的玩家缓存 '
                f'{self.cache_minutes()} 分钟，每人每小时可联网查询 {QUERIES_PER_HOUR} 次、刷新 {REFRESHES_PER_HOUR} 次。'
                'Atlas 总额度用完时暂停联网查询，到期自动恢复；缓存仍可查看。'
                'Atlas 请求故障时可改用 tarkov.dev 快照，回复里会注明。')

    # ---- the command ----

    async def run(self, event, action, mode, target, url, trace, refresh, owner):
        """Yields the replies of a query (`action` 'query') or a binding ('bind') in one mode."""
        cmd = self.app.cmd
        ticket = self.quota.ticket(owner, refresh)
        asked = where = None
        if target is None:
            bound = await asyncio.to_thread(self.app.accounts.bound, self.app.binding_key(event))
            if not bound:
                yield event.plain_result(f"你还没有绑定账号。发送 {cmd('绑定 <昵称、账号ID 或玩家页网址>')} 绑定自己。\n"
                                         f"查别人：{cmd('[模式] <昵称、账号ID 或网址>')}；完整用法：{cmd('帮助')}")
                return
            aid = bound[0]
        elif url or ACCOUNT_ID.fullmatch(target):
            aid = url[1] if url else int(target)
        else:
            aid = None
            async for found in self.resolve(event, mode, target, trace, ticket):
                if isinstance(found, tuple):
                    aid, where = found
                else:
                    yield found
            if aid is None:
                return
            asked = target
        try:
            summary = await self.app.atlas_players.profile(mode, aid, refresh=refresh, ticket=ticket)
        except AtlasNotFound as exc:
            yield event.plain_result(self.missing(exc, action, mode, aid, target))
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not can_fall_back(exc):
                raise
            logger.warning('Tarkov [%s] Atlas %s failed, using tarkov.dev: %s', trace, action, type(exc).__name__)
            if action == 'bind':
                # The account is already settled (perhaps by Atlas' search): the snapshot only confirms it.
                yield await self.snapshot.bind(event, mode, target, url, trace, aid=aid, asked=asked,
                                               note=f'{failure_text(exc)}，已改用 tarkov.dev 快照确认账号。')
            else:
                yield await self.fallback(event, mode, aid, exc, trace)
            return
        notes = []
        if asked and summary['nickname'].lower() != asked.lower():
            # The record, the name list or the cached profile may be out of date (a renamed player): Atlas' live
            # search says who has the name now.
            other, renamed, verified = await self.who_has_the_name(mode, asked, aid, summary, trace, ticket)
            if other != aid:
                notes.append(f'{where}中的 {asked} 对应账号 ID {aid}（现在叫 {summary["nickname"]}）；'
                             f'Atlas 搜索到的 {asked} 是账号 ID {other}，下面是它的档案。')
            aid, summary = other, renamed
            if not verified:
                if action == 'bind':
                    yield event.plain_result(f'无法确认昵称 {asked} 对应的账号；资料中的昵称是 {summary["nickname"]}。'
                                             '请检查昵称，或使用账号 ID / 玩家页网址绑定。')
                    return
                notes.append(f'注意：{where}中的 {asked} 对应账号 ID {aid}，档案里现在的昵称是 {summary["nickname"]}，可能改过名。')
                notes.append('本次昵称核验未通过，下面按已记录的账号 ID 展示资料。')
        if not summary.get('from_cache'):
            await self.weekly.feed(aid, mode, summary)
            await self.snapshot.remember_name(aid, summary['nickname'])
        if action == 'bind':
            await asyncio.to_thread(self.app.accounts.bind, self.app.binding_key(event), aid, summary['nickname'])
            yield event.plain_result(f"已绑定：{summary['nickname']}（账号 ID {aid}）。之后发送 {cmd()} 即可查询自己"
                                     f"（查 {label_of(self.app.default_mode())}），也可以写模式，例如 {cmd('pvp')}、{cmd('赛季')}；"
                                     f"要最新数据，在命令最后加“刷新”。")
            return
        age = summary.get('cache_age', 0)
        if summary.get('from_cache') and age >= CACHE_NOTE_SECONDS:
            notes.append(f'这是插件 {max(1, round(age/60))} 分钟前从 Atlas 取得的数据（缓存）。')
        yield await self.picture(event, summary, mode, aid, notes, trace)

    async def resolve(self, event, mode, target, trace, ticket):
        """Yields chat messages and, when the name is settled, one (account id, where it was found) tuple."""
        label = label_of(mode)
        snapshot = self.snapshot
        matches, total = await asyncio.to_thread(self.app.accounts.lookup, target)
        where = '记录'
        if not matches and snapshot.index_enabled():
            where = '名单'
            notice = snapshot.index_notice(mode)
            if notice:
                yield event.plain_result(notice)
            try:
                matches, total = await snapshot.names.lookup(path_of(mode), target)
            except (QueryError, TimeoutError) as exc:
                # The list only saves requests; Atlas' search can still find the name.
                logger.warning('Tarkov [%s] name list unavailable: %s', trace, type(exc).__name__)
                matches, total = [], 0
        logger.info('Tarkov [%s] name matches=%s source=%s', trace, total, where)
        found = pick(matches, target)
        if found:
            yield found[0], where
            return
        if not ATLAS_NICKNAME.fullmatch(target):
            yield event.plain_result(ambiguous(target, matches, total) if matches else
                                     f'记录和名单里都没有 {target}；Atlas 只能搜索 3～15 个字符的昵称（字母、数字、_ -），'
                                     '更长的名字请用账号 ID 或 tarkov.dev 玩家页网址查询。')
            return
        try:
            searched = await self.app.atlas_players.search(target, mode, ticket=ticket)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not can_fall_back(exc):
                raise
            logger.warning('Tarkov [%s] Atlas search failed: %s', trace, type(exc).__name__)
            yield event.plain_result(f'{failure_text(exc)}，暂时无法按昵称查找 {target}，请稍后再试，或改用账号 ID / 玩家页网址查询。')
            return
        folded = [entry for entry in searched if entry[1].lower() == target.lower()]
        found = pick(folded, target)
        if found:
            yield found[0], 'Atlas 搜索'
            return
        if folded or len(matches) > 1:
            listed = folded or matches
            yield event.plain_result(ambiguous(target, listed, len(listed)))
            return
        if searched:
            hint = 'Atlas 搜索到的相近名字：'+'、'.join(f'{name}（账号 ID {aid}）' for aid, name in searched[:SUGGESTIONS])+'。'
        else:
            hint = (await snapshot.suggestion(mode, target, self.app.cmd(MODE_WORDS[mode]))).removesuffix('如果都不是，')
        yield event.plain_result(f'Atlas 在 {label} 模式下没有找到名为 {target} 的玩家。{hint}'
                                 '请检查拼写，或改用账号 ID / tarkov.dev 玩家页网址查询。')

    async def who_has_the_name(self, mode, asked, aid, summary, trace, ticket):
        """(account id, its summary, whether it carries the name `asked` now), by Atlas' live search and a fresh
        lookup of the account it names. A failed check keeps the account and summary already found, unverified."""
        try:
            found = await self.app.atlas_players.search(asked, mode, ticket=ticket, live=True)
            exact = [entry for entry in found if entry[1].lower() == asked.lower()]
            if len(exact) != 1:
                return aid, summary, False
            fresh = await self.app.atlas_players.profile(mode, exact[0][0], refresh=True, ticket=ticket)
            if fresh['nickname'].lower() == asked.lower():
                return exact[0][0], fresh, True
        except asyncio.CancelledError:
            raise
        except QueryError as exc:
            logger.info('Tarkov [%s] Atlas rename check skipped: %s', trace, type(exc).__name__)
        return aid, summary, False

    def missing(self, exc, action, mode, aid, target):
        """The reply when Atlas says there is no such account or no profile in the mode."""
        if exc.no_account:
            return f'{exc}，请检查账号 ID 或昵称。'
        other = MODE_WORDS[next(m for m in ('pvp', 'pve', 'season') if m != mode)]
        if action == 'bind':
            return f"{exc}。绑定时可以写上有档案的模式，例如 {self.app.cmd('绑定', other, target or aid)}。"
        return f'{exc}。可以换个模式查询，例如 {self.app.cmd(other, aid)}。'

    async def fallback(self, event, mode, aid, exc, trace):
        """The tarkov.dev snapshot when Atlas failed, said so in the reply."""
        reason = failure_text(exc)
        try:
            summary = await self.snapshot.profile(mode, aid)
        except NotFound:
            raise QueryError(f'{reason}；tarkov.dev 上也没有账号 ID {aid} 的 {label_of(mode)} 快照，请稍后再试') from None
        except asyncio.CancelledError:
            raise
        except Exception as second:
            logger.warning('Tarkov [%s] tarkov.dev fallback failed: %s', trace, type(second).__name__)
            raise QueryError(f'{reason}；改用 tarkov.dev 快照也没有成功，请稍后再试') from None
        return await self.snapshot.reply(event, summary, mode, aid, [f'{reason}，下面是 tarkov.dev 的快照（不是实时数据）。'], trace)

    # ---- the picture ----

    async def picture(self, event, summary, mode, aid, notes, trace):
        """The player picture, then one text block: the source links, the notes and how to refresh."""
        refresh = (f"要看最新数据，在命令最后加“刷新”，例如 {self.app.cmd(MODE_WORDS[mode], aid, '刷新')}"
                   f'（每人每小时 {REFRESHES_PER_HOUR} 次）。')
        lines = [f'数据来源：Atlas {ATLAS_URL}　tarkov.dev 玩家页：{page_url(mode, aid)}', *notes, refresh]
        try:
            card = await asyncio.to_thread(build_atlas_card, summary['doc'], mode,
                                           skill_limit=self.app.config.int('skill_count'))
            # The account asked for, so the picture and the links agree whatever the document says.
            card['aid'] = aid if mode == 'arena' else str(aid)
            # Arena achievements are not in tarkov.dev's icon catalog; that picture uses text cards.
            icons = {} if mode == 'arena' else await self.icons(card, trace)
            data = await self.app.render(render_atlas_image, card, icons)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            what = f"{summary['nickname']}（账号 ID {aid}）的 {label_of(mode)} 档案"
            return event.plain_result('\n'.join([self.app.render_failure(exc, what), *lines]))
        return event.chain_result([Image.fromBytes(data), Plain('\n'.join(lines))])

    async def icons(self, card, trace):
        """The pictures the card needs from the local cache, downloading missing ones (failures draw placeholders)."""
        try:
            catalog = await self.assets.catalog_for(card['achievement_ids'])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Without tarkov.dev's public data only the item pictures (linked in Atlas' document) are known.
            logger.warning('Tarkov [%s] icon catalog unavailable: %s', trace, type(exc).__name__)
            catalog = {}
        achievements = {key: catalog.get('achievements', {}).get(key) for key in card['achievement_ids']}
        badge = level_badge(catalog, card['level'])
        prestige = catalog.get('prestige', {}).get(str(card['prestige'])) if card['prestige'] else None
        links = [*card['assets'], *achievements.values(), badge, prestige]
        found = await self.assets.images([link for link in links if link])
        return {'level_badge': found.get(badge), 'prestige': found.get(prestige), 'items': found,
                'achievements': {key: found.get(url) for key, url in achievements.items() if url}}

    async def close(self):
        await self.assets.close()
