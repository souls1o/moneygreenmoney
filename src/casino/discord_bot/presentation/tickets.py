from __future__ import annotations

import asyncio
import io
import re
from datetime import timezone

import discord

from casino.container import AppContainer
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.domain.enums import TicketCategory
from casino.domain.records import GuildConfigRecord, TicketRecord
from casino.errors import TicketError
from casino.logging.logger import get_logger

_SLUG = re.compile(r"[^a-z0-9]+")


class TicketPresenter:
    def __init__(self, bot: discord.Client, container: AppContainer) -> None:
        self._bot = bot
        self._container = container
        self._embeds = EmbedFactory(container)
        self._logger = get_logger("casino.discord.ticket")

    async def ensure_panel(self, guild_id: str) -> discord.Message | None:
        config = await self._container.guild_configs.get(guild_id)
        if not config.support_channel_id:
            return None
        guild = self._bot.get_guild(int(guild_id))
        if guild is None:
            return None
        channel = guild.get_channel(int(config.support_channel_id))
        if not isinstance(channel, discord.TextChannel):
            return None
        panel = self._embeds.support_hub()
        if config.support_message_id:
            try:
                existing = await channel.fetch_message(int(config.support_message_id))
                await existing.edit(**panel.edit_kwargs())
                panel.stop()
                return existing
            except discord.HTTPException:
                self._logger.info("support_panel_repost", guildId=guild_id)
        posted = await channel.send(**panel.send_kwargs())
        panel.stop()
        await self._container.guild_configs.set_support_message(guild_id, str(posted.id))
        return posted

    async def open_ticket(
        self,
        interaction: discord.Interaction,
        category: TicketCategory,
        subject: str,
        body: str,
    ) -> TicketRecord:
        if interaction.guild is None:
            raise TicketError("Open tickets in the casino server.")
        guild_id = str(interaction.guild.id)
        config = await self._container.guild_configs.get(guild_id)
        if not config.support_category_id:
            raise TicketError("Ask an admin to set the support category in `/admin config`.")
        ticket = await self._container.tickets.create(
            guild_id=guild_id,
            opener_id=str(interaction.user.id),
            category=category,
            subject=subject,
            body=body,
        )
        try:
            channel = await self._create_channel(interaction.guild, ticket, interaction.user, config)
        except Exception:
            await self._container.tickets.close(ticket.ticket_id, str(interaction.user.id), "Channel could not be created.")
            raise
        if channel is None:
            await self._container.tickets.close(ticket.ticket_id, str(interaction.user.id), "Channel could not be created.")
            raise TicketError("I could not create a private ticket channel. Ask an admin to check bot permissions.")
        ticket = await self._container.tickets.attach_channel(ticket.ticket_id, str(channel.id))
        await self._post_room(channel, ticket, config)
        return ticket

    def is_staff(self, member: discord.Member, config: GuildConfigRecord) -> bool:
        role_ids = [role.id for role in member.roles]
        if self._container.permissions.is_admin(member.id, role_ids):
            return True
        if member.guild_permissions.administrator:
            return True
        if config.support_role_id and any(str(role.id) == config.support_role_id for role in member.roles):
            return True
        return False

    def format_transcript(self, ticket: TicketRecord) -> str:
        lines = [
            f"Ticket transcript · #{ticket.number}",
            f"Topic {ticket.category.label()}",
            f"Subject {ticket.subject}",
            f"Opened by {ticket.opener_id}",
            f"Channel {ticket.channel_id or '-'}",
            "",
            "--- Opening ---",
            ticket.body,
            "",
            "--- Chat ---",
        ]
        for event in ticket.chat_log:
            stamp = _event_stamp(event.get("at"))
            author = f"{event.get('authorName') or 'unknown'} ({event.get('authorId') or '?'})"
            kind = str(event.get("kind") or "created")
            text = str(event.get("content") or "")
            attachments = event.get("attachments") or []
            if attachments:
                text = (text + " " if text else "") + " ".join(str(item) for item in attachments)
            if kind == "edited":
                previous = str(event.get("previousContent") or "")
                lines.append(f"[{stamp}] {author} edited: {text}".rstrip())
                if previous:
                    lines.append(f"    was: {previous}")
            elif kind == "deleted":
                lines.append(f"[{stamp}] {author} deleted: {text}".rstrip() or f"[{stamp}] {author} deleted a message")
            else:
                lines.append(f"[{stamp}] {author}: {text}".rstrip())
        return "\n".join(lines) + "\n"

    async def record_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        event = _chat_event("created", message)
        await self._container.tickets.append_chat(str(message.channel.id), event)

    async def record_edit(self, before: discord.Message | None, after: discord.Message) -> None:
        if after.guild is None:
            return
        event = _chat_event("edited", after, previous=before)
        await self._container.tickets.append_chat(str(after.channel.id), event)

    async def record_delete(self, message: discord.Message | None, *, channel_id: int, message_id: int) -> None:
        if message is not None:
            event = _chat_event("deleted", message)
        else:
            event = {
                "kind": "deleted",
                "messageId": str(message_id),
                "authorId": "",
                "authorName": "unknown",
                "content": "(content was not cached)",
                "previousContent": "",
                "attachments": [],
                "at": discord.utils.utcnow(),
            }
        await self._container.tickets.append_chat(str(channel_id), event)

    async def handle_close(self, interaction: discord.Interaction, ticket_id: str, reason: str) -> TicketRecord:
        ticket = await self._container.tickets.get(ticket_id)
        config = await self._container.guild_configs.get(ticket.guild_id)
        actor_id = str(interaction.user.id)
        staff = isinstance(interaction.user, discord.Member) and self.is_staff(interaction.user, config)
        if actor_id != ticket.opener_id and not staff:
            raise TicketError("Only the opener or support staff can close this ticket.")
        ticket = await self._container.tickets.close(ticket_id, actor_id, reason)
        await self.close_channel(ticket, interaction.user, reason)
        return ticket

    async def close_channel(self, ticket: TicketRecord, actor: discord.abc.User, reason: str) -> None:
        guild = self._bot.get_guild(int(ticket.guild_id))
        if guild is None or not ticket.channel_id:
            return
        channel = guild.get_channel(int(ticket.channel_id))
        config = await self._container.guild_configs.get(ticket.guild_id)
        latest = ticket
        try:
            latest = await self._container.tickets.get(ticket.ticket_id)
        except Exception:
            latest = ticket
        if isinstance(channel, discord.TextChannel):
            latest = await self._snapshot_history(channel, latest)
        transcript = self.format_transcript(latest)
        await self._post_log(guild, config, latest, actor, reason, transcript)
        if isinstance(channel, discord.TextChannel):
            closed = self._embeds.ticket_closed(latest, str(actor.id), reason)
            try:
                await channel.send(**closed.send_kwargs())
                closed.stop()
            except discord.HTTPException:
                closed.stop()
            self._bot.loop.create_task(self._delete_later(channel, ticket.ticket_id))

    async def _create_channel(
        self,
        guild: discord.Guild,
        ticket: TicketRecord,
        opener: discord.abc.User,
        config: GuildConfigRecord,
    ) -> discord.TextChannel | None:
        category = guild.get_channel(int(config.support_category_id)) if config.support_category_id else None
        if not isinstance(category, discord.CategoryChannel):
            raise TicketError("The support category is missing. Ask an admin to set it again.")
        me = guild.me
        overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
        }
        opener_member = guild.get_member(opener.id)
        if opener_member is not None:
            overwrites[opener_member] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                attach_files=True,
                embed_links=True,
                read_message_history=True,
            )
        if config.support_role_id:
            role = guild.get_role(int(config.support_role_id))
            if role is not None:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    attach_files=True,
                    embed_links=True,
                    read_message_history=True,
                    manage_messages=True,
                )
        if me is not None:
            overwrites[me] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                attach_files=True,
                embed_links=True,
                manage_channels=True,
                manage_messages=True,
                read_message_history=True,
            )
        name = _channel_name(ticket.number, opener.name)
        try:
            return await guild.create_text_channel(
                name=name,
                category=category,
                overwrites=overwrites,
                topic=f"#{ticket.number} {ticket.category.label()} · {ticket.subject}",
                reason=f"Support ticket {ticket.ticket_id}",
            )
        except discord.HTTPException:
            self._logger.exception("ticket_channel_create_failed", ticketId=ticket.ticket_id)
            return None

    async def _post_room(
        self,
        channel: discord.TextChannel,
        ticket: TicketRecord,
        config: GuildConfigRecord,
    ) -> None:
        mentions = f"<@{ticket.opener_id}>"
        if config.support_role_id:
            mentions += f" <@&{config.support_role_id}>"
        try:
            await channel.send(
                content=mentions,
                allowed_mentions=discord.AllowedMentions(users=True, roles=True, everyone=False),
            )
        except discord.HTTPException:
            self._logger.info("ticket_ping_failed", ticketId=ticket.ticket_id)
        panel = self._embeds.ticket_room(ticket, support_role_id=config.support_role_id)
        try:
            await channel.send(**panel.send_kwargs())
        except discord.HTTPException:
            self._logger.exception("ticket_room_post_failed", ticketId=ticket.ticket_id)
        panel.stop()

    async def _post_log(
        self,
        guild: discord.Guild,
        config: GuildConfigRecord,
        ticket: TicketRecord,
        actor: discord.abc.User,
        reason: str,
        transcript: str,
    ) -> None:
        if not config.support_logs_channel_id:
            self._logger.info("ticket_log_skipped_no_channel", ticketId=ticket.ticket_id)
            return
        logs = guild.get_channel(int(config.support_logs_channel_id))
        if logs is None:
            try:
                fetched = await guild.fetch_channel(int(config.support_logs_channel_id))
            except discord.HTTPException:
                fetched = None
            logs = fetched
        if not isinstance(logs, discord.TextChannel):
            self._logger.info("ticket_log_skipped_missing_channel", ticketId=ticket.ticket_id)
            return
        panel = self._embeds.ticket_closed(ticket, str(actor.id), reason)
        try:
            await logs.send(**panel.send_kwargs())
        except discord.HTTPException:
            self._logger.exception("ticket_log_failed", ticketId=ticket.ticket_id)
        panel.stop()
        try:
            await logs.send(
                file=discord.File(
                    io.BytesIO(transcript.encode("utf-8")),
                    filename=f"ticket-{ticket.number}.txt",
                )
            )
        except discord.HTTPException:
            self._logger.exception("ticket_transcript_failed", ticketId=ticket.ticket_id)

    async def _snapshot_history(self, channel: discord.TextChannel, ticket: TicketRecord) -> TicketRecord:
        seen = {str(event.get("messageId")) for event in ticket.chat_log if event.get("messageId")}
        added = 0
        try:
            async for message in channel.history(limit=500, oldest_first=True):
                message_id = str(message.id)
                if message_id in seen:
                    continue
                event = _chat_event("created", message)
                await self._container.tickets.append_chat(str(channel.id), event)
                ticket.chat_log.append(event)
                seen.add(message_id)
                added += 1
        except discord.HTTPException:
            self._logger.exception("ticket_history_snapshot_failed", ticketId=ticket.ticket_id)
        if added:
            self._logger.info("ticket_history_snapshotted", ticketId=ticket.ticket_id, added=added)
        return ticket

    async def _delete_later(self, channel: discord.TextChannel, ticket_id: str) -> None:
        try:
            await asyncio.sleep(20)
            await channel.delete(reason=f"Closed ticket {ticket_id}")
        except discord.HTTPException:
            self._logger.info("ticket_channel_delete_skipped", ticketId=ticket_id)


def _channel_name(number: int, username: str) -> str:
    slug = _SLUG.sub("-", username.lower()).strip("-")[:18] or "player"
    return f"ticket-{number}-{slug}"[:100]


def _chat_event(kind: str, message: discord.Message, *, previous: discord.Message | None = None) -> dict:
    attachments = [item.url for item in message.attachments]
    previous_content = ""
    if previous is not None:
        previous_content = previous.clean_content or ""
        if previous.attachments:
            previous_content = (
                previous_content + " " if previous_content else ""
            ) + " ".join(item.url for item in previous.attachments)
    return {
        "kind": kind,
        "messageId": str(message.id),
        "authorId": str(message.author.id),
        "authorName": str(message.author),
        "content": message.clean_content or "",
        "previousContent": previous_content,
        "attachments": attachments,
        "at": message.edited_at or message.created_at,
    }


def _event_stamp(value) -> str:
    if value is None:
        return "unknown time"
    try:
        stamp = value.astimezone(timezone.utc)
    except Exception:
        return str(value)
    return stamp.strftime("%Y-%m-%d %H:%M UTC")
