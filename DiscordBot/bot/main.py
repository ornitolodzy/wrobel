"""Wróbel on Discord: channel 2 next to the web UI. A thin adapter over the API Gateway.

Ways to ask:
  /wrobel tekst:<twierdzenie albo link> [obraz:<plik>] [prywatnie:True]
  message context menu "Sprawdź w Wróblu" (right click a message -> Apps)
  direct message to the bot
  @Wróbel <twierdzenie> in a channel (or @Wróbel as a reply to the message to check)

Message content of other messages is not needed (no privileged intent): Discord delivers
content for DMs, for messages that mention the bot and for context-menu targets.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import tasks

from bot.config import Settings
from bot.format import help_embed, problem_embed, result_embed
from bot.gateway import (IMAGE_TYPES, MAX_IMAGE_BYTES, MAX_TEXT_CHARS, GatewayClient, Request, classify,
                         live_eligible)
from bot.limits import UserLimiter

log = logging.getLogger("wrobel.discord")
_MENTION = re.compile(r"<@!?\d+>|<@&\d+>")
HELP_WORDS = {"", "pomoc", "help", "?", "start", "jak", "info"}
NO_PINGS = discord.AllowedMentions.none()
PERMISSIONS = discord.Permissions(view_channel=True, send_messages=True, send_messages_in_threads=True,
                                  embed_links=True, read_message_history=True)


Show = Callable[[discord.Embed], Awaitable[Any]]


async def edit_reply(interaction: discord.Interaction, embed: discord.Embed) -> None:
    """Replaces the deferred "thinking…" reply; called again it updates the same message."""
    await interaction.edit_original_response(embed=embed)


def first_image(attachments: list[discord.Attachment]) -> discord.Attachment | None:
    for a in attachments:
        if (a.content_type or "").split(";")[0].strip().lower() in IMAGE_TYPES:
            return a
    return None


async def request_from(text: str, attachments: list[discord.Attachment]) -> Request | str | None:
    """Request to verify, an error message for the user (str), or None (nothing to check)."""
    image = first_image(attachments)
    if image is not None:
        if image.size > MAX_IMAGE_BYTES:
            return "Obraz jest za duży (maks. 10 MB)."
        return Request(kind="image", image=await image.read(), filename=image.filename,
                       mime=(image.content_type or "image/png").split(";")[0])
    if len(text or "") > MAX_TEXT_CHARS:
        return f"Tekst jest za długi (maks. {MAX_TEXT_CHARS} znaków). Wybierz jedno konkretne twierdzenie."
    return classify(text)


class WrobelBot(discord.Client):
    def __init__(self, settings: Settings) -> None:
        super().__init__(intents=discord.Intents.default(), allowed_mentions=NO_PINGS)
        self.settings = settings
        self.tree = app_commands.CommandTree(self)
        self.limiter = UserLimiter(settings.user_limit_per_minute)
        self.gateway: GatewayClient | None = None
        self.tree.on_error = self.on_app_command_error
        self._register_commands()

    # ------------------------------------------------------------------ lifecycle
    async def setup_hook(self) -> None:
        self.gateway = GatewayClient(self.settings.gateway_url, self.settings.request_timeout_s)
        if self.settings.guild_id:
            guild = discord.Object(id=self.settings.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)  # dev server: commands visible immediately
            log.info("Slash commands synced to guild %s", self.settings.guild_id)
        await self.tree.sync()
        log.info("Slash commands synced globally")
        self.heartbeat.start()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id %s), servers: %d", self.user, self.user.id if self.user else "?",
                 len(self.guilds))
        if self.application_id:
            url = discord.utils.oauth_url(self.application_id, permissions=PERMISSIONS,
                                          scopes=("bot", "applications.commands"))
            log.info("Invite link (add the bot to a server): %s", url)
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.listening,
                                                             name="/wrobel · sprawdzam fakty"))

    async def close(self) -> None:
        if self.gateway is not None:
            await self.gateway.close()
        Path(self.settings.health_file).unlink(missing_ok=True)
        await super().close()

    @tasks.loop(seconds=30)
    async def heartbeat(self) -> None:
        """Docker healthcheck reads this file's age: fresh = connected to Discord."""
        if self.is_ready() and not self.is_closed():
            Path(self.settings.health_file).touch()

    @heartbeat.before_loop
    async def _before_heartbeat(self) -> None:
        await self.wait_until_ready()

    # ------------------------------------------------------------------ core
    async def check(self, user_id: int, req: Request, asked: str | None, show: Show) -> None:
        """Verify and show the answer. Without confirmed data in the corpus the first answer says
        a live search is running, and `show` is called again with the result (same message)."""
        wait = self.limiter.hit(user_id)
        if wait:
            await show(problem_embed("Zwolnij trochę", f"Możesz wysłać {self.settings.user_limit_per_minute} "
                                                       f"zapytań na minutę. Spróbuj ponownie za {wait} s."))
            return
        assert self.gateway is not None
        result = await self.gateway.verify(req)
        data = result.data or {}
        log.info("check kind=%s ok=%s status=%s", req.kind, result.ok, data.get("status"))
        if not result.ok:
            await show(problem_embed("Nie udało się sprawdzić",
                                     f"{result.problem}\nTo problem techniczny, **nie ocena prawdziwości treści**."))
            return
        asked = asked if req.kind == "claim" else None
        pending = live_eligible(data)
        await show(result_embed(data, asked=asked, live_pending=pending))
        if not pending:
            return
        live = await self.gateway.live(data["check_id"])
        log.info("live search ok=%s status=%s improved=%s", live.ok, (live.data or {}).get("status"),
                 ((live.data or {}).get("live_search") or {}).get("improved"))
        if live.ok and (live.data or {}).get("live_search"):
            await show(result_embed(live.data or {}, asked=asked))
        else:
            await show(result_embed(data, asked=asked,
                                    live_failed=live.problem or "Nie udało się teraz wyszukać na żywo."))

    # ------------------------------------------------------------------ slash + context menu
    def _register_commands(self) -> None:
        bot = self

        @app_commands.command(name="wrobel",
                              description="Sprawdź twierdzenie, link albo zrzut ekranu w wiarygodnych źródłach")
        @app_commands.describe(tekst="Twierdzenie albo pytanie do sprawdzenia, albo link do strony",
                               obraz="Zrzut ekranu / zdjęcie z tekstem do sprawdzenia",
                               prywatnie="Odpowiedź widoczna tylko dla Ciebie")
        @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def wrobel(interaction: discord.Interaction, tekst: str | None = None,
                         obraz: discord.Attachment | None = None, prywatnie: bool = False) -> None:
            if not (tekst or "").strip() and obraz is None:
                await interaction.response.send_message(embed=help_embed(), ephemeral=True)
                return
            if obraz is not None and first_image([obraz]) is None:
                await interaction.response.send_message(
                    embed=problem_embed("To nie jest obraz", "Obsługuję PNG, JPG, WEBP, GIF, BMP i TIFF."),
                    ephemeral=True)
                return
            await interaction.response.defer(thinking=True, ephemeral=prywatnie)
            req = await request_from(tekst or "", [obraz] if obraz else [])
            if isinstance(req, str):
                await edit_reply(interaction, problem_embed("Nie mogę tego sprawdzić", req))
            else:
                await bot.check(interaction.user.id, req, tekst, lambda e: edit_reply(interaction, e))

        @app_commands.context_menu(name="Sprawdź w Wróblu")
        @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def check_message(interaction: discord.Interaction, message: discord.Message) -> None:
            req = await request_from(message.content, message.attachments)
            if req is None:
                await interaction.response.send_message(
                    embed=problem_embed("Brak treści", "Ta wiadomość nie zawiera tekstu ani obrazu do sprawdzenia."),
                    ephemeral=True)
                return
            await interaction.response.defer(thinking=True)
            if isinstance(req, str):
                await edit_reply(interaction, problem_embed("Nie mogę tego sprawdzić", req))
            else:
                await bot.check(interaction.user.id, req, message.content, lambda e: edit_reply(interaction, e))

        self.tree.add_command(wrobel)
        self.tree.add_command(check_message)

    async def on_app_command_error(self, interaction: discord.Interaction,
                                   error: app_commands.AppCommandError) -> None:
        log.exception("command error", exc_info=error)
        embed = problem_embed("Coś poszło nie tak", "Wystąpił błąd techniczny. To nie jest ocena treści.")
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ DM + @mention
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or self.user is None:
            return
        is_dm = message.guild is None
        if not is_dm and self.user not in message.mentions:
            return  # in channels: only when mentioned directly (@everyone does not count)

        text = _MENTION.sub("", message.content).strip()
        attachments = list(message.attachments)
        # "@Wróbel" as a reply to someone's message -> check that message
        if not text and not attachments and message.reference and message.reference.message_id:
            try:
                ref = message.reference.resolved
                if not isinstance(ref, discord.Message):
                    ref = await message.channel.fetch_message(message.reference.message_id)
                text, attachments = _MENTION.sub("", ref.content).strip(), list(ref.attachments)
            except discord.HTTPException:
                pass

        if not attachments and text.lower().strip("!?. ") in HELP_WORDS:
            await message.reply(embed=help_embed(), mention_author=False)
            return
        req = await request_from(text, attachments)
        if req is None:
            await message.reply(embed=help_embed(), mention_author=False)
            return
        if isinstance(req, str):
            await message.reply(embed=problem_embed("Nie mogę tego sprawdzić", req), mention_author=False)
            return
        reply: discord.Message | None = None

        async def show(embed: discord.Embed) -> None:
            nonlocal reply
            if reply is None:
                reply = await message.reply(embed=embed, mention_author=False)
            else:
                await reply.edit(embed=embed)

        async with message.channel.typing():
            await self.check(message.author.id, req, text, show)


def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    discord.VoiceClient.warn_nacl = False  # no voice features, PyNaCl not needed
    if not settings.discord_token:
        log.error("DISCORD_TOKEN is empty: create a bot in https://discord.com/developers/applications "
                  "and put its token in .env in the monorepo root")
        sys.exit(2)
    Path(settings.health_file).unlink(missing_ok=True)
    bot = WrobelBot(settings)
    try:
        bot.run(settings.discord_token, log_handler=None)
    except discord.LoginFailure:
        log.error("Discord rejected DISCORD_TOKEN (invalid or reset). Copy a fresh token from the Bot tab.")
        sys.exit(3)


if __name__ == "__main__":
    main()
