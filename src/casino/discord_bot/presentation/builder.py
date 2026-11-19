from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Self

import discord

ButtonCallback = Callable[[discord.Interaction], Awaitable[None]]


@dataclass(slots=True)
class _FieldSpec:
    name: str
    value: str
    inline: bool = False
    divider: bool = False


@dataclass(slots=True)
class _ButtonSpec:
    label: str
    style: discord.ButtonStyle
    custom_id: str | None = None
    disabled: bool = False
    callback: ButtonCallback | None = None
    url: str | None = None


@dataclass(slots=True)
class _ButtonGroup:
    buttons: list[_ButtonSpec]


@dataclass(slots=True)
class _ItemSpec:
    item: discord.ui.Item


@dataclass(slots=True)
class CasinoPanel:
    """A styled panel: Components V2 container, or a classic embed when fields are inline."""

    view: discord.ui.View
    title: str = ""
    embed: discord.Embed | None = None
    content: str | None = None
    allowed_mentions: discord.AllowedMentions | None = None

    def stop(self) -> None:
        self.view.stop()

    def send_kwargs(self, *, ephemeral: bool = False) -> dict[str, object]:
        payload: dict[str, object] = {"view": self.view}
        if self.embed is not None:
            payload["embed"] = self.embed
        if self.content is not None:
            payload["content"] = self.content
        if self.allowed_mentions is not None:
            payload["allowed_mentions"] = self.allowed_mentions
        if ephemeral:
            payload["ephemeral"] = True
        return payload

    def edit_kwargs(self) -> dict[str, object]:
        payload: dict[str, object] = {"view": self.view}
        if self.embed is not None:
            payload["embed"] = self.embed
        if self.content is not None:
            payload["content"] = self.content
        return payload


