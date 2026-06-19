import discord
from discord.ui import Button, View

class ImagePaginator(View):
    def __init__(self, images: list, query_info: str = "", author_id: int = None, guild_id: int = 0):
        super().__init__(timeout=300)
        self.images = images
        self.current = 0
        self.query_info = query_info
        self.author_id = author_id
        self.guild_id = guild_id
        self._update_buttons()

    def _update_buttons(self):
        for child in self.children:
            if isinstance(child, Button):
                if child.custom_id == "prev":
                    child.disabled = self.current <= 0
                elif child.custom_id == "next":
                    child.disabled = self.current >= len(self.images) - 1

    def _make_embed(self):
        img = self.images[self.current]
        tags = img.get('tags', [])
        tag_str = ', '.join(f'`{t}`' for t in tags) if tags else '`none`'

        score = img.get('score')
        score_text = f"**Match:** {score:.1%}\n" if score is not None else ""

        embed = discord.Embed(
            title=f"🖼️ {self.query_info} — {self.current + 1} / {len(self.images)}",
            description=(
                f"{score_text}"
                f"**By:** <@{img['user_id']}> (`{img.get('username', 'Unknown')}`)\n"
                f"**Tags:** {tag_str}\n"
                f"**Size:** {img.get('width', '?')}×{img.get('height', '?')} | "
                f"{img.get('format', '?')} | {img.get('size_mb', 0):.2f} MB\n"
                f"[🔗 Jump to Message](https://discord.com/channels/{self.guild_id}/{img['channel_id']}/{img['message_id']})"
            ),
            color=discord.Color.blurple()
        )
        embed.set_image(url=img['url'])
        embed.set_footer(text=f"ID: {img['id']} | Msg: {img['message_id']}")
        return embed

    @discord.ui.button(label="◀️ Prev", style=discord.ButtonStyle.secondary, custom_id="prev")
    async def prev(self, interaction: discord.Interaction, button: Button):
        if self.author_id and interaction.user.id != self.author_id:
            await interaction.response.send_message("Not your browser.", ephemeral=True)
            return
        self.current -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self._make_embed(), view=self)

    @discord.ui.button(label="➡️ Next", style=discord.ButtonStyle.secondary, custom_id="next")
    async def next_btn(self, interaction: discord.Interaction, button: Button):
        if self.author_id and interaction.user.id != self.author_id:
            await interaction.response.send_message("Not your browser.", ephemeral=True)
            return
        self.current += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self._make_embed(), view=self)

    @discord.ui.button(label="🗑️ Close", style=discord.ButtonStyle.danger, custom_id="close")
    async def close(self, interaction: discord.Interaction, button: Button):
        if self.author_id and interaction.user.id != self.author_id:
            await interaction.response.send_message("Not your browser.", ephemeral=True)
            return
        await interaction.message.delete()

    async def send(self, interaction: discord.Interaction, ephemeral: bool = False):
        self._update_buttons()
        if interaction.response.is_done():
            await interaction.followup.send(embed=self._make_embed(), view=self, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(embed=self._make_embed(), view=self, ephemeral=ephemeral)
