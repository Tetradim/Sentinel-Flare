"""Discord bot for Sentinel Flare."""

from __future__ import annotations

import os
import logging
from datetime import datetime

import discord
from discord import app_commands
from dotenv import load_dotenv

from darkpool.command_service import (
    CommandSummary,
    build_alerts_summary,
    build_confluence_summary,
    build_darkpool_summary,
    build_levels_summary,
    build_watchlist_summary,
)
from darkpool.discord_formatting import summary_to_embed
from darkpool.providers import ProviderError
from darkpool.subscriptions import SubscriptionStore

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "")

ALERT_THRESHOLDS: dict[str, int] = {}
SUBSCRIPTIONS = SubscriptionStore()


class DarkpoolBot(discord.Client):
    """Discord bot for dark pool monitoring commands."""

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        if DISCORD_GUILD_ID:
            guild = discord.Object(id=int(DISCORD_GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        logger.info("Commands synced")


bot = DarkpoolBot()


def _discord_embed(summary: CommandSummary) -> discord.Embed:
    payload = summary_to_embed(summary)
    embed = discord.Embed(
        title=payload["title"],
        description=payload["description"],
        color=payload["color"],
        timestamp=datetime.fromisoformat(payload["timestamp"]),
    )
    for field in payload["fields"]:
        embed.add_field(name=field["name"], value=field["value"], inline=field.get("inline", False))
    embed.set_footer(text=payload["footer"]["text"])
    return embed


async def _send_summary(
    interaction: discord.Interaction,
    builder,
    *args,
    **kwargs,
):
    await interaction.response.defer()
    try:
        summary: CommandSummary = await builder(*args, **kwargs)
    except ProviderError as exc:
        await interaction.followup.send(f"Provider error: {exc}", ephemeral=True)
        return

    await interaction.followup.send(embed=_discord_embed(summary))


@bot.tree.command()
async def darkpool(interaction: discord.Interaction, symbol: str = "AAPL", provider: str = "finra"):
    """Get a combined dark pool, confluence, and alert summary."""
    await _send_summary(interaction, build_darkpool_summary, symbol, provider=provider)


@bot.tree.command()
async def levels(interaction: discord.Interaction, symbol: str = "AAPL", provider: str = "finra"):
    """Show clustered dark pool levels for a ticker."""
    await _send_summary(interaction, build_levels_summary, symbol, provider=provider)


@bot.tree.command()
async def confluence(interaction: discord.Interaction, symbol: str = "AAPL", provider: str = "finra"):
    """Show dark pool, exposure-node, and options-flow confluence."""
    await _send_summary(interaction, build_confluence_summary, symbol, provider=provider)


@bot.tree.command()
async def alerts(interaction: discord.Interaction, symbol: str = "AAPL", provider: str = "finra"):
    """Show explainable alert candidates for a ticker."""
    await _send_summary(interaction, build_alerts_summary, symbol, provider=provider)


@bot.tree.command()
async def watchlist(interaction: discord.Interaction, symbols: str = "AAPL,NVDA,MSFT", provider: str = "finra"):
    """Show top dark pool candidates for a comma-separated ticker list."""
    parsed = [symbol.strip() for symbol in symbols.split(",") if symbol.strip()]
    await _send_summary(interaction, build_watchlist_summary, parsed, provider=provider)


@bot.tree.command()
async def subscribe(
    interaction: discord.Interaction,
    topic: str = "alerts",
    symbols: str = "AAPL,NVDA,MSFT",
    min_score: float = 70.0,
    provider: str = "finra",
):
    """Subscribe the current channel to an autopost topic."""
    parsed = [symbol.strip() for symbol in symbols.split(",") if symbol.strip()]
    subscription = SUBSCRIPTIONS.create(
        channel_id=str(interaction.channel_id),
        topic=topic,
        symbols=parsed,
        min_score=min_score,
        provider=provider,
    )
    await interaction.response.send_message(
        f"Subscribed this channel to {subscription.topic} for {', '.join(subscription.symbols)} "
        f"at score >= {subscription.min_score:.0f}. ID: {subscription.id}"
    )


@bot.tree.command()
async def subscriptions(interaction: discord.Interaction):
    """List autopost subscriptions for the current channel."""
    rows = SUBSCRIPTIONS.list(channel_id=str(interaction.channel_id))
    if not rows:
        await interaction.response.send_message("No subscriptions configured for this channel.")
        return
    lines = [
        f"{row.id}: {row.topic} | {', '.join(row.symbols)} | score >= {row.min_score:.0f} | {row.provider}"
        for row in rows
    ]
    await interaction.response.send_message("\n".join(lines))


@bot.tree.command()
async def unsubscribe(interaction: discord.Interaction, subscription_id: str):
    """Remove an autopost subscription from the current channel."""
    deleted = SUBSCRIPTIONS.delete(subscription_id, channel_id=str(interaction.channel_id))
    if deleted:
        await interaction.response.send_message(f"Removed subscription {subscription_id}.")
    else:
        await interaction.response.send_message(f"No subscription found for {subscription_id} in this channel.")


@bot.tree.command()
async def setalert(interaction: discord.Interaction, symbol: str, threshold: int = 100000):
    """Set a whale alert threshold for a symbol."""
    ALERT_THRESHOLDS[symbol.upper()] = threshold
    embed = discord.Embed(
        title=f"Alert set for {symbol.upper()}",
        description=f"Whale alerts trigger for {threshold:,}+ shares",
        color=discord.Color.green(),
    )
    embed.add_field(name="Symbol", value=symbol.upper(), inline=True)
    embed.add_field(name="Threshold", value=f"{threshold:,}", inline=True)
    await interaction.response.send_message(embed=embed)


@bot.tree.command()
async def alertstatus(interaction: discord.Interaction):
    """Show all configured whale alerts."""
    if not ALERT_THRESHOLDS:
        await interaction.response.send_message("No alerts configured. Use /setalert symbol:AAPL threshold:100000")
        return

    embed = discord.Embed(title="Active Whale Alerts", color=discord.Color.gold())
    for symbol, threshold in ALERT_THRESHOLDS.items():
        embed.add_field(name=symbol, value=f"{threshold:,} shares", inline=True)
    await interaction.response.send_message(embed=embed)


@bot.tree.command()
async def removealert(interaction: discord.Interaction, symbol: str):
    """Remove a whale alert for a symbol."""
    sym = symbol.upper()
    if sym in ALERT_THRESHOLDS:
        del ALERT_THRESHOLDS[sym]
        await interaction.response.send_message(f"Alert removed for {sym}")
    else:
        await interaction.response.send_message(f"No alert found for {sym}")


@bot.event
async def on_ready():
    logger.info("Logged in as %s", bot.user)


@bot.event
async def on_guild_join(guild: discord.Guild):
    logger.info("Joined guild: %s (%s)", guild.name, guild.id)


async def send_alert(
    webhook_url: str,
    symbol: str,
    direction: str,
    size: int,
    price: float,
    alert_type: str = "whale",
):
    """Send an alert to a Discord webhook."""
    import httpx

    color = 0x00FF00 if direction == "BUY" else 0xFF0000
    embed = {
        "title": f"Darkpool Alert: {symbol}",
        "description": f"{direction} {size:,} shares @ ${price:.2f}",
        "color": color,
        "fields": [
            {"name": "Type", "value": alert_type, "inline": True},
            {"name": "Notional", "value": f"${size * price:,.0f}", "inline": True},
        ],
        "timestamp": datetime.utcnow().isoformat(),
    }
    payload = {"content": f"Darkpool {alert_type.title()} Alert", "embeds": [embed]}

    async with httpx.AsyncClient() as client:
        await client.post(webhook_url, json=payload, timeout=10.0)


if __name__ == "__main__":
    if DISCORD_TOKEN:
        bot.run(DISCORD_TOKEN)
    else:
        print("DISCORD_BOT_TOKEN not set in .env")
        print("Add to .env: DISCORD_BOT_TOKEN=your_token_here")
        print("Optional: DISCORD_GUILD_ID=your_server_id")