class EmbedBuilder:
    """Single style path for every bot panel. Buttons live inside the container. No emojis."""

    def __init__(self) -> None:
        self._title = ""
        self._description = ""
        self._parts: list[_FieldSpec | _ButtonGroup | _ItemSpec] = []
        self._open_buttons: list[_ButtonSpec] | None = None
        self._footer = ""
        self._accent: int | None = None
        self._thumbnail: str | None = None
        self._outside: list[str] = []
        self._timeout: float | None = 180

    def title(self, text: str) -> Self:
        self._title = text.strip()
        return self

    def description(self, text: str) -> Self:
        self._description = text.strip()
        return self

    def field(self, name: str, value: str, *, inline: bool = False) -> Self:
        self._flush_buttons()
        self._parts.append(_FieldSpec(name.strip() or "\u200b", str(value).strip() or "\u200b", inline))
        return self

    def divider(self) -> Self:
        self._flush_buttons()
        self._parts.append(_FieldSpec("", "", divider=True))
        return self

    def fields(self, pairs: Sequence[tuple[str, str]], *, inline: bool = False) -> Self:
        for name, value in pairs:
            self.field(name, value, inline=inline)
        return self

    def columns(self, left: tuple[str, str], right: tuple[str, str]) -> Self:
        self.field(left[0], left[1], inline=True)
        self.field(right[0], right[1], inline=True)
        self.field("\u200b", "\u200b", inline=True)
        return self

    def footer(self, text: str) -> Self:
        self._footer = text.strip()
        return self

    def accent(self, color: int | None) -> Self:
        self._accent = color
        return self

    def thumbnail(self, url: str) -> Self:
        self._thumbnail = url
        return self

    def timeout(self, seconds: float | None) -> Self:
        self._timeout = seconds
        return self

    def persistent(self) -> Self:
        self._timeout = None
        return self

    def button(
        self,
        label: str,
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        custom_id: str | None = None,
        disabled: bool = False,
        callback: ButtonCallback | None = None,
        url: str | None = None,
    ) -> Self:
        if self._open_buttons is None:
            self._open_buttons = []
        self._open_buttons.append(
            _ButtonSpec(
                label=label,
                style=discord.ButtonStyle.link if url else style,
                custom_id=None if url else custom_id,
                disabled=disabled,
                callback=None if url else callback,
                url=url,
            )
        )
        return self

    def add_item(self, item: discord.ui.Item) -> Self:
        self._flush_buttons()
        self._parts.append(_ItemSpec(item))
        return self

    def outside(self, text: str) -> Self:
        cleaned = text.strip()
        if cleaned:
            self._outside.append(cleaned)
        return self

    def build(self) -> CasinoPanel:
        self._flush_buttons()
        if any(field.inline for field in self._field_specs()):
            return CasinoPanel(view=self._classic_view(), title=self._title, embed=self._classic_embed())
        view = discord.ui.LayoutView(timeout=self._timeout)
        self.apply_to(view)
        return CasinoPanel(view=view, title=self._title)

    def apply_to(self, view: discord.ui.LayoutView) -> discord.ui.LayoutView:
        self._flush_buttons()
        view.clear_items()
        for text in self._outside:
            view.add_item(discord.ui.TextDisplay(text))
        view.add_item(self._container())
        return view

    def _container(self) -> discord.ui.Container:
        children: list[discord.ui.Item] = []
        header = self._header_text()
        if self._thumbnail and header:
            children.append(
                discord.ui.Section(
                    discord.ui.TextDisplay(header),
                    accessory=discord.ui.Thumbnail(self._thumbnail),
                )
            )
        elif header:
            children.append(discord.ui.TextDisplay(header))

        field_buffer: list[_FieldSpec] = []
        footer_added = False

        def flush_fields() -> None:
            if not field_buffer:
                return
            groups: list[list[_FieldSpec]] = [[]]
            for field in field_buffer:
                if field.divider:
                    groups.append([])
                    continue
                groups[-1].append(field)
            rendered = False
            for group in groups:
                if not group:
                    continue
                children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
                children.append(
                    discord.ui.TextDisplay(
                        "\n\n".join(f"**{field.name}**\n{field.value}" for field in group)
                    )
                )
                rendered = True
            if not rendered:
                children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
            field_buffer.clear()

        def maybe_footer(*, later_fields: bool) -> None:
            nonlocal footer_added
            if footer_added or later_fields or not self._footer:
                return
            children.append(discord.ui.Separator(visible=False, spacing=discord.SeparatorSpacing.small))
            children.append(discord.ui.TextDisplay(f"-# {self._footer}"))
            footer_added = True

        for index, part in enumerate(self._parts):
            if isinstance(part, _FieldSpec):
                field_buffer.append(part)
                continue
            flush_fields()
            maybe_footer(later_fields=self._has_later_fields(index + 1))
            children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
            if isinstance(part, _ButtonGroup):
                for chunk in _chunks(part.buttons, 5):
                    row = discord.ui.ActionRow()
                    for spec in chunk:
                        row.add_item(_styled_button(spec))
                    children.append(row)
            else:
                row = discord.ui.ActionRow()
                row.add_item(part.item)
                children.append(row)

        flush_fields()
        maybe_footer(later_fields=False)

        if not children:
            children.append(discord.ui.TextDisplay(" "))
        if self._accent is None:
            return discord.ui.Container(*children)
        return discord.ui.Container(*children, accent_color=self._accent)

    def _header_text(self) -> str:
        parts: list[str] = []
        if self._title:
            # Discord treats trailing " #" in headings as markup, which would hide "#1".
            title = self._title.replace("#", "\u2060#")
            parts.append(f"## {title}")
        if self._description:
            parts.append(self._description)
        return "\n\n".join(parts)

    def _classic_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=self._title or None,
            description=self._description or None,
            colour=self._accent,
        )
        if self._thumbnail:
            embed.set_thumbnail(url=self._thumbnail)
        if self._footer:
            embed.set_footer(text=self._footer)
        for field in self._field_specs():
            if field.divider:
                continue
            embed.add_field(name=field.name, value=field.value, inline=field.inline)
        return embed

    def _classic_view(self) -> discord.ui.View:
        view = discord.ui.View(timeout=self._timeout)
        for spec in self._button_specs():
            view.add_item(_styled_button(spec))
        for part in self._parts:
            if isinstance(part, _ItemSpec):
                view.add_item(part.item)
        return view

    def _fields_text(self) -> str:
        blocks = [f"**{field.name}**\n{field.value}" for field in self._field_specs() if not field.divider]
        return "\n\n".join(blocks)

    def _flush_buttons(self) -> None:
        if self._open_buttons:
            self._parts.append(_ButtonGroup(self._open_buttons))
            self._open_buttons = None

    def _field_specs(self) -> list[_FieldSpec]:
        return [part for part in self._parts if isinstance(part, _FieldSpec)]

    def _button_specs(self) -> list[_ButtonSpec]:
        buttons: list[_ButtonSpec] = []
        for part in self._parts:
            if isinstance(part, _ButtonGroup):
                buttons.extend(part.buttons)
        if self._open_buttons:
            buttons.extend(self._open_buttons)
        return buttons

    def _has_later_fields(self, start: int) -> bool:
        return any(isinstance(part, _FieldSpec) and not part.divider for part in self._parts[start:])


class _CallbackButton(discord.ui.Button):
    def __init__(self, spec: _ButtonSpec) -> None:
        super().__init__(
            label=spec.label,
            style=spec.style,
            custom_id=spec.custom_id,
            disabled=spec.disabled,
        )
        self._user_callback = spec.callback

    async def callback(self, interaction: discord.Interaction) -> None:
        if self._user_callback is not None:
            await self._user_callback(interaction)


def _styled_button(spec: _ButtonSpec) -> discord.ui.Button:
    if spec.url:
        return discord.ui.Button(label=spec.label, url=spec.url, disabled=spec.disabled)
    if spec.callback is not None:
        return _CallbackButton(spec)
    return discord.ui.Button(
        label=spec.label,
        style=spec.style,
        custom_id=spec.custom_id,
        disabled=spec.disabled,
    )


def _chunks(items: list[_ButtonSpec], size: int) -> list[list[_ButtonSpec]]:
    return [items[index : index + size] for index in range(0, len(items), size)]
