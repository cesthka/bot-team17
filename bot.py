import re
import os
import json
import asyncio
import logging
import aiohttp
import asyncpg
import discord
from typing import Union, Optional
from discord.ext import commands
from discord import ui, ButtonStyle, SelectOption

# ============================================================
# LOGGING — remplace tous les print()
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bot")
logging.getLogger("discord").setLevel(logging.WARNING)

# ============================================================
# CONFIG
# ============================================================
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TOKEN        = os.environ.get("TOKEN") or os.environ.get("DISCORD_TOKEN")
PREFIX       = os.environ.get("PREFIX", "+")
BUYER_ID     = int(os.environ.get("BUYER_ID", "0"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if not TOKEN:
    raise RuntimeError("❌ TOKEN manquant !")
if not BUYER_ID:
    log.warning("⚠️ BUYER_ID non défini — aucune commande ne fonctionnera.")
if not DATABASE_URL:
    raise RuntimeError("❌ DATABASE_URL manquant !")

# ============================================================
# CACHE
# ============================================================
data = {"owners": [], "wl": []}

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True


class TeamBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=PREFIX, intents=intents, help_command=None)
        self.db: asyncpg.Pool = None

    async def setup_hook(self):
        self.db = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

        # --- Tables ---
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS staff (
                user_id BIGINT PRIMARY KEY,
                rank    TEXT NOT NULL
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS ticket_config (
                guild_id   BIGINT PRIMARY KEY,
                categories JSONB    NOT NULL DEFAULT '[]'::jsonb,
                ping_roles BIGINT[] NOT NULL DEFAULT '{}',
                ping_users BIGINT[] NOT NULL DEFAULT '{}'
            )
        """)
        # --- Migrations (anciennes installations) ---
        await self.db.execute(
            "ALTER TABLE ticket_config ADD COLUMN IF NOT EXISTS ping_roles BIGINT[] NOT NULL DEFAULT '{}'"
        )
        await self.db.execute(
            "ALTER TABLE ticket_config ADD COLUMN IF NOT EXISTS ping_users BIGINT[] NOT NULL DEFAULT '{}'"
        )

        rows = await self.db.fetch("SELECT user_id, rank FROM staff")
        for r in rows:
            if r["rank"] == "owner":
                data["owners"].append(r["user_id"])
            elif r["rank"] == "wl":
                data["wl"].append(r["user_id"])
        log.info("📦 DB chargée : %d owners, %d wl", len(data["owners"]), len(data["wl"]))

        self.add_view(TicketPanelView())
        self.add_view(TicketControlView())


bot = TeamBot()
EMOJI_REGEX = re.compile(r"<(a?):([a-zA-Z0-9_]+):(\d+)>")
EDITING_USERS = set()


# ============================================================
# DB HELPERS — staff
# ============================================================
async def db_add_staff(uid: int, rank: str):
    await bot.db.execute(
        "INSERT INTO staff (user_id, rank) VALUES ($1, $2) "
        "ON CONFLICT (user_id) DO UPDATE SET rank = EXCLUDED.rank",
        uid, rank,
    )
    if rank == "owner":
        if uid not in data["owners"]: data["owners"].append(uid)
        if uid in data["wl"]: data["wl"].remove(uid)
    elif rank == "wl":
        if uid not in data["wl"]: data["wl"].append(uid)
        if uid in data["owners"]: data["owners"].remove(uid)

async def db_remove_staff(uid: int):
    await bot.db.execute("DELETE FROM staff WHERE user_id = $1", uid)
    if uid in data["owners"]: data["owners"].remove(uid)
    if uid in data["wl"]: data["wl"].remove(uid)


# ============================================================
# PERMISSIONS
# ============================================================
def is_buyer(uid): return uid == BUYER_ID
def is_owner(uid): return is_buyer(uid) or uid in data["owners"]
def is_wl(uid):    return is_owner(uid) or uid in data["wl"]
def has_any_perm(uid): return is_wl(uid)

def get_rank(uid):
    if is_buyer(uid): return "💎 Buyer"
    if uid in data["owners"]: return "👑 Owner"
    if uid in data["wl"]: return "⭐ Whitelist"
    return "🔒 Aucun"

def get_rank_short(uid):
    if is_buyer(uid): return "Buyer"
    if uid in data["owners"]: return "Owner"
    if uid in data["wl"]: return "Whitelist"
    return "Aucun"

# Le check global laisse passer +help pour tout le monde, sinon WL+ requis
@bot.check
async def global_perm_check(ctx):
    if ctx.command and ctx.command.name == "help":
        return True
    return has_any_perm(ctx.author.id)


# ============================================================
# COULEURS
# ============================================================
COLORS = {
    "rouge": 0xff0000, "red": 0xff0000, "rouge_fonce": 0x8b0000, "darkred": 0x8b0000,
    "rouge_clair": 0xff6666, "lightred": 0xff6666, "bordeaux": 0x800020,
    "bleu": 0x3498db, "blue": 0x3498db, "bleu_fonce": 0x00008b, "darkblue": 0x00008b,
    "bleu_clair": 0xadd8e6, "lightblue": 0xadd8e6, "bleu_marine": 0x000080, "navy": 0x000080,
    "cyan": 0x00ffff, "turquoise": 0x40e0d0,
    "vert": 0x2ecc71, "green": 0x2ecc71, "vert_fonce": 0x006400, "darkgreen": 0x006400,
    "vert_clair": 0x90ee90, "lightgreen": 0x90ee90, "lime": 0x32cd32, "kaki": 0xbdb76b, "khaki": 0xbdb76b,
    "jaune": 0xf1c40f, "yellow": 0xf1c40f, "orange": 0xe67e22,
    "or": 0xffd700, "dore": 0xffd700, "gold": 0xffd700,
    "rose": 0xff69b4, "pink": 0xff69b4, "violet": 0x9b59b6, "purple": 0x9b59b6,
    "magenta": 0xff00ff, "fuchsia": 0xff00ff, "mauve": 0xe0b0ff,
    "noir": 0x000000, "black": 0x000000, "blanc": 0xffffff, "white": 0xffffff,
    "gris": 0x95a5a6, "gray": 0x95a5a6, "grey": 0x95a5a6,
    "gris_fonce": 0x36393f, "darkgray": 0x36393f, "darkgrey": 0x36393f,
    "argent": 0xc0c0c0, "argente": 0xc0c0c0, "silver": 0xc0c0c0,
    "marron": 0x8b4513, "brown": 0x8b4513, "brun": 0x8b4513, "beige": 0xf5f5dc,
    "discord": 0x5865f2, "blurple": 0x5865f2, "embed": 0x2b2d31, "invisible": 0x2b2d31,
}

def normalize_color_name(s):
    s = s.lower().strip()
    for a, b in [("é","e"),("è","e"),("ê","e"),("ë","e"),("à","a"),("â","a"),("ä","a"),
                 ("ô","o"),("ö","o"),("ù","u"),("û","u"),("ü","u"),("î","i"),("ï","i"),
                 ("ç","c"),(" ","_"),("-","_")]:
        s = s.replace(a, b)
    return s


# ============================================================
# EMBED BUILDER
# ============================================================
class EmbedSession:
    def __init__(self):
        self.title = None
        self.description = "*Utilise le menu déroulant ci-dessous pour personnaliser ton embed.*"
        self.color = 0x2b2d31
        self.footer = None; self.image = None; self.thumbnail = None
        self.author = None; self.url = None; self.timestamp = False

    def build(self):
        e = discord.Embed(color=self.color)
        if self.title: e.title = self.title
        if self.description: e.description = self.description
        if self.footer: e.set_footer(text=self.footer)
        if self.image: e.set_image(url=self.image)
        if self.thumbnail: e.set_thumbnail(url=self.thumbnail)
        if self.author: e.set_author(name=self.author)
        if self.url and self.title: e.url = self.url
        if self.timestamp: e.timestamp = discord.utils.utcnow()
        return e

PROMPTS = {
    "title":       ("📝 Quel titre veux-tu mettre ?", "Tape ton titre (max **256** caractères). `rien` pour retirer."),
    "description": ("📄 Quelle description ?", "Markdown supporté (max **4000** caractères). `rien` pour retirer."),
    "color":       ("🎨 Quelle couleur ?", "Exemples : `rouge`, `bleu`, `vert`, `red`, `gold`, `pink`, `discord`..."),
    "author":      ("👤 Quel auteur ?", "Affiché en haut, en petit. `rien` pour retirer."),
    "footer":      ("🔻 Quel footer ?", "Affiché en bas, en petit. `rien` pour retirer."),
    "image":       ("🖼️ Envoie l'image", "Colle une **URL** ou **upload** le fichier. `rien` pour retirer."),
    "thumbnail":   ("🌄 Envoie le thumbnail", "Petite image en haut à droite. **URL** ou **upload**. `rien` pour retirer."),
    "url":         ("🔗 Lien sur le titre ?", "Doit commencer par `http://` ou `https://`. `rien` pour retirer."),
}

class EditSelect(ui.Select):
    def __init__(self):
        opts = [
            SelectOption(label="Titre",        value="title",       description="Modifier le titre",             emoji="📝"),
            SelectOption(label="Description",  value="description", description="Modifier la description",       emoji="📄"),
            SelectOption(label="Couleur",      value="color",       description="Changer la couleur",            emoji="🎨"),
            SelectOption(label="Auteur",       value="author",      description="Auteur en haut",                emoji="👤"),
            SelectOption(label="Footer",       value="footer",      description="Texte du bas",                  emoji="🔻"),
            SelectOption(label="Image",        value="image",       description="Grande image",                  emoji="🖼️"),
            SelectOption(label="Thumbnail",    value="thumbnail",   description="Petite image haut-droite",      emoji="🌄"),
            SelectOption(label="URL du titre", value="url",         description="Rendre le titre cliquable",     emoji="🔗"),
            SelectOption(label="Timestamp",    value="timestamp",   description="Activer/désactiver la date",    emoji="⏰"),
            SelectOption(label="Reset",        value="reset",       description="Tout réinitialiser",            emoji="🔄"),
        ]
        super().__init__(placeholder="🛠️ Choisis un élément à modifier...", options=opts)

    async def callback(self, interaction):
        view = self.view
        if interaction.user.id != view.author_id:
            return await interaction.response.send_message("❌ Pas ton embed.", ephemeral=True)
        if view.is_editing:
            return await interaction.response.send_message("⏳ Déjà en train d'éditer.", ephemeral=True)

        choice = self.values[0]

        if choice == "timestamp":
            view.session.timestamp = not view.session.timestamp
            return await interaction.response.edit_message(embed=view.session.build(), view=view)
        if choice == "reset":
            view.session = EmbedSession()
            return await interaction.response.edit_message(embed=view.session.build(), view=view)

        view.is_editing = True
        EDITING_USERS.add(interaction.user.id)
        prompt_msg = None
        try:
            title, hint = PROMPTS[choice]
            prompt = discord.Embed(title=title, description=hint, color=0x5865f2)
            prompt.set_footer(text=f"💡 3 min pour répondre • {interaction.user.display_name}")
            await interaction.response.send_message(embed=prompt)
            prompt_msg = await interaction.original_response()

            def check(m):
                return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id

            try:
                user_msg = await bot.wait_for("message", check=check, timeout=180)
            except asyncio.TimeoutError:
                if prompt_msg:
                    try:
                        await prompt_msg.edit(embed=discord.Embed(description="⏰ Temps écoulé.", color=0xed4245))
                        await asyncio.sleep(3); await prompt_msg.delete()
                    except Exception:
                        log.debug("timeout prompt cleanup failed", exc_info=True)
                try: await interaction.message.edit(view=view)
                except Exception: log.debug("view refresh failed", exc_info=True)
                return

            content = user_msg.content.strip()
            clear = content.lower() in {"rien","none","clear","supprimer","delete","vide","remove"}
            error = None

            if choice == "title":
                if clear: view.session.title = None
                elif len(content) > 256: error = "❌ Titre trop long (max 256)."
                else: view.session.title = content
            elif choice == "description":
                if clear: view.session.description = None
                elif len(content) > 4000: error = "❌ Description trop longue (max 4000)."
                else: view.session.description = content
            elif choice == "color":
                if clear: view.session.color = 0x2b2d31
                else:
                    key = normalize_color_name(content)
                    if key in COLORS: view.session.color = COLORS[key]
                    else: error = f"❌ Couleur inconnue : `{content}`."
            elif choice == "author":
                if clear: view.session.author = None
                elif len(content) > 256: error = "❌ Auteur trop long."
                else: view.session.author = content
            elif choice == "footer":
                if clear: view.session.footer = None
                elif len(content) > 2048: error = "❌ Footer trop long."
                else: view.session.footer = content
            elif choice in ("image", "thumbnail"):
                url = None
                if clear: pass
                elif user_msg.attachments: url = user_msg.attachments[0].url
                elif content.startswith(("http://","https://")): url = content
                else: error = "❌ URL invalide ou pièce jointe absente."
                if not error: setattr(view.session, choice, url)
            elif choice == "url":
                if clear: view.session.url = None
                elif content.startswith(("http://","https://")): view.session.url = content
                else: error = "❌ URL invalide."

            try: await prompt_msg.delete()
            except Exception: log.debug("prompt delete failed", exc_info=True)
            try: await user_msg.delete()
            except Exception: log.debug("user_msg delete failed", exc_info=True)

            if error:
                try:
                    err = await interaction.channel.send(error)
                    await asyncio.sleep(4); await err.delete()
                except Exception:
                    log.debug("error msg cleanup failed", exc_info=True)

            try: await interaction.message.edit(embed=view.session.build(), view=view)
            except Exception: log.debug("final refresh failed", exc_info=True)
        finally:
            view.is_editing = False
            EDITING_USERS.discard(interaction.user.id)


class ChannelSelectView(ui.View):
    def __init__(self, session, author_id):
        super().__init__(timeout=120)
        self.session = session; self.author_id = author_id

    @ui.select(cls=ui.ChannelSelect, channel_types=[discord.ChannelType.text], placeholder="📤 Salon de destination")
    async def select_channel(self, interaction, select):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ Pas ton embed.", ephemeral=True)
        channel = select.values[0]
        try:
            real = interaction.guild.get_channel(channel.id) or await interaction.guild.fetch_channel(channel.id)
            await real.send(embed=self.session.build())
            await interaction.response.edit_message(content=f"✅ Envoyé dans {real.mention} !", view=None)
        except Exception as e:
            log.exception("embed send to channel failed")
            await interaction.response.edit_message(content=f"❌ Erreur : {e}", view=None)


class EmbedView(ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=900)
        self.author_id = author_id
        self.session = EmbedSession()
        self.is_editing = False
        self.add_item(EditSelect())

    @ui.button(label="✅ Envoyer", style=ButtonStyle.success, row=1)
    async def btn_send(self, interaction, _):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ Pas ton embed.", ephemeral=True)
        await interaction.response.send_message("📤 Salon ?", view=ChannelSelectView(self.session, self.author_id), ephemeral=True)

    @ui.button(label="❌ Annuler", style=ButtonStyle.danger, row=1)
    async def btn_cancel(self, interaction, _):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ Pas ton embed.", ephemeral=True)
        await interaction.response.edit_message(content="❌ Annulé.", embed=None, view=None)
        self.stop()


# ============================================================
# TICKETS — Catégories par défaut Team 17"
# ============================================================
DEFAULT_CATEGORIES = [
    {
        "emoji": "🟢", "label": "Rejoindre", "desc": "Tu veux faire partie de la team",
        "welcome": (
            "__**Rejoindre la Team 17\"**__\n\n"
            "Envoie un **screenshot de ton profil Palma** prouvant que :\n\n"
            "› Tu as **`17\"`** après ton pseudo\n"
            "› Tu as **`/palma`** dans ton statut\n\n"
            "> *Patiente, un membre du staff va valider ta demande.*"
        ),
    },
    {
        "emoji": "🔴", "label": "Quitter", "desc": "Tu veux quitter la team",
        "welcome": (
            "__**Quitter la Team 17\"**__\n\n"
            "Tu souhaites quitter la team ? Pas de soucis.\n\n"
            "> *Explique-nous **brièvement** la raison de ton départ.*\n\n"
            "Un membre du staff va passer pour finaliser."
        ),
    },
    {
        "emoji": "🟡", "label": "Aide", "desc": "Besoin d'aide ou d'une info",
        "welcome": (
            "__**Demande d'aide**__\n\n"
            "Explique **clairement ton problème** ou ta question.\n\n"
            "› Plus tu donnes de détails, plus on peut t'aider vite\n"
            "› Joins des **captures d'écran** si nécessaire\n\n"
            "> *Patiente, le staff arrive.*"
        ),
    },
    {
        "emoji": "⚠️", "label": "Abus", "desc": "Signaler un comportement abusif",
        "welcome": (
            "__**Signalement d'abus**__\n\n"
            "Donne-nous un maximum d'infos pour qu'on puisse agir :\n\n"
            "› **Pseudo** de la personne concernée\n"
            "› **Ce qui s'est passé** (en détail)\n"
            "› **Preuves** (screenshots, vidéos...)\n\n"
            "> ⚠️ *Tout faux signalement sera sanctionné.*"
        ),
    },
]


async def get_ticket_config(guild_id):
    """Retourne {categories, ping_roles, ping_users}"""
    row = await bot.db.fetchrow(
        "SELECT categories, ping_roles, ping_users FROM ticket_config WHERE guild_id = $1",
        guild_id,
    )
    if row:
        cats = row["categories"]
        if isinstance(cats, str):
            try: cats = json.loads(cats)
            except json.JSONDecodeError:
                log.exception("invalid json in categories"); cats = []
        if not cats: cats = list(DEFAULT_CATEGORIES)
        return {
            "categories": cats,
            "ping_roles": list(row["ping_roles"] or []),
            "ping_users": list(row["ping_users"] or []),
        }
    return {"categories": list(DEFAULT_CATEGORIES), "ping_roles": [], "ping_users": []}


async def save_ticket_config(guild_id, categories, ping_roles, ping_users):
    await bot.db.execute(
        """
        INSERT INTO ticket_config (guild_id, categories, ping_roles, ping_users)
        VALUES ($1, $2::jsonb, $3, $4)
        ON CONFLICT (guild_id) DO UPDATE
            SET categories = EXCLUDED.categories,
                ping_roles = EXCLUDED.ping_roles,
                ping_users = EXCLUDED.ping_users
        """,
        guild_id, json.dumps(categories), list(ping_roles), list(ping_users),
    )


async def create_ticket_channel(interaction, category):
    """category : dict {emoji, label, desc, welcome (optionnel)}"""
    guild = interaction.guild
    user = interaction.user
    category_label = category["label"]
    category_emoji = category.get("emoji", "📂")
    welcome_text = category.get("welcome")

    # ⚠️ Fix bug : égalité stricte au lieu de startswith
    existing = discord.utils.find(
        lambda c: c.topic == f"ticket-{user.id}",
        guild.text_channels,
    )
    if existing:
        return await interaction.response.send_message(
            f"❌ Tu as déjà un ticket ouvert : {existing.mention}", ephemeral=True
        )

    if not guild.me.guild_permissions.manage_channels:
        return await interaction.response.send_message(
            "❌ Il me manque la permission **Gérer les salons**.", ephemeral=True
        )

    disc_category = discord.utils.get(guild.categories, name="🎫 Tickets")
    if not disc_category:
        try:
            disc_category = await guild.create_category("🎫 Tickets")
        except discord.Forbidden:
            return await interaction.response.send_message(
                "❌ Pas la permission de créer la catégorie.", ephemeral=True
            )

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, attach_files=True,
            embed_links=True, read_message_history=True,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, manage_channels=True,
            manage_messages=True,
        ),
    }
    for uid in [BUYER_ID] + data["owners"] + data["wl"]:
        m = guild.get_member(uid)
        if m:
            overwrites[m] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_messages=True,
                attach_files=True, embed_links=True, read_message_history=True,
            )

    prefix = re.sub(r"[^a-z0-9]", "", category_label.lower())[:15] or "ticket"
    try:
        channel = await guild.create_text_channel(
            name=f"{prefix}-{user.name}", category=disc_category,
            overwrites=overwrites, topic=f"ticket-{user.id}",
            reason=f"Ticket ({category_label}) par {user}",
        )
    except discord.Forbidden:
        return await interaction.response.send_message(
            "❌ Pas la permission de créer le salon.", ephemeral=True
        )

    # --- Embed d'accueil ---
    embed = discord.Embed(color=0x5865f2, timestamp=discord.utils.utcnow())
    embed.set_author(name=f"Ticket — {category_label}", icon_url=user.display_avatar.url)
    if welcome_text:
        embed.description = (
            f"Salut {user.mention} ! 👋\n\n"
            f"**Catégorie :** {category_emoji} {category_label}\n\n{welcome_text}"
        )
    else:
        embed.description = (
            f"Salut {user.mention} ! 👋\n\n"
            f"**Catégorie :** {category_emoji} {category_label}\n\n"
            f"Un membre du **staff** va te répondre rapidement.\n"
            f"Décris ton problème en attendant."
        )
    embed.set_footer(text=f"{user} • {user.id}")
    embed.set_thumbnail(url=user.display_avatar.url)

    # --- Mentions configurées (créateur + rôles + users) ---
    config = await get_ticket_config(guild.id)
    mentions = [user.mention]
    for rid in config["ping_roles"]:
        role = guild.get_role(rid)
        if role: mentions.append(role.mention)
    for uid in config["ping_users"]:
        m = guild.get_member(uid)
        if m: mentions.append(m.mention)
    ping_content = " ".join(mentions)

    await channel.send(
        content=ping_content,
        embed=embed,
        view=TicketControlView(),
        allowed_mentions=discord.AllowedMentions(users=True, roles=True, everyone=False),
    )
    await interaction.response.send_message(
        f"✅ Ton ticket a été créé : {channel.mention}", ephemeral=True
    )


# ----- Panel persistant -----
class TicketPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Ouvrir un ticket", style=ButtonStyle.primary, emoji="🎫", custom_id="ticket_open_btn")
    async def open_ticket(self, interaction, _):
        config = await get_ticket_config(interaction.guild.id)
        cats = config["categories"]
        if not cats:
            return await interaction.response.send_message("❌ Aucune catégorie configurée.", ephemeral=True)
        view = TicketCategoryPickView(cats)
        await interaction.response.send_message("📂 **Choisis une raison :**", view=view, ephemeral=True)


class TicketCategoryPickView(ui.View):
    def __init__(self, categories):
        super().__init__(timeout=60)
        opts = []
        for i, cat in enumerate(categories[:25]):
            opts.append(SelectOption(
                label=cat["label"][:100], value=str(i),
                emoji=cat.get("emoji", "📂"),
                description=cat.get("desc", "")[:100],
            ))
        select = ui.Select(placeholder="📂 Choisis une raison...", options=opts)
        select.callback = self._make_cb(categories)
        self.add_item(select)

    def _make_cb(self, categories):
        async def callback(interaction):
            cat = categories[int(interaction.data["values"][0])]
            await create_ticket_channel(interaction, cat)
        return callback


# ----- Contrôles dans le ticket -----
class TicketControlView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Fermer", style=ButtonStyle.danger, emoji="🔒", custom_id="ticket_close_btn", row=0)
    async def close_ticket(self, interaction, _):
        topic = interaction.channel.topic or ""
        creator_id = None
        if topic.startswith("ticket-"):
            try: creator_id = int(topic.split("-", 1)[1])
            except (ValueError, IndexError):
                log.debug("invalid ticket topic format")
        if interaction.user.id != creator_id and not has_any_perm(interaction.user.id):
            return await interaction.response.send_message(
                "❌ Tu ne peux pas fermer ce ticket.", ephemeral=True
            )
        await interaction.response.send_message(
            embed=discord.Embed(description="⚠️ **Es-tu sûr de vouloir fermer ce ticket ?**", color=0xed4245),
            view=TicketConfirmCloseView(),
        )

    @ui.button(label="Prendre en charge", style=ButtonStyle.success, emoji="✋", custom_id="ticket_claim_btn", row=0)
    async def claim_ticket(self, interaction, _):
        if not has_any_perm(interaction.user.id):
            return await interaction.response.send_message("❌ Réservé au staff.", ephemeral=True)
        embed = discord.Embed(
            description=f"✋ **{interaction.user.mention}** a pris en charge ce ticket.",
            color=0x2ecc71,
        )
        embed.timestamp = discord.utils.utcnow()
        await interaction.response.send_message(embed=embed)
        try:
            if not interaction.channel.name.startswith("✅"):
                await interaction.channel.edit(name=f"✅-{interaction.channel.name}")
        except Exception:
            log.debug("claim rename failed", exc_info=True)


class TicketConfirmCloseView(ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @ui.button(label="Confirmer la fermeture", style=ButtonStyle.danger, emoji="✅")
    async def confirm(self, interaction, _):
        await interaction.response.send_message(embed=discord.Embed(
            description=f"🔒 Fermé par {interaction.user.mention}.\nSuppression dans **5s**...",
            color=0xed4245,
        ))
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Fermé par {interaction.user}")
        except Exception:
            log.exception("ticket delete failed")

    @ui.button(label="Annuler", style=ButtonStyle.secondary, emoji="❌")
    async def cancel(self, interaction, _):
        await interaction.response.edit_message(
            embed=discord.Embed(description="❌ Annulé.", color=0x95a5a6), view=None
        )


# ============================================================
# TICKET SETUP — Builder
# ============================================================
class TicketSetupSession:
    def __init__(self):
        self.title = "🎫 Ouvre un ticket !"
        self.description = (
            "Pour rejoindre la team, **ouvre un ticket** et envoie un *screenshot de ton profil Palma* prouvant que :\n\n"
            "› Tu as **`17\"`** après ton pseudo\n"
            "› Tu as **`/palma`** dans ton statut\n\n"
            "> *Patiente, un membre du staff validera ta demande.*"
        )
        self.color = 0x2b2d31
        self.image = None
        self.thumbnail = None
        self.footer = "Team 17\" • Le staff te répondra dès que possible"
        self.categories = []
        self.ping_roles = []   # NEW : list[int]
        self.ping_users = []   # NEW : list[int]

    def build(self):
        e = discord.Embed(title=self.title, description=self.description, color=self.color)
        if self.image: e.set_image(url=self.image)
        if self.thumbnail: e.set_thumbnail(url=self.thumbnail)
        if self.footer: e.set_footer(text=self.footer)
        return e

    def categories_text(self):
        if not self.categories:
            return "*Aucune catégorie — ajoute-en au moins une !*"
        return "\n".join(
            f"`{i+1}.` {c.get('emoji','📂')} **{c['label']}** — {c.get('desc','')}"
            + ("  ✏️" if c.get("welcome") else "")
            for i, c in enumerate(self.categories)
        )

    def pings_text(self):
        parts = []
        if self.ping_roles:
            parts.append("**Rôles :** " + ", ".join(f"<@&{r}>" for r in self.ping_roles))
        if self.ping_users:
            parts.append("**Membres :** " + ", ".join(f"<@{u}>" for u in self.ping_users))
        return "\n".join(parts) if parts else "*Aucun ping (créateur seulement)*"


TICKET_PROMPTS = {
    "title":       ("📝 Titre du panel", "Tape le titre (max **256** caractères). `rien` pour retirer."),
    "description": ("📄 Description du panel", "Texte au-dessus du bouton. `rien` pour retirer."),
    "color":       ("🎨 Couleur du panel", "Exemples : `rouge`, `bleu`, `vert`, `gold`, `discord`..."),
    "footer":      ("🔻 Footer du panel", "Texte en bas. `rien` pour retirer."),
    "image":       ("🖼️ Image du panel", "**URL** ou **upload**. `rien` pour retirer."),
    "thumbnail":   ("🌄 Thumbnail du panel", "**URL** ou **upload**. `rien` pour retirer."),
}


async def _wait_response(interaction, view, title, hint):
    """Pose une question dans le salon, attend la réponse. try/finally garantit le reset."""
    view.is_editing = True
    EDITING_USERS.add(interaction.user.id)
    prompt_msg = None
    try:
        prompt = discord.Embed(title=title, description=hint, color=0x5865f2)
        prompt.set_footer(text=f"💡 3 min • {interaction.user.display_name}")
        await interaction.response.send_message(embed=prompt)
        prompt_msg = await interaction.original_response()
        def chk(m):
            return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id
        try:
            msg = await bot.wait_for("message", check=chk, timeout=180)
        except asyncio.TimeoutError:
            return None, None
        try: await prompt_msg.delete()
        except Exception: log.debug("prompt delete failed", exc_info=True)
        try: await msg.delete()
        except Exception: log.debug("user msg delete failed", exc_info=True)
        return msg.content.strip(), msg
    finally:
        view.is_editing = False
        EDITING_USERS.discard(interaction.user.id)


async def _wait_simple(channel, user_id, view, title, hint):
    """Variante sans interaction : envoie directement dans le salon."""
    view.is_editing = True
    EDITING_USERS.add(user_id)
    h = None
    try:
        h = await channel.send(embed=discord.Embed(title=title, description=hint, color=0x5865f2))
        def chk(m):
            return m.author.id == user_id and m.channel.id == channel.id
        try:
            msg = await bot.wait_for("message", check=chk, timeout=180)
            txt = msg.content.strip()
            try: await msg.delete()
            except Exception: log.debug("user msg delete failed", exc_info=True)
            return txt
        except asyncio.TimeoutError:
            return None
    finally:
        if h:
            try: await h.delete()
            except Exception: log.debug("hint delete failed", exc_info=True)
        view.is_editing = False
        EDITING_USERS.discard(user_id)


# ----- Sub-views pour configurer les pings -----
class PingRoleSelectView(ui.View):
    def __init__(self, parent_view, author_id):
        super().__init__(timeout=120)
        self.parent_view = parent_view
        self.author_id = author_id

    @ui.select(
        cls=ui.RoleSelect,
        placeholder="🔔 Rôles à ping (laisse vide pour aucun)",
        min_values=0, max_values=10,
    )
    async def select_roles(self, interaction, select):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ Pas ton setup.", ephemeral=True)
        ids = [r.id for r in select.values]
        self.parent_view.session.ping_roles = ids
        try:
            if self.parent_view.message:
                await self.parent_view.message.edit(
                    embed=self.parent_view.build_preview(), view=self.parent_view
                )
        except Exception:
            log.exception("parent view refresh (roles) failed")
        names = ", ".join(r.mention for r in select.values) if select.values else "*Aucun*"
        await interaction.response.edit_message(
            content=f"✅ Pings rôles mis à jour : {names}",
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class PingUserSelectView(ui.View):
    def __init__(self, parent_view, author_id):
        super().__init__(timeout=120)
        self.parent_view = parent_view
        self.author_id = author_id

    @ui.select(
        cls=ui.UserSelect,
        placeholder="🔔 Membres à ping (laisse vide pour aucun)",
        min_values=0, max_values=10,
    )
    async def select_users(self, interaction, select):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ Pas ton setup.", ephemeral=True)
        ids = [u.id for u in select.values]
        self.parent_view.session.ping_users = ids
        try:
            if self.parent_view.message:
                await self.parent_view.message.edit(
                    embed=self.parent_view.build_preview(), view=self.parent_view
                )
        except Exception:
            log.exception("parent view refresh (users) failed")
        names = ", ".join(u.mention for u in select.values) if select.values else "*Aucun*"
        await interaction.response.edit_message(
            content=f"✅ Pings membres mis à jour : {names}",
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class TicketSetupSelect(ui.Select):
    def __init__(self):
        super().__init__(placeholder="🛠️ Que veux-tu modifier ?", options=[
            SelectOption(label="Titre",                  value="title",       emoji="📝", description="Modifier le titre"),
            SelectOption(label="Description",            value="description", emoji="📄", description="Modifier la description"),
            SelectOption(label="Couleur",                value="color",       emoji="🎨", description="Changer la couleur"),
            SelectOption(label="Footer",                 value="footer",      emoji="🔻", description="Texte du bas"),
            SelectOption(label="Image",                  value="image",       emoji="🖼️", description="Grande image"),
            SelectOption(label="Thumbnail",              value="thumbnail",   emoji="🌄", description="Petite image haut-droite"),
            SelectOption(label="Ajouter catégorie",      value="cat_add",     emoji="➕", description="Ajouter une raison au menu"),
            SelectOption(label="Modifier message accueil", value="cat_msg",   emoji="✏️", description="Changer le message d'une catégorie"),
            SelectOption(label="Supprimer catégorie",    value="cat_del",     emoji="➖", description="Supprimer une raison"),
            SelectOption(label="Vider les catégories",   value="cat_clear",   emoji="🗑️", description="Tout supprimer"),
            SelectOption(label="Pings rôles",            value="ping_roles",  emoji="🔔", description="Rôles à ping à l'ouverture"),
            SelectOption(label="Pings membres",          value="ping_users",  emoji="🔔", description="Membres à ping à l'ouverture"),
            SelectOption(label="Reset tout",             value="reset",       emoji="🔄", description="Réinitialiser le panel"),
        ])

    async def callback(self, interaction):
        view = self.view
        if interaction.user.id != view.author_id:
            return await interaction.response.send_message("❌ Pas ton setup.", ephemeral=True)
        if view.is_editing:
            return await interaction.response.send_message("⏳ Déjà en train d'éditer.", ephemeral=True)
        choice = self.values[0]

        # ----- Resets / quick actions -----
        if choice == "reset":
            view.session = TicketSetupSession()
            view.session.categories = list(DEFAULT_CATEGORIES)
            return await interaction.response.edit_message(embed=view.build_preview(), view=view)

        if choice == "cat_clear":
            view.session.categories = []
            return await interaction.response.edit_message(embed=view.build_preview(), view=view)

        # ----- Sub-menus pings -----
        if choice == "ping_roles":
            return await interaction.response.send_message(
                "🔔 **Sélectionne les rôles à ping** à l'ouverture d'un ticket :",
                view=PingRoleSelectView(view, interaction.user.id),
                ephemeral=True,
            )
        if choice == "ping_users":
            return await interaction.response.send_message(
                "🔔 **Sélectionne les membres à ping** à l'ouverture d'un ticket :",
                view=PingUserSelectView(view, interaction.user.id),
                ephemeral=True,
            )

        # ----- Catégories -----
        if choice == "cat_add":
            if len(view.session.categories) >= 25:
                return await interaction.response.send_message("❌ Max 25 catégories.", ephemeral=True)
            label, _ = await _wait_response(interaction, view, "➕ Nom de la catégorie", "Tape le **nom**.\n*Ex : `Rejoindre`*")
            if not label: return
            label = label[:100]
            desc = await _wait_simple(interaction.channel, interaction.user.id, view,
                                      "📄 Description courte",
                                      f"Description pour **{label}**.\n`rien` pour vide.")
            if desc and desc.lower() in {"rien","none","vide"}: desc = ""
            desc = (desc or "")[:100]
            emoji_v = await _wait_simple(interaction.channel, interaction.user.id, view,
                                         "😀 Emoji",
                                         f"Emoji pour **{label}**.\n`rien` pour 📂")
            if not emoji_v or emoji_v.lower() in {"rien","none","vide"}: emoji_v = "📂"
            emoji_v = emoji_v[:30]
            welcome = await _wait_simple(
                interaction.channel, interaction.user.id, view,
                "💬 Message d'accueil",
                f"Message affiché à l'ouverture du ticket pour **{label}**.\nMarkdown supporté.\n`rien` pour message par défaut.",
            )
            if not welcome or welcome.lower() in {"rien","none","vide"}: welcome = None
            view.session.categories.append({"emoji": emoji_v, "label": label, "desc": desc, "welcome": welcome})
            try: await interaction.message.edit(embed=view.build_preview(), view=view)
            except Exception: log.debug("cat_add refresh failed", exc_info=True)
            return

        if choice == "cat_msg":
            if not view.session.categories:
                return await interaction.response.send_message("❌ Aucune catégorie.", ephemeral=True)
            lst = "\n".join(f"`{i+1}.` {c.get('emoji','📂')} {c['label']}" for i, c in enumerate(view.session.categories))
            content, _ = await _wait_response(interaction, view, "✏️ Modifier le message d'accueil",
                                              f"Tape le **numéro** de la catégorie :\n\n{lst}")
            if not content: return
            try:
                idx = int(content) - 1
                if not (0 <= idx < len(view.session.categories)): raise ValueError
            except ValueError:
                try: await interaction.channel.send("❌ Numéro invalide.", delete_after=4)
                except Exception: log.debug("err msg failed", exc_info=True)
                return
            cat = view.session.categories[idx]
            welcome = await _wait_simple(
                interaction.channel, interaction.user.id, view,
                f"💬 Nouveau message pour {cat['label']}",
                "Tape le nouveau message d'accueil (markdown supporté).\n`rien` pour réinitialiser par défaut.",
            )
            if not welcome or welcome.lower() in {"rien","none","vide"}: cat["welcome"] = None
            else: cat["welcome"] = welcome
            try: await interaction.message.edit(embed=view.build_preview(), view=view)
            except Exception: log.debug("cat_msg refresh failed", exc_info=True)
            return

        if choice == "cat_del":
            if not view.session.categories:
                return await interaction.response.send_message("❌ Aucune catégorie.", ephemeral=True)
            lst = "\n".join(f"`{i+1}.` {c.get('emoji','📂')} {c['label']}" for i, c in enumerate(view.session.categories))
            content, _ = await _wait_response(interaction, view, "➖ Supprimer",
                                              f"Tape le **numéro** :\n\n{lst}")
            if not content: return
            try:
                idx = int(content) - 1
                if 0 <= idx < len(view.session.categories):
                    view.session.categories.pop(idx)
            except ValueError:
                log.debug("invalid index", exc_info=True)
            try: await interaction.message.edit(embed=view.build_preview(), view=view)
            except Exception: log.debug("cat_del refresh failed", exc_info=True)
            return

        # ----- Champs classiques -----
        title, hint = TICKET_PROMPTS[choice]
        content, user_msg = await _wait_response(interaction, view, title, hint)
        if content is None: return
        clear = content.lower() in {"rien","none","clear","supprimer","delete","vide","remove"}
        error = None
        if choice == "title":
            if clear: view.session.title = None
            elif len(content) > 256: error = "❌ Trop long."
            else: view.session.title = content
        elif choice == "description":
            if clear: view.session.description = None
            elif len(content) > 4000: error = "❌ Trop long."
            else: view.session.description = content
        elif choice == "color":
            if clear: view.session.color = 0x5865f2
            else:
                key = normalize_color_name(content)
                if key in COLORS: view.session.color = COLORS[key]
                else: error = f"❌ Couleur inconnue : `{content}`."
        elif choice == "footer":
            if clear: view.session.footer = None
            else: view.session.footer = content
        elif choice in ("image", "thumbnail"):
            url = None
            if clear: pass
            elif user_msg and user_msg.attachments: url = user_msg.attachments[0].url
            elif content.startswith(("http://","https://")): url = content
            else: error = "❌ URL invalide."
            if not error: setattr(view.session, choice, url)
        if error:
            try: await interaction.channel.send(error, delete_after=4)
            except Exception: log.debug("err send failed", exc_info=True)
        try: await interaction.message.edit(embed=view.build_preview(), view=view)
        except Exception: log.debug("final refresh failed", exc_info=True)


class TicketSetupChannelSelect(ui.View):
    def __init__(self, session, author_id, guild_id):
        super().__init__(timeout=120)
        self.session = session; self.author_id = author_id; self.guild_id = guild_id

    @ui.select(cls=ui.ChannelSelect, channel_types=[discord.ChannelType.text], placeholder="📤 Salon de destination")
    async def select_channel(self, interaction, select):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ Pas ton setup.", ephemeral=True)
        channel = select.values[0]
        try:
            real = interaction.guild.get_channel(channel.id) or await interaction.guild.fetch_channel(channel.id)
            await save_ticket_config(
                self.guild_id, self.session.categories,
                self.session.ping_roles, self.session.ping_users,
            )
            await real.send(embed=self.session.build(), view=TicketPanelView())
            await interaction.response.edit_message(
                content=f"✅ Panel envoyé dans {real.mention} !\n💾 Configuration sauvegardée.",
                embed=None, view=None,
            )
        except Exception as e:
            log.exception("panel send failed")
            await interaction.response.edit_message(content=f"❌ Erreur : {e}", view=None)


class TicketSetupView(ui.View):
    def __init__(self, author_id, guild_id):
        super().__init__(timeout=600)
        self.author_id = author_id
        self.guild_id = guild_id
        self.session = TicketSetupSession()
        self.is_editing = False
        self.message = None  # référence pour les sub-views
        self.add_item(TicketSetupSelect())

    def build_preview(self):
        e = self.session.build()
        e.add_field(
            name=f"📂 Catégories ({len(self.session.categories)})",
            value=self.session.categories_text(), inline=False,
        )
        e.add_field(
            name="🔔 Ping à l'ouverture",
            value=self.session.pings_text(), inline=False,
        )
        e.add_field(name="\u200b", value="*✏️ = message d'accueil personnalisé*", inline=False)
        return e

    @ui.button(label="✅ Envoyer le panel", style=ButtonStyle.success, row=1)
    async def btn_send(self, interaction, _):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ Pas ton setup.", ephemeral=True)
        if not self.session.categories:
            return await interaction.response.send_message("❌ Ajoute au moins une catégorie !", ephemeral=True)
        await interaction.response.send_message(
            "📤 Dans quel salon ?",
            view=TicketSetupChannelSelect(self.session, self.author_id, self.guild_id),
            ephemeral=True,
        )

    @ui.button(label="📤 Envoyer ici", style=ButtonStyle.primary, row=1)
    async def btn_send_here(self, interaction, _):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ Pas ton setup.", ephemeral=True)
        if not self.session.categories:
            return await interaction.response.send_message("❌ Ajoute au moins une catégorie !", ephemeral=True)
        await save_ticket_config(
            self.guild_id, self.session.categories,
            self.session.ping_roles, self.session.ping_users,
        )
        await interaction.channel.send(embed=self.session.build(), view=TicketPanelView())
        await interaction.response.edit_message(
            content="✅ Panel envoyé !\n💾 Configuration sauvegardée.",
            embed=None, view=None,
        )
        self.stop()

    @ui.button(label="❌ Annuler", style=ButtonStyle.danger, row=1)
    async def btn_cancel(self, interaction, _):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ Pas ton setup.", ephemeral=True)
        await interaction.response.edit_message(content="❌ Annulé.", embed=None, view=None)
        self.stop()


# ============================================================
# COMMANDES — Utilitaires
# ============================================================
@bot.command(name="embed")
async def embed_cmd(ctx):
    view = EmbedView(ctx.author.id)
    await ctx.reply(
        content=f"**🛠️ Constructeur d'embed** — {ctx.author.mention}",
        embed=view.session.build(), view=view, mention_author=False,
    )


@bot.command(name="create", aliases=["steal", "addemoji"])
@commands.cooldown(1, 5, commands.BucketType.user)  # anti-spam
async def create_emoji(ctx, *, args: str = None):
    if not ctx.author.guild_permissions.manage_expressions:
        return await ctx.reply("❌ Perm **Gérer les emojis** manquante.", mention_author=False, delete_after=5)
    if not ctx.guild.me.guild_permissions.manage_expressions:
        return await ctx.reply("❌ Il me faut **Gérer les emojis**.", mention_author=False, delete_after=5)
    if not args:
        return await ctx.reply(f"❌ `{PREFIX}create <:emoji:id> ...`", mention_author=False)

    matches = EMOJI_REGEX.findall(args)
    if not matches:
        return await ctx.reply("❌ Aucun emoji détecté.", mention_author=False)

    custom_name = None
    if len(matches) == 1:
        leftover = EMOJI_REGEX.sub("", args).strip()
        if leftover:
            c = re.sub(r"[^a-zA-Z0-9_]", "", leftover)[:32]
            if len(c) >= 2: custom_name = c

    status = await ctx.reply(f"⏳ Création de {len(matches)} emoji(s)...", mention_author=False)
    added, failed = [], []
    async with aiohttp.ClientSession() as session:
        for animated, name, emoji_id in matches:
            ext = "gif" if animated else "png"
            url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"
            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        failed.append(f"`{name}`"); continue
                    img = await resp.read()
                final = re.sub(r"[^a-zA-Z0-9_]", "_", custom_name or name)[:32]
                if len(final) < 2: final = f"emoji_{emoji_id[:6]}"
                new = await ctx.guild.create_custom_emoji(name=final, image=img, reason=f"+create par {ctx.author}")
                added.append(str(new))
            except discord.HTTPException as e:
                failed.append(f"`{name}` (slots pleins)" if e.code == 30008 else f"`{name}`")
                log.warning("create emoji http err: %s", e)
            except Exception:
                log.exception("create emoji generic err")
                failed.append(f"`{name}`")

    lines = []
    if added: lines.append(f"✅ **{len(added)} ajouté(s)** : {' '.join(added)}")
    if failed: lines.append(f"❌ **{len(failed)} échec(s)** : {', '.join(failed)}")
    await status.edit(content="\n".join(lines) or "❌ Rien ajouté.")


# ============================================================
# CLEAR — NOUVEAU
# ============================================================
@bot.command(name="clear", aliases=["purge", "prune"])
async def clear_cmd(ctx, *, arg: str = None):
    """
    +clear <1-100>   → supprime N derniers messages
    +clear @membre   → supprime les messages de ce membre (parmi les 500 derniers)
    +clear <user_id> → idem pour un user qui n'est plus sur le serveur
    """
    if not ctx.guild.me.guild_permissions.manage_messages:
        return await ctx.reply("❌ Perm **Gérer les messages** manquante.", mention_author=False, delete_after=5)
    if not arg:
        e = discord.Embed(
            title="❌ Argument manquant",
            description=(
                f"**Usages :**\n"
                f"`{PREFIX}clear <1-100>` — supprime N messages\n"
                f"`{PREFIX}clear @membre` — supprime les messages de ce membre\n"
                f"`{PREFIX}clear <user_id>` — même chose pour un user parti"
            ),
            color=0xed4245,
        )
        return await ctx.reply(embed=e, mention_author=False, delete_after=12)

    # Supprimer la commande
    try: await ctx.message.delete()
    except Exception: log.debug("clear: command delete failed", exc_info=True)

    # 1. Nombre 1-100 ?
    if arg.isdigit():
        n = int(arg)
        if 1 <= n <= 100:
            try:
                deleted = await ctx.channel.purge(limit=n)
                msg = await ctx.channel.send(f"✅ **{len(deleted)}** message(s) supprimé(s).")
                await asyncio.sleep(4)
                try: await msg.delete()
                except Exception: log.debug("clear feedback delete failed", exc_info=True)
            except discord.Forbidden:
                await ctx.channel.send("❌ Permissions insuffisantes.", delete_after=5)
            except Exception as e:
                log.exception("clear purge by count failed")
                await ctx.channel.send(f"❌ Erreur : {e}", delete_after=5)
            return
        if n > 100:
            return await ctx.channel.send("❌ Max **100** messages à la fois.", delete_after=5)

    # 2. Membre/User (mention ou ID)
    target = None
    try:
        target = await commands.MemberConverter().convert(ctx, arg)
    except commands.MemberNotFound:
        try:
            target = await commands.UserConverter().convert(ctx, arg)
        except commands.UserNotFound:
            pass

    if target is None:
        return await ctx.channel.send(
            "❌ Argument invalide. Tape un **nombre 1-100** ou mentionne un **membre/ID**.",
            delete_after=6,
        )

    uid = target.id
    def check(m): return m.author.id == uid
    try:
        deleted = await ctx.channel.purge(limit=500, check=check)
        msg = await ctx.channel.send(
            f"✅ **{len(deleted)}** message(s) de **{target}** supprimé(s) "
            f"*(scan des 500 derniers messages)*.",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await asyncio.sleep(5)
        try: await msg.delete()
        except Exception: log.debug("clear feedback delete failed", exc_info=True)
    except discord.Forbidden:
        await ctx.channel.send("❌ Permissions insuffisantes.", delete_after=5)
    except Exception as e:
        log.exception("clear purge by user failed")
        await ctx.channel.send(f"❌ Erreur : {e}", delete_after=5)


# ============================================================
# PERMISSIONS — commandes
# ============================================================
@bot.command(name="setowner")
async def setowner(ctx, member: discord.Member = None):
    if not is_buyer(ctx.author.id):
        return await ctx.reply("❌ Réservé au **Buyer**.", mention_author=False, delete_after=5)
    if not member:
        return await ctx.reply(embed=discord.Embed(
            title="❌ Argument manquant",
            description=f"**Syntaxe :** `{PREFIX}setowner @membre`\n\nPromeut un membre au rang **Owner**.",
            color=0xed4245,
        ), mention_author=False)
    if member.id == BUYER_ID:
        return await ctx.reply("❌ Le buyer a déjà tous les droits.", mention_author=False)
    if member.id in data["owners"]:
        return await ctx.reply("❌ Déjà owner.", mention_author=False)
    await db_add_staff(member.id, "owner")
    e = discord.Embed(title="👑 Nouveau Owner", color=0xffd700, timestamp=discord.utils.utcnow())
    e.description = (
        f"{member.mention} est maintenant **Owner** !\n\n"
        f"**Permissions obtenues :**\n"
        f"✅ Gérer la whitelist (`{PREFIX}setwl` / `{PREFIX}removewl`)\n"
        f"✅ Setup les panels de tickets (`{PREFIX}ticketsetup`)\n"
        f"✅ Dérank un membre (`{PREFIX}derank`)\n"
        f"✅ + Toutes les commandes Whitelist"
    )
    e.set_thumbnail(url=member.display_avatar.url)
    e.set_footer(text=f"Par {ctx.author.display_name}")
    await ctx.reply(embed=e, mention_author=False)

@bot.command(name="removeowner", aliases=["unsetowner"])
async def removeowner(ctx, member: discord.Member = None):
    if not is_buyer(ctx.author.id):
        return await ctx.reply("❌ Réservé au **Buyer**.", mention_author=False, delete_after=5)
    if not member:
        return await ctx.reply(embed=discord.Embed(
            title="❌ Argument manquant",
            description=f"**Syntaxe :** `{PREFIX}removeowner @membre`",
            color=0xed4245,
        ), mention_author=False)
    if member.id not in data["owners"]:
        return await ctx.reply("❌ Pas owner.", mention_author=False)
    await db_remove_staff(member.id)
    e = discord.Embed(title="❌ Owner retiré", color=0xed4245, timestamp=discord.utils.utcnow())
    e.description = f"{member.mention} n'est plus **Owner**.\nToutes ses permissions admin ont été révoquées."
    e.set_thumbnail(url=member.display_avatar.url)
    e.set_footer(text=f"Par {ctx.author.display_name}")
    await ctx.reply(embed=e, mention_author=False)

@bot.command(name="setwl", aliases=["addwl"])
async def setwl(ctx, member: discord.Member = None):
    if not is_owner(ctx.author.id):
        return await ctx.reply("❌ Réservé **Owner+**.", mention_author=False, delete_after=5)
    if not member:
        return await ctx.reply(embed=discord.Embed(
            title="❌ Argument manquant",
            description=f"**Syntaxe :** `{PREFIX}setwl @membre`",
            color=0xed4245,
        ), mention_author=False)
    if member.id == BUYER_ID or member.id in data["owners"]:
        return await ctx.reply("❌ Déjà un rang supérieur.", mention_author=False)
    if member.id in data["wl"]:
        return await ctx.reply("❌ Déjà whitelist.", mention_author=False)
    await db_add_staff(member.id, "wl")
    e = discord.Embed(title="⭐ Whitelist +1", color=0x2ecc71, timestamp=discord.utils.utcnow())
    e.description = (
        f"{member.mention} est maintenant **Whitelist** !\n\n"
        f"**Accès débloqué :**\n"
        f"✅ Constructeur d'embed (`{PREFIX}embed`)\n"
        f"✅ Cloner des emojis (`{PREFIX}create`)\n"
        f"✅ Gérer les rôles (`{PREFIX}addrole` / `{PREFIX}delrole`)\n"
        f"✅ Nettoyer les salons (`{PREFIX}clear`)\n"
        f"✅ Voir le staff (`{PREFIX}staff`)"
    )
    e.set_thumbnail(url=member.display_avatar.url)
    e.set_footer(text=f"Par {ctx.author.display_name}")
    await ctx.reply(embed=e, mention_author=False)

@bot.command(name="removewl", aliases=["unsetwl"])
async def removewl(ctx, member: discord.Member = None):
    if not is_owner(ctx.author.id):
        return await ctx.reply("❌ Réservé **Owner+**.", mention_author=False, delete_after=5)
    if not member:
        return await ctx.reply(embed=discord.Embed(
            title="❌ Argument manquant",
            description=f"**Syntaxe :** `{PREFIX}removewl @membre`",
            color=0xed4245,
        ), mention_author=False)
    if member.id not in data["wl"]:
        return await ctx.reply("❌ Pas whitelist.", mention_author=False)
    await db_remove_staff(member.id)
    e = discord.Embed(title="❌ Whitelist retiré", color=0xe67e22, timestamp=discord.utils.utcnow())
    e.description = f"{member.mention} n'a plus accès aux commandes du bot."
    e.set_thumbnail(url=member.display_avatar.url)
    e.set_footer(text=f"Par {ctx.author.display_name}")
    await ctx.reply(embed=e, mention_author=False)


@bot.command(name="staff", aliases=["perms", "team"])
async def staff(ctx):
    e = discord.Embed(title="👑 Équipe du bot", color=0xffd700, timestamp=discord.utils.utcnow())
    try:
        buyer = ctx.guild.get_member(BUYER_ID) or await bot.fetch_user(BUYER_ID)
        bv = buyer.mention if hasattr(buyer, "mention") else str(buyer)
    except Exception:
        log.debug("buyer fetch failed", exc_info=True)
        bv = f"`{BUYER_ID}`"
    e.add_field(name="💎 Buyer", value=f"{bv}", inline=False)
    owners = [(ctx.guild.get_member(u).mention if ctx.guild.get_member(u) else f"`{u}`") for u in data["owners"]]
    e.add_field(name=f"👑 Owners ({len(owners)})", value="\n".join(f"⤷ {o}" for o in owners) or "*Aucun*", inline=False)
    wl = [(ctx.guild.get_member(u).mention if ctx.guild.get_member(u) else f"`{u}`") for u in data["wl"]]
    e.add_field(name=f"⭐ Whitelist ({len(wl)})", value="\n".join(f"⤷ {w}" for w in wl) or "*Aucun*", inline=False)
    e.set_footer(text=f"Ton rang : {get_rank_short(ctx.author.id)} • {len(data['owners']) + len(data['wl']) + 1} membre(s)")
    await ctx.reply(embed=e, mention_author=False)


# ============================================================
# TICKETS — commandes
# ============================================================
@bot.command(name="ticketsetup", aliases=["tsetup", "panel"])
async def ticket_setup(ctx, channel: discord.TextChannel = None):
    if not is_owner(ctx.author.id):
        return await ctx.reply("❌ Réservé **Owner+**.", mention_author=False, delete_after=5)
    view = TicketSetupView(ctx.author.id, ctx.guild.id)
    existing = await get_ticket_config(ctx.guild.id)
    view.session.categories = list(existing["categories"]) if existing["categories"] else list(DEFAULT_CATEGORIES)
    view.session.ping_roles = list(existing["ping_roles"])
    view.session.ping_users = list(existing["ping_users"])
    msg = await ctx.reply(
        content=f"**🛠️ Setup du panel tickets** — {ctx.author.mention}",
        embed=view.build_preview(), view=view, mention_author=False,
    )
    view.message = msg
    try: await ctx.message.delete()
    except Exception: log.debug("ticketsetup cleanup failed", exc_info=True)


@bot.command(name="close")
async def close_ticket(ctx):
    if not (ctx.channel.topic or "").startswith("ticket-"):
        return await ctx.reply("❌ Pas un salon de ticket.", mention_author=False, delete_after=5)
    topic = ctx.channel.topic or ""
    creator_id = None
    try: creator_id = int(topic.split("-", 1)[1])
    except (ValueError, IndexError): log.debug("invalid ticket topic")
    if ctx.author.id != creator_id and not has_any_perm(ctx.author.id):
        return await ctx.reply("❌ Tu ne peux pas fermer ce ticket.", mention_author=False, delete_after=5)
    await ctx.send(embed=discord.Embed(
        description=f"🔒 Fermeture par {ctx.author.mention} dans **5s**...",
        color=0xed4245,
    ))
    await asyncio.sleep(5)
    try: await ctx.channel.delete(reason=f"Fermé par {ctx.author}")
    except Exception: log.exception("close failed")


@bot.command(name="ticketadd", aliases=["tadd", "add"])
async def ticket_add(ctx, member: discord.Member = None):
    if not (ctx.channel.topic or "").startswith("ticket-"):
        return await ctx.reply("❌ Pas un salon de ticket.", mention_author=False, delete_after=5)
    if not is_wl(ctx.author.id): return
    if not member:
        return await ctx.reply(f"❌ `{PREFIX}ticketadd @membre`", mention_author=False)
    await ctx.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
    await ctx.reply(f"✅ {member.mention} ajouté au ticket.", mention_author=False)


@bot.command(name="ticketremove", aliases=["tremove", "remove"])
async def ticket_remove(ctx, member: discord.Member = None):
    if not (ctx.channel.topic or "").startswith("ticket-"):
        return await ctx.reply("❌ Pas un salon de ticket.", mention_author=False, delete_after=5)
    if not is_wl(ctx.author.id): return
    if not member:
        return await ctx.reply(f"❌ `{PREFIX}ticketremove @membre`", mention_author=False)
    await ctx.channel.set_permissions(member, overwrite=None)
    await ctx.reply(f"✅ {member.mention} retiré du ticket.", mention_author=False)


# ============================================================
# RÔLES — commandes
# ============================================================
@bot.command(name="addrole", aliases=["giverole", "ar"])
async def addrole_cmd(ctx, member: discord.Member = None, *, role: discord.Role = None):
    if not ctx.guild.me.guild_permissions.manage_roles:
        return await ctx.reply("❌ Perm **Gérer les rôles** manquante.", mention_author=False, delete_after=5)
    if not member or not role:
        return await ctx.reply(f"❌ Usage : `{PREFIX}addrole @membre @rôle`", mention_author=False)
    if member.id == BUYER_ID: return await ctx.reply("❌ Pas touche au **Buyer**.", mention_author=False, delete_after=5)
    if role.is_default(): return await ctx.reply("❌ Impossible avec `@everyone`.", mention_author=False, delete_after=5)
    if role.managed: return await ctx.reply("❌ Rôle géré par une intégration.", mention_author=False, delete_after=5)
    if role >= ctx.guild.me.top_role: return await ctx.reply("❌ Rôle au-dessus du mien.", mention_author=False, delete_after=5)
    if not is_buyer(ctx.author.id) and role >= ctx.author.top_role:
        return await ctx.reply("❌ Rôle au-dessus du tien.", mention_author=False, delete_after=5)
    if role in member.roles:
        return await ctx.reply("❌ A déjà ce rôle.", mention_author=False, delete_after=5)
    try:
        await member.add_roles(role, reason=f"+addrole par {ctx.author}")
        e = discord.Embed(description=f"✅ {role.mention} ajouté à {member.mention}", color=0x2ecc71)
        e.set_footer(text=f"Par {ctx.author}")
        await ctx.reply(embed=e, mention_author=False)
    except Exception as e:
        log.exception("addrole failed")
        await ctx.reply(f"❌ Erreur : {e}", mention_author=False, delete_after=5)


@bot.command(name="delrole", aliases=["removerole", "dr"])
async def delrole_cmd(ctx, member: discord.Member = None, *, role: discord.Role = None):
    if not ctx.guild.me.guild_permissions.manage_roles:
        return await ctx.reply("❌ Perm **Gérer les rôles** manquante.", mention_author=False, delete_after=5)
    if not member or not role:
        return await ctx.reply(f"❌ Usage : `{PREFIX}delrole @membre @rôle`", mention_author=False)
    if member.id == BUYER_ID: return await ctx.reply("❌ Pas touche au **Buyer**.", mention_author=False, delete_after=5)
    if role.is_default(): return await ctx.reply("❌ Impossible avec `@everyone`.", mention_author=False, delete_after=5)
    if role.managed: return await ctx.reply("❌ Rôle géré par une intégration.", mention_author=False, delete_after=5)
    if role >= ctx.guild.me.top_role: return await ctx.reply("❌ Rôle au-dessus du mien.", mention_author=False, delete_after=5)
    if not is_buyer(ctx.author.id) and role >= ctx.author.top_role:
        return await ctx.reply("❌ Rôle au-dessus du tien.", mention_author=False, delete_after=5)
    if role not in member.roles:
        return await ctx.reply("❌ N'a pas ce rôle.", mention_author=False, delete_after=5)
    try:
        await member.remove_roles(role, reason=f"+delrole par {ctx.author}")
        e = discord.Embed(description=f"✅ {role.mention} retiré de {member.mention}", color=0xe67e22)
        e.set_footer(text=f"Par {ctx.author}")
        await ctx.reply(embed=e, mention_author=False)
    except Exception as e:
        log.exception("delrole failed")
        await ctx.reply(f"❌ Erreur : {e}", mention_author=False, delete_after=5)


@bot.command(name="derank", aliases=["stripall", "wipe"])
async def derank_cmd(ctx, member: discord.Member = None):
    if not is_owner(ctx.author.id):
        return await ctx.reply("❌ Réservé **Owner+**.", mention_author=False, delete_after=5)
    if not ctx.guild.me.guild_permissions.manage_roles:
        return await ctx.reply("❌ Perm **Gérer les rôles** manquante.", mention_author=False, delete_after=5)
    if not member:
        return await ctx.reply(embed=discord.Embed(
            title="❌ Argument manquant",
            description=f"**Syntaxe :** `{PREFIX}derank @membre`",
            color=0xed4245,
        ), mention_author=False)
    if member.id == BUYER_ID:
        return await ctx.reply("❌ Pas touche au **Buyer**.", mention_author=False, delete_after=5)
    if member.id == ctx.guild.owner_id:
        return await ctx.reply("❌ Impossible de dérank le **propriétaire** du serveur.", mention_author=False, delete_after=5)
    if member.id == ctx.author.id:
        return await ctx.reply("❌ Pas toi-même.", mention_author=False, delete_after=5)
    if member.top_role >= ctx.guild.me.top_role:
        return await ctx.reply("❌ Membre au-dessus du bot.", mention_author=False, delete_after=5)
    if not is_buyer(ctx.author.id) and member.top_role >= ctx.author.top_role:
        return await ctx.reply("❌ Membre au-dessus ou égal à toi.", mention_author=False, delete_after=5)
    bot_top = ctx.guild.me.top_role
    removable = [r for r in member.roles if not r.is_default() and not r.managed and r < bot_top]
    if not removable:
        return await ctx.reply("❌ Aucun rôle retirable.", mention_author=False, delete_after=5)
    msg = await ctx.reply(f"⏳ Retrait de **{len(removable)}** rôle(s)...", mention_author=False)
    try:
        await member.remove_roles(*removable, reason=f"+derank par {ctx.author}")
        rl = ", ".join(r.mention for r in removable[:15])
        if len(removable) > 15: rl += f" *(+{len(removable)-15} autres)*"
        e = discord.Embed(
            title="💥 Dérank",
            description=f"**{len(removable)}** rôle(s) retiré(s) de {member.mention}\n\n{rl}",
            color=0xed4245,
        )
        e.set_footer(text=f"Par {ctx.author}")
        e.timestamp = discord.utils.utcnow()
        await msg.edit(content=None, embed=e)
    except Exception as e:
        log.exception("derank failed")
        await msg.edit(content=f"❌ Erreur : {e}")


# ============================================================
# PANNEAU D'ADMINISTRATION (+admin)
# ============================================================
ADMIN_PAGES = {
    "perms": {
        "emoji": "👑", "title": "Gestion des Permissions", "color": 0xffd700,
        "content": (
            f"*Gérer qui a accès au bot.*\n\n"
            f"💎 **`{PREFIX}setowner @membre`** — Promouvoir en Owner\n"
            f">>> Rang requis : **Buyer**\n"
            f"Donne les permissions admin (whitelist, tickets, dérank).\n\n"
            f"💎 **`{PREFIX}removeowner @membre`** — Retirer Owner\n"
            f">>> Rang requis : **Buyer**\n\n"
            f"👑 **`{PREFIX}setwl @membre`** — Ajouter à la whitelist\n"
            f">>> Rang requis : **Owner+**\n\n"
            f"👑 **`{PREFIX}removewl @membre`** — Retirer de la whitelist\n"
            f">>> Rang requis : **Owner+**"
        ),
    },
    "tickets": {
        "emoji": "🎫", "title": "Système de Tickets", "color": 0x5865f2,
        "content": (
            f"*Système de support avec catégories Team 17\".*\n\n"
            f"👑 **`{PREFIX}ticketsetup`** — Créer un panel de tickets\n"
            f">>> Rang requis : **Owner+**\n"
            f"Constructeur interactif avec menu déroulant.\n"
            f"Catégories par défaut :\n"
            f"🟢 Rejoindre • 🔴 Quitter • 🟡 Aide • ⚠️ Abus\n\n"
            f"Chaque catégorie peut avoir son **propre message d'accueil**.\n\n"
            f"🔔 **Ping à l'ouverture** : configure des rôles ou membres "
            f"à ping automatiquement quand un ticket est créé.\n\n"
            f"⭐ **`{PREFIX}close`** — Fermer un ticket *(WL+ ou créateur)*\n"
            f"⭐ **`{PREFIX}ticketadd @membre`** — Ajouter au ticket *(WL+)*\n"
            f"⭐ **`{PREFIX}ticketremove @membre`** — Retirer du ticket *(WL+)*\n\n"
            f"**Dans chaque ticket :**\n"
            f"🔒 **Fermer** • ✋ **Prendre en charge**"
        ),
    },
    "roles": {
        "emoji": "🎭", "title": "Gestion des Rôles", "color": 0xe67e22,
        "content": (
            f"*Ajouter, retirer ou purger les rôles.*\n\n"
            f"⭐ **`{PREFIX}addrole @membre @rôle`** — Donner un rôle\n"
            f"⭐ **`{PREFIX}delrole @membre @rôle`** — Retirer un rôle\n"
            f"👑 **`{PREFIX}derank @membre`** — Retirer **tous** les rôles *(Owner+)*"
        ),
    },
    "moderation": {
        "emoji": "🛡️", "title": "Modération", "color": 0xed4245,
        "content": (
            f"*Nettoyer les salons rapidement.*\n\n"
            f"⭐ **`{PREFIX}clear <1-100>`** — Supprime les N derniers messages\n"
            f"⭐ **`{PREFIX}clear @membre`** — Supprime tous ses messages "
            f"*(parmi les 500 derniers)*\n"
            f"⭐ **`{PREFIX}clear <user_id>`** — Idem, même si la personne n'est plus là\n\n"
            f"> ⚠️ Discord empêche la suppression bulk des messages > 14 jours."
        ),
    },
    "utils": {
        "emoji": "🛠️", "title": "Utilitaires", "color": 0x3498db,
        "content": (
            f"⭐ **`{PREFIX}embed`** — Constructeur d'embed interactif\n"
            f"⭐ **`{PREFIX}create <emojis>`** — Cloner des emojis\n"
            f"⭐ **`{PREFIX}staff`** — Voir l'équipe (Buyer/Owners/WL)"
        ),
    },
    "hierarchy": {
        "emoji": "📊", "title": "Hiérarchie des Rangs", "color": 0x9b59b6,
        "content": (
            f"**💎 Buyer** — Contrôle total\n"
            f"**👑 Owner** — Admin (promu par le Buyer)\n"
            f"**⭐ Whitelist** — Accès bot (ajouté par un Owner)\n"
            f"**🔒 Aucun rang** — Pas d'accès, commandes ignorées"
        ),
    },
}


class AdminSelect(ui.Select):
    def __init__(self):
        opts = [SelectOption(label=d["title"][:25], value=k, emoji=d["emoji"]) for k, d in ADMIN_PAGES.items()]
        super().__init__(placeholder="📂 Choisis une catégorie...", options=opts)

    async def callback(self, interaction):
        page = ADMIN_PAGES[self.values[0]]
        embed = discord.Embed(
            title=f"{page['emoji']}  {page['title']}",
            description=page["content"],
            color=page.get("color", 0x5865f2),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"Panneau Admin • {interaction.user.display_name}")
        await interaction.response.edit_message(embed=embed)


@bot.command(name="admin")
async def admin_cmd(ctx):
    rank = get_rank(ctx.author.id)
    e = discord.Embed(title="⚙️ Panneau d'Administration", color=0x5865f2, timestamp=discord.utils.utcnow())
    e.description = f"*Toutes les commandes admin expliquées en détail.*\n\n**Ton rang :** {rank}"
    e.add_field(name="💎 Buyer", value="`setowner` `removeowner`", inline=True)
    e.add_field(name="👑 Owner+", value="`setwl` `removewl`\n`ticketsetup` `derank`", inline=True)
    e.add_field(name="⭐ Whitelist+", value="`embed` `create` `clear`\n`addrole` `delrole` `staff`", inline=True)
    e.set_footer(text=f"Rang : {get_rank_short(ctx.author.id)}")
    view = ui.View(timeout=180); view.add_item(AdminSelect())
    await ctx.reply(embed=e, view=view, mention_author=False)


# ============================================================
# HELP — Menu déroulant
# ============================================================
HELP_PAGES = {
    "overview": {
        "emoji": "🏠", "title": "Vue d'ensemble", "color": 0x5865f2,
        "content": lambda ctx: (
            f"*Sélectionne une catégorie ci-dessous pour voir les commandes en détail.*\n\n"
            f"**Préfixe :** `{PREFIX}`\n"
            f"**Ton rang :** {get_rank(ctx.author.id)}\n\n"
            f"📂 **Catégories disponibles :**\n"
            f"🛠️ Utilitaires • 🎫 Tickets • 🎭 Rôles • 🛡️ Modération\n"
            f"👑 Permissions • 📊 Hiérarchie\n\n"
            f"💡 *Tu peux aussi taper `{PREFIX}admin` pour le panneau admin complet.*"
        ),
    },
    "utils": {
        "emoji": "🛠️", "title": "Utilitaires", "color": 0x3498db,
        "content": lambda ctx: (
            f"*Outils pratiques pour gérer le serveur.*\n\n"
            f"**`{PREFIX}embed`** — Constructeur d'embed interactif\n"
            f"⤷ Menu déroulant pour personnaliser titre, couleur, image...\n\n"
            f"**`{PREFIX}create <emojis>`** — Cloner des emojis sur le serveur\n"
            f"⤷ Ex : `{PREFIX}create <:nom:1234567890>` *(plusieurs OK)*\n\n"
            f"**`{PREFIX}staff`** — Voir l'équipe complète (Buyer/Owners/WL)"
        ),
    },
    "tickets": {
        "emoji": "🎫", "title": "Système de Tickets", "color": 0x5865f2,
        "content": lambda ctx: (
            f"*Système de support avec catégories personnalisables.*\n\n"
            f"**`{PREFIX}ticketsetup`** *(Owner+)*\n"
            f"⤷ Constructeur interactif du panel de tickets\n"
            f"⤷ Catégories, message d'accueil, **pings auto** (rôles/membres)\n\n"
            f"**`{PREFIX}close`** — Fermer le ticket actuel\n"
            f"**`{PREFIX}ticketadd @membre`** — Ajouter quelqu'un au ticket\n"
            f"**`{PREFIX}ticketremove @membre`** — Retirer quelqu'un\n\n"
            f"**Dans chaque ticket :**\n"
            f"🔒 Bouton fermer • ✋ Bouton prendre en charge"
        ),
    },
    "roles": {
        "emoji": "🎭", "title": "Gestion des Rôles", "color": 0xe67e22,
        "content": lambda ctx: (
            f"*Ajouter, retirer ou purger les rôles d'un membre.*\n\n"
            f"**`{PREFIX}addrole @membre @rôle`** — Donner un rôle\n"
            f"**`{PREFIX}delrole @membre @rôle`** — Retirer un rôle\n"
            f"**`{PREFIX}derank @membre`** *(Owner+)* — Retirer **tous** les rôles\n\n"
            f"> ⚠️ Le bot ne peut gérer que les rôles **en-dessous** du sien."
        ),
    },
    "moderation": {
        "emoji": "🛡️", "title": "Modération", "color": 0xed4245,
        "content": lambda ctx: (
            f"*Nettoyer les salons.*\n\n"
            f"**`{PREFIX}clear <1-100>`** — Supprime les N derniers messages\n"
            f"⤷ Ex : `{PREFIX}clear 20` → supprime les 20 derniers\n\n"
            f"**`{PREFIX}clear @membre`** — Supprime tous ses messages\n"
            f"⤷ Scan des 500 derniers messages du salon\n\n"
            f"**`{PREFIX}clear <user_id>`** — Idem si la personne est partie\n"
            f"⤷ Ex : `{PREFIX}clear 123456789012345678`\n\n"
            f"> ⚠️ Discord ne permet pas la suppression bulk de messages > 14 jours."
        ),
    },
    "perms": {
        "emoji": "👑", "title": "Permissions", "color": 0xffd700,
        "content": lambda ctx: (
            f"*Gérer l'accès au bot.*\n\n"
            f"💎 **Buyer uniquement :**\n"
            f"**`{PREFIX}setowner @membre`** — Promouvoir Owner\n"
            f"**`{PREFIX}removeowner @membre`** — Retirer Owner\n\n"
            f"👑 **Owner+ :**\n"
            f"**`{PREFIX}setwl @membre`** — Ajouter en Whitelist\n"
            f"**`{PREFIX}removewl @membre`** — Retirer de la Whitelist\n\n"
            f"> 💡 *Voir `{PREFIX}staff` pour la liste complète.*"
        ),
    },
    "hierarchy": {
        "emoji": "📊", "title": "Hiérarchie des Rangs", "color": 0x9b59b6,
        "content": lambda ctx: (
            f"**💎 Buyer** — Contrôle total\n"
            f"⤷ Seul rang capable de promouvoir des Owners\n\n"
            f"**👑 Owner** — Admin\n"
            f"⤷ Gère la whitelist, setup les tickets, dérank\n\n"
            f"**⭐ Whitelist** — Accès bot\n"
            f"⤷ Toutes les commandes utilitaires + modération\n\n"
            f"**🔒 Aucun rang** — Pas d'accès\n"
            f"⤷ Les commandes sont ignorées silencieusement\n\n"
            f"**Ton rang actuel :** {get_rank(ctx.author.id)}"
        ),
    },
}


class HelpSelect(ui.Select):
    def __init__(self, ctx):
        self.ctx = ctx
        opts = [SelectOption(label=d["title"][:25], value=k, emoji=d["emoji"]) for k, d in HELP_PAGES.items()]
        super().__init__(placeholder="📂 Choisis une catégorie...", options=opts)

    async def callback(self, interaction):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message(
                "❌ Utilise `+help` toi-même.", ephemeral=True,
            )
        page = HELP_PAGES[self.values[0]]
        embed = discord.Embed(
            title=f"{page['emoji']}  {page['title']}",
            description=page["content"](self.ctx),
            color=page.get("color", 0x5865f2),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"Bot Team 17\" • Rang : {get_rank_short(self.ctx.author.id)}")
        await interaction.response.edit_message(embed=embed)


class HelpView(ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.add_item(HelpSelect(ctx))


@bot.command(name="help", aliases=["h", "commands"])
async def help_cmd(ctx):
    page = HELP_PAGES["overview"]
    e = discord.Embed(
        title=f"{page['emoji']}  {page['title']}",
        description=page["content"](ctx),
        color=page.get("color", 0x5865f2),
        timestamp=discord.utils.utcnow(),
    )
    e.set_footer(text=f"Bot Team 17\" • Rang : {get_rank_short(ctx.author.id)}")
    view = HelpView(ctx)
    await ctx.reply(embed=e, view=view, mention_author=False)


# ============================================================
# EVENTS
# ============================================================
@bot.event
async def on_message(message):
    if message.author.bot: return
    if message.author.id in EDITING_USERS: return
    await bot.process_commands(message)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        return
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CommandOnCooldown):
        return await ctx.reply(
            f"⏳ Attends **{error.retry_after:.1f}s** avant de réutiliser cette commande.",
            mention_author=False, delete_after=5,
        )
    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.reply(embed=discord.Embed(
            description=f"❌ Argument manquant : `{error.param.name}`\n\nTape `{PREFIX}help` ou `{PREFIX}admin`.",
            color=0xed4245,
        ), mention_author=False, delete_after=10)
    if isinstance(error, commands.MemberNotFound):
        return await ctx.reply("❌ Membre introuvable.", mention_author=False, delete_after=5)
    if isinstance(error, commands.RoleNotFound):
        return await ctx.reply("❌ Rôle introuvable.", mention_author=False, delete_after=5)
    if isinstance(error, commands.BadArgument):
        return await ctx.reply("❌ Argument invalide.", mention_author=False, delete_after=5)
    log.exception("uncaught command error: %s", error)


@bot.event
async def on_ready():
    log.info("✅ Connecté : %s | Buyer : %s", bot.user, BUYER_ID)
    log.info("📊 %d serveur(s) | %d owners | %d wl",
             len(bot.guilds), len(data["owners"]), len(data["wl"]))
    await bot.change_presence(activity=discord.Game(name=f"{PREFIX}help"))


if __name__ == "__main__":
    bot.run(TOKEN)
