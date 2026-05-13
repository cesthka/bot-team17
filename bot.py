import re
import os
import asyncio
import aiohttp
import asyncpg
import discord
from discord.ext import commands
from discord import ui, ButtonStyle, SelectOption

# ============================================================
# CONFIG — Variables d'environnement (.env / VPS)
# ============================================================
# Charge .env si présent (pip install python-dotenv)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TOKEN        = os.environ.get("TOKEN") or os.environ.get("DISCORD_TOKEN")
PREFIX       = os.environ.get("PREFIX", "+")
BUYER_ID     = int(os.environ.get("BUYER_ID", "0"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# asyncpg veut postgresql://, pas postgres://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if not TOKEN:
    raise RuntimeError("❌ TOKEN manquant ! Définis la variable d'environnement TOKEN.")
if not BUYER_ID:
    print("⚠️ BUYER_ID non défini — aucune commande ne fonctionnera.")
if not DATABASE_URL:
    raise RuntimeError("❌ DATABASE_URL manquant ! Définis DATABASE_URL (postgresql://...).")

# ============================================================
# CACHE EN MÉMOIRE (rechargé depuis la DB au démarrage)
# ============================================================
data = {"owners": [], "wl": []}

# ============================================================
# BOT
# ============================================================
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
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS staff (
                user_id BIGINT PRIMARY KEY,
                rank    TEXT NOT NULL
            )
        """)
        rows = await self.db.fetch("SELECT user_id, rank FROM staff")
        for r in rows:
            if r["rank"] == "owner":
                data["owners"].append(r["user_id"])
            elif r["rank"] == "wl":
                data["wl"].append(r["user_id"])
        print(f"📦 DB chargée : {len(data['owners'])} owners, {len(data['wl'])} wl")

        self.add_view(TicketPanelView())
        self.add_view(TicketControlView())


bot = TeamBot()

EMOJI_REGEX = re.compile(r"<(a?):([a-zA-Z0-9_]+):(\d+)>")
EDITING_USERS = set()


# ============================================================
# DB HELPERS
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
    return None

def get_rank_short(uid):
    if is_buyer(uid): return "Buyer"
    if uid in data["owners"]: return "Owner"
    if uid in data["wl"]: return "Whitelist"
    return None

@bot.check
async def global_perm_check(ctx):
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
        title, hint = PROMPTS[choice]
        prompt = discord.Embed(title=title, description=hint, color=0x5865f2)
        prompt.set_footer(text=f"💡 3 min pour répondre • {interaction.user.display_name}")
        await interaction.response.send_message(embed=prompt)
        prompt_msg = await interaction.original_response()

        def check(m): return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id

        try:
            user_msg = await bot.wait_for("message", check=check, timeout=180)
        except asyncio.TimeoutError:
            view.is_editing = False; EDITING_USERS.discard(interaction.user.id)
            try:
                await prompt_msg.edit(embed=discord.Embed(description="⏰ Temps écoulé.", color=0xed4245))
                await asyncio.sleep(3); await prompt_msg.delete()
            except: pass
            try: await interaction.message.edit(view=view)
            except: pass
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
        except: pass
        try: await user_msg.delete()
        except: pass

        view.is_editing = False; EDITING_USERS.discard(interaction.user.id)

        if error:
            try:
                err = await interaction.channel.send(error)
                await asyncio.sleep(4); await err.delete()
            except: pass

        try: await interaction.message.edit(embed=view.session.build(), view=view)
        except: pass

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
# TICKETS — Système complet avec catégories
# ============================================================

TICKET_CATEGORIES = {
    "support":     {"emoji": "🆘", "label": "Support général",  "desc": "Besoin d'aide ou une question"},
    "bug":         {"emoji": "🐛", "label": "Signaler un bug",  "desc": "Quelque chose ne fonctionne pas"},
    "partenariat": {"emoji": "🤝", "label": "Partenariat",      "desc": "Proposer une collaboration"},
    "suggestion":  {"emoji": "💡", "label": "Suggestion",        "desc": "Proposer une idée ou amélioration"},
    "autre":       {"emoji": "📩", "label": "Autre",             "desc": "Autre demande"},
}


async def create_ticket_channel(interaction, category_key):
    """Crée le salon de ticket avec la catégorie choisie."""
    guild = interaction.guild
    user = interaction.user
    cat_info = TICKET_CATEGORIES.get(category_key, TICKET_CATEGORIES["autre"])

    # Vérifier si déjà un ticket ouvert
    existing = discord.utils.find(lambda c: c.topic and c.topic.startswith(f"ticket-{user.id}"), guild.text_channels)
    if existing:
        return await interaction.response.send_message(f"❌ Tu as déjà un ticket ouvert : {existing.mention}", ephemeral=True)

    # Catégorie Discord
    disc_category = discord.utils.get(guild.categories, name="🎫 Tickets")
    if not disc_category:
        try:
            disc_category = await guild.create_category("🎫 Tickets")
        except discord.Forbidden:
            return await interaction.response.send_message("❌ Pas la permission de créer la catégorie.", ephemeral=True)

    # Permissions
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, embed_links=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_messages=True),
    }
    for uid in [BUYER_ID] + data["owners"] + data["wl"]:
        m = guild.get_member(uid)
        if m:
            overwrites[m] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True, attach_files=True, embed_links=True, read_message_history=True)

    # Créer le salon
    channel_name = f"ticket-{user.name}"
    try:
        channel = await guild.create_text_channel(
            name=channel_name, category=disc_category,
            overwrites=overwrites, topic=f"ticket-{user.id}-{category_key}",
            reason=f"Ticket ({cat_info['label']}) par {user}"
        )
    except discord.Forbidden:
        return await interaction.response.send_message("❌ Pas la permission de créer le salon.", ephemeral=True)

    # Embed d'ouverture
    embed = discord.Embed(color=0x5865f2, timestamp=discord.utils.utcnow())
    embed.set_author(name=f"Ticket — {cat_info['label']}", icon_url=user.display_avatar.url)
    embed.description = (
        f"Salut {user.mention} ! 👋\n\n"
        f"**Catégorie :** {cat_info['emoji']} {cat_info['label']}\n"
        f"**Ouvert par :** {user.mention}\n\n"
        f"Un membre du **staff** va te répondre rapidement.\n"
        f"Décris ton problème en attendant."
    )
    embed.set_footer(text=f"{user} • {user.id}")
    embed.set_thumbnail(url=user.display_avatar.url)

    await channel.send(content=f"{user.mention}", embed=embed, view=TicketControlView())
    await interaction.response.send_message(f"✅ Ton ticket a été créé : {channel.mention}", ephemeral=True)


# ----- Vue du panel (ce que les users voient) -----
class TicketCategorySelect(ui.Select):
    def __init__(self):
        opts = [
            SelectOption(
                label=info["label"], value=key,
                emoji=info["emoji"], description=info["desc"]
            )
            for key, info in TICKET_CATEGORIES.items()
        ]
        super().__init__(placeholder="📂 Choisis une raison...", options=opts, custom_id="ticket_category_select")

    async def callback(self, interaction):
        await create_ticket_channel(interaction, self.values[0])


class TicketPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketCategorySelect())


# ----- Contrôles dans le ticket (close + claim) -----
class TicketControlView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Fermer", style=ButtonStyle.danger, emoji="🔒", custom_id="ticket_close_btn", row=0)
    async def close_ticket(self, interaction, _):
        topic = interaction.channel.topic or ""
        creator_id = None
        if topic.startswith("ticket-"):
            try:
                creator_id = int(topic.split("-")[1])
            except: pass
        if interaction.user.id != creator_id and not has_any_perm(interaction.user.id):
            return await interaction.response.send_message("❌ Tu ne peux pas fermer ce ticket.", ephemeral=True)
        await interaction.response.send_message(
            embed=discord.Embed(description="⚠️ **Es-tu sûr de vouloir fermer ce ticket ?**\nCette action est irréversible.", color=0xed4245),
            view=TicketConfirmCloseView()
        )

    @ui.button(label="Prendre en charge", style=ButtonStyle.success, emoji="✋", custom_id="ticket_claim_btn", row=0)
    async def claim_ticket(self, interaction, _):
        if not has_any_perm(interaction.user.id):
            return await interaction.response.send_message("❌ Réservé au staff.", ephemeral=True)
        embed = discord.Embed(
            description=f"✋ **{interaction.user.mention}** a pris en charge ce ticket.",
            color=0x2ecc71
        )
        embed.timestamp = discord.utils.utcnow()
        await interaction.response.send_message(embed=embed)
        # Renommer le salon pour indiquer qui gère
        try:
            current_name = interaction.channel.name
            if not current_name.startswith("✅"):
                await interaction.channel.edit(name=f"✅-{current_name}")
        except: pass


class TicketConfirmCloseView(ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @ui.button(label="Confirmer la fermeture", style=ButtonStyle.danger, emoji="✅")
    async def confirm(self, interaction, _):
        embed = discord.Embed(
            description=f"🔒 Fermé par {interaction.user.mention}.\nSuppression dans **5 secondes**...",
            color=0xed4245
        )
        embed.timestamp = discord.utils.utcnow()
        await interaction.response.send_message(embed=embed)
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Ticket fermé par {interaction.user}")
        except: pass

    @ui.button(label="Annuler", style=ButtonStyle.secondary, emoji="❌")
    async def cancel(self, interaction, _):
        await interaction.response.edit_message(
            embed=discord.Embed(description="❌ Fermeture annulée.", color=0x95a5a6), view=None
        )


# ----- Builder du panel (+ticketsetup) -----
class TicketSetupSession:
    """Config du panel qu'on construit."""
    def __init__(self):
        self.title = "🎫 Support — Ouvrir un ticket"
        self.description = "Besoin d'aide, d'une info, ou tu veux signaler quelque chose ?\n\n**Choisis une catégorie** dans le menu ci-dessous pour ouvrir un ticket privé avec le staff.\n\n> ⚠️ *N'ouvre pas de ticket pour rien.*"
        self.color = 0x5865f2
        self.image = None
        self.thumbnail = None
        self.footer = "Le staff te répondra dès que possible."

    def build(self):
        e = discord.Embed(title=self.title, description=self.description, color=self.color)
        if self.image: e.set_image(url=self.image)
        if self.thumbnail: e.set_thumbnail(url=self.thumbnail)
        if self.footer: e.set_footer(text=self.footer)
        return e


TICKET_PROMPTS = {
    "title":       ("📝 Titre du panel", "Tape le titre (max **256** caractères). `rien` pour retirer."),
    "description": ("📄 Description du panel", "Ce que les utilisateurs voient au-dessus du menu. `rien` pour retirer."),
    "color":       ("🎨 Couleur du panel", "Exemples : `rouge`, `bleu`, `vert`, `gold`, `discord`..."),
    "footer":      ("🔻 Footer du panel", "Texte en bas du panel. `rien` pour retirer."),
    "image":       ("🖼️ Image du panel", "Colle une **URL** ou **upload** le fichier. `rien` pour retirer."),
    "thumbnail":   ("🌄 Thumbnail du panel", "Petite image haut-droite. **URL** ou **upload**. `rien` pour retirer."),
}

class TicketSetupSelect(ui.Select):
    def __init__(self):
        opts = [
            SelectOption(label="Titre",       value="title",       emoji="📝", description="Modifier le titre"),
            SelectOption(label="Description", value="description", emoji="📄", description="Modifier la description"),
            SelectOption(label="Couleur",     value="color",       emoji="🎨", description="Changer la couleur"),
            SelectOption(label="Footer",      value="footer",      emoji="🔻", description="Texte du bas"),
            SelectOption(label="Image",       value="image",       emoji="🖼️", description="Grande image"),
            SelectOption(label="Thumbnail",   value="thumbnail",   emoji="🌄", description="Petite image haut-droite"),
            SelectOption(label="Reset",       value="reset",       emoji="🔄", description="Tout réinitialiser"),
        ]
        super().__init__(placeholder="🛠️ Personnalise le panel...", options=opts)

    async def callback(self, interaction):
        view = self.view
        if interaction.user.id != view.author_id:
            return await interaction.response.send_message("❌ Pas ton setup.", ephemeral=True)
        if view.is_editing:
            return await interaction.response.send_message("⏳ Déjà en train d'éditer.", ephemeral=True)

        choice = self.values[0]

        if choice == "reset":
            view.session = TicketSetupSession()
            return await interaction.response.edit_message(embed=view.build_preview(), view=view)

        view.is_editing = True
        EDITING_USERS.add(interaction.user.id)
        title, hint = TICKET_PROMPTS[choice]
        prompt = discord.Embed(title=title, description=hint, color=0x5865f2)
        prompt.set_footer(text=f"💡 3 min pour répondre • {interaction.user.display_name}")
        await interaction.response.send_message(embed=prompt)
        prompt_msg = await interaction.original_response()

        def check(m):
            return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id

        try:
            user_msg = await bot.wait_for("message", check=check, timeout=180)
        except asyncio.TimeoutError:
            view.is_editing = False; EDITING_USERS.discard(interaction.user.id)
            try:
                await prompt_msg.edit(embed=discord.Embed(description="⏰ Temps écoulé.", color=0xed4245))
                await asyncio.sleep(3); await prompt_msg.delete()
            except: pass
            return

        content = user_msg.content.strip()
        clear = content.lower() in {"rien", "none", "clear", "supprimer", "delete", "vide", "remove"}
        error = None

        if choice == "title":
            if clear: view.session.title = None
            elif len(content) > 256: error = "❌ Titre trop long (max 256)."
            else: view.session.title = content
        elif choice == "description":
            if clear: view.session.description = None
            elif len(content) > 4000: error = "❌ Description trop longue."
            else: view.session.description = content
        elif choice == "color":
            if clear: view.session.color = 0x5865f2
            else:
                key = normalize_color_name(content)
                if key in COLORS: view.session.color = COLORS[key]
                else: error = f"❌ Couleur inconnue : `{content}`."
        elif choice == "footer":
            if clear: view.session.footer = None
            elif len(content) > 2048: error = "❌ Footer trop long."
            else: view.session.footer = content
        elif choice in ("image", "thumbnail"):
            url = None
            if clear: pass
            elif user_msg.attachments: url = user_msg.attachments[0].url
            elif content.startswith(("http://", "https://")): url = content
            else: error = "❌ URL invalide ou pièce jointe absente."
            if not error: setattr(view.session, choice, url)

        try: await prompt_msg.delete()
        except: pass
        try: await user_msg.delete()
        except: pass

        view.is_editing = False; EDITING_USERS.discard(interaction.user.id)

        if error:
            try:
                err = await interaction.channel.send(error)
                await asyncio.sleep(4); await err.delete()
            except: pass

        try: await interaction.message.edit(embed=view.build_preview(), view=view)
        except: pass


class TicketSetupChannelSelect(ui.View):
    def __init__(self, session, author_id):
        super().__init__(timeout=120)
        self.session = session; self.author_id = author_id

    @ui.select(cls=ui.ChannelSelect, channel_types=[discord.ChannelType.text], placeholder="📤 Salon de destination")
    async def select_channel(self, interaction, select):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ Pas ton setup.", ephemeral=True)
        channel = select.values[0]
        try:
            real = interaction.guild.get_channel(channel.id) or await interaction.guild.fetch_channel(channel.id)
            await real.send(embed=self.session.build(), view=TicketPanelView())
            await interaction.response.edit_message(content=f"✅ Panel envoyé dans {real.mention} !", embed=None, view=None)
        except Exception as e:
            await interaction.response.edit_message(content=f"❌ Erreur : {e}", view=None)


class TicketSetupView(ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=600)
        self.author_id = author_id
        self.session = TicketSetupSession()
        self.is_editing = False
        self.add_item(TicketSetupSelect())

    def build_preview(self):
        """Embed de preview avec le rendu + note."""
        preview = self.session.build()
        return preview

    @ui.button(label="✅ Envoyer le panel", style=ButtonStyle.success, row=1)
    async def btn_send(self, interaction, _):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ Pas ton setup.", ephemeral=True)
        await interaction.response.send_message(
            "📤 Dans quel salon veux-tu envoyer le panel ?",
            view=TicketSetupChannelSelect(self.session, self.author_id),
            ephemeral=True
        )

    @ui.button(label="📤 Envoyer ici", style=ButtonStyle.primary, row=1)
    async def btn_send_here(self, interaction, _):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ Pas ton setup.", ephemeral=True)
        await interaction.channel.send(embed=self.session.build(), view=TicketPanelView())
        await interaction.response.edit_message(content="✅ Panel envoyé dans ce salon !", embed=None, view=None)
        self.stop()

    @ui.button(label="❌ Annuler", style=ButtonStyle.danger, row=1)
    async def btn_cancel(self, interaction, _):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ Pas ton setup.", ephemeral=True)
        await interaction.response.edit_message(content="❌ Setup annulé.", embed=None, view=None)
        self.stop()


# ============================================================
# COMMANDES
# ============================================================
@bot.command(name="embed")
async def embed_cmd(ctx):
    view = EmbedView(ctx.author.id)
    await ctx.reply(content=f"**🛠️ Constructeur d'embed** — {ctx.author.mention}",
                    embed=view.session.build(), view=view, mention_author=False)


@bot.command(name="create", aliases=["steal", "addemoji"])
async def create_emoji(ctx, *, args: str = None):
    if not ctx.author.guild_permissions.manage_expressions:
        return await ctx.reply("❌ Perm **Gérer les emojis** manquante.", mention_author=False, delete_after=5)
    if not ctx.guild.me.guild_permissions.manage_expressions:
        return await ctx.reply("❌ Il me faut **Gérer les emojis**.", mention_author=False, delete_after=5)
    if not args:
        return await ctx.reply("❌ `+create <:emoji:id> ...`", mention_author=False)

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
            except Exception:
                failed.append(f"`{name}`")

    lines = []
    if added: lines.append(f"✅ **{len(added)} ajouté(s)** : {' '.join(added)}")
    if failed: lines.append(f"❌ **{len(failed)} échec(s)** : {', '.join(failed)}")
    await status.edit(content="\n".join(lines) or "❌ Rien ajouté.")


# ============================================================
# COMMANDES PERMISSIONS (améliorées)
# ============================================================
@bot.command(name="setowner")
async def setowner(ctx, member: discord.Member = None):
    if not is_buyer(ctx.author.id): return
    if not member:
        return await ctx.reply(embed=discord.Embed(
            title="❌ Argument manquant",
            description=f"**Syntaxe :** `{PREFIX}setowner @membre`\n\nPromeut un membre au rang **Owner**.\nIl pourra gérer la whitelist, les tickets, les rôles et le dérank.",
            color=0xed4245
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
    if not is_buyer(ctx.author.id): return
    if not member:
        return await ctx.reply(embed=discord.Embed(
            title="❌ Argument manquant",
            description=f"**Syntaxe :** `{PREFIX}removeowner @membre`\n\nRetire le rang **Owner** à un membre.",
            color=0xed4245
        ), mention_author=False)
    if member.id not in data["owners"]:
        return await ctx.reply("❌ Pas owner.", mention_author=False)
    await db_remove_staff(member.id)
    e = discord.Embed(title="❌ Owner retiré", color=0xed4245, timestamp=discord.utils.utcnow())
    e.description = (
        f"{member.mention} n'est plus **Owner**.\n\n"
        f"**Permissions révoquées :**\n"
        f"🔒 Gestion whitelist\n"
        f"🔒 Setup tickets\n"
        f"🔒 Dérank\n"
        f"🔒 Toutes les commandes staff"
    )
    e.set_thumbnail(url=member.display_avatar.url)
    e.set_footer(text=f"Par {ctx.author.display_name}")
    await ctx.reply(embed=e, mention_author=False)

@bot.command(name="setwl", aliases=["addwl"])
async def setwl(ctx, member: discord.Member = None):
    if not is_owner(ctx.author.id): return
    if not member:
        return await ctx.reply(embed=discord.Embed(
            title="❌ Argument manquant",
            description=f"**Syntaxe :** `{PREFIX}setwl @membre`\n\nAjoute un membre à la **Whitelist**.\nIl pourra utiliser toutes les commandes de base du bot.",
            color=0xed4245
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
        f"✅ Ajouter/retirer dans les tickets\n"
        f"✅ Voir le staff (`{PREFIX}staff`)"
    )
    e.set_thumbnail(url=member.display_avatar.url)
    e.set_footer(text=f"Par {ctx.author.display_name}")
    await ctx.reply(embed=e, mention_author=False)

@bot.command(name="removewl", aliases=["unsetwl"])
async def removewl(ctx, member: discord.Member = None):
    if not is_owner(ctx.author.id): return
    if not member:
        return await ctx.reply(embed=discord.Embed(
            title="❌ Argument manquant",
            description=f"**Syntaxe :** `{PREFIX}removewl @membre`\n\nRetire un membre de la **Whitelist**.",
            color=0xed4245
        ), mention_author=False)
    if member.id not in data["wl"]:
        return await ctx.reply("❌ Pas whitelist.", mention_author=False)
    await db_remove_staff(member.id)
    e = discord.Embed(title="❌ Whitelist retiré", color=0xe67e22, timestamp=discord.utils.utcnow())
    e.description = f"{member.mention} n'a plus accès aux commandes du bot.\nToutes ses permissions ont été révoquées."
    e.set_thumbnail(url=member.display_avatar.url)
    e.set_footer(text=f"Par {ctx.author.display_name}")
    await ctx.reply(embed=e, mention_author=False)


# ===== STAFF LIST =====
@bot.command(name="staff", aliases=["perms", "team"])
async def staff(ctx):
    e = discord.Embed(title="👑 Équipe du bot", color=0xffd700, timestamp=discord.utils.utcnow())
    try:
        buyer = ctx.guild.get_member(BUYER_ID) or await bot.fetch_user(BUYER_ID)
        bv = buyer.mention if hasattr(buyer, "mention") else str(buyer)
    except: bv = f"`{BUYER_ID}`"
    e.add_field(name=f"💎 Buyer", value=f"{bv}", inline=False)
    owners = [(ctx.guild.get_member(u).mention if ctx.guild.get_member(u) else f"`{u}`") for u in data["owners"]]
    e.add_field(name=f"👑 Owners ({len(owners)})", value="\n".join(f"⤷ {o}" for o in owners) or "*Aucun*", inline=False)
    wl = [(ctx.guild.get_member(u).mention if ctx.guild.get_member(u) else f"`{u}`") for u in data["wl"]]
    e.add_field(name=f"⭐ Whitelist ({len(wl)})", value="\n".join(f"⤷ {w}" for w in wl) or "*Aucun*", inline=False)
    e.set_footer(text=f"Ton rang : {get_rank_short(ctx.author.id)} • {len(data['owners']) + len(data['wl']) + 1} membre(s)")
    await ctx.reply(embed=e, mention_author=False)


# ===== TICKETS =====
@bot.command(name="ticketsetup", aliases=["tsetup", "panel"])
async def ticket_setup(ctx, channel: discord.TextChannel = None):
    if not is_owner(ctx.author.id): return
    view = TicketSetupView(ctx.author.id)
    e = discord.Embed(title="🛠️ Setup du panel de tickets", description="*Personnalise le panel avant de l'envoyer.*\n*Utilise le menu déroulant pour modifier chaque élément.*", color=0x5865f2)
    e.add_field(name="📂 Catégories incluses", value="\n".join(f"{v['emoji']} **{v['label']}** — {v['desc']}" for v in TICKET_CATEGORIES.values()), inline=False)
    e.set_footer(text=f"Setup par {ctx.author.display_name} • Timeout : 10 min")
    preview = view.session.build()
    # On envoie le builder
    await ctx.reply(
        content=f"**🛠️ Constructeur de panel tickets** — {ctx.author.mention}\n\n**Aperçu du panel :**",
        embed=preview, view=view, mention_author=False
    )
    try: await ctx.message.delete()
    except: pass

@bot.command(name="close")
async def close_ticket(ctx):
    if not (ctx.channel.topic or "").startswith("ticket-"):
        return await ctx.reply("❌ Pas un salon de ticket.", mention_author=False, delete_after=5)
    topic = ctx.channel.topic or ""
    creator_id = None
    if topic.startswith("ticket-"):
        try: creator_id = int(topic.split("-")[1])
        except: pass
    if ctx.author.id != creator_id and not has_any_perm(ctx.author.id):
        return await ctx.reply("❌ Tu ne peux pas fermer ce ticket.", mention_author=False, delete_after=5)
    await ctx.send(embed=discord.Embed(description=f"🔒 Fermeture par {ctx.author.mention} dans **5s**...", color=0xed4245))
    await asyncio.sleep(5)
    try: await ctx.channel.delete(reason=f"Fermé par {ctx.author}")
    except: pass

@bot.command(name="add")
async def ticket_add(ctx, member: discord.Member = None):
    if not (ctx.channel.topic or "").startswith("ticket-"): return
    if not is_wl(ctx.author.id): return
    if not member:
        return await ctx.reply(f"❌ `{PREFIX}add @membre` — Ajouter quelqu'un au ticket.", mention_author=False)
    await ctx.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
    await ctx.reply(f"✅ {member.mention} ajouté au ticket.", mention_author=False)

@bot.command(name="remove")
async def ticket_remove(ctx, member: discord.Member = None):
    if not (ctx.channel.topic or "").startswith("ticket-"): return
    if not is_wl(ctx.author.id): return
    if not member:
        return await ctx.reply(f"❌ `{PREFIX}remove @membre` — Retirer quelqu'un du ticket.", mention_author=False)
    await ctx.channel.set_permissions(member, overwrite=None)
    await ctx.reply(f"✅ {member.mention} retiré du ticket.", mention_author=False)


# ===== RÔLES =====
@bot.command(name="addrole", aliases=["giverole", "ar"])
async def addrole_cmd(ctx, member: discord.Member = None, *, role: discord.Role = None):
    if not ctx.guild.me.guild_permissions.manage_roles:
        return await ctx.reply("❌ Perm **Gérer les rôles** manquante.", mention_author=False, delete_after=5)
    if not member or not role:
        return await ctx.reply(f"❌ Usage : `{PREFIX}addrole @membre @rôle` (ou IDs)", mention_author=False)
    if member.id == BUYER_ID: return await ctx.reply("❌ Pas touche au **Buyer**.", mention_author=False, delete_after=5)
    if role.is_default(): return await ctx.reply("❌ Impossible avec `@everyone`.", mention_author=False, delete_after=5)
    if role.managed: return await ctx.reply("❌ Rôle géré par une intégration.", mention_author=False, delete_after=5)
    if role >= ctx.guild.me.top_role: return await ctx.reply("❌ Rôle au-dessus du mien.", mention_author=False, delete_after=5)
    if not is_buyer(ctx.author.id) and role >= ctx.author.top_role:
        return await ctx.reply("❌ Rôle au-dessus du tien.", mention_author=False, delete_after=5)
    if role in member.roles: return await ctx.reply(f"❌ A déjà ce rôle.", mention_author=False, delete_after=5)
    try:
        await member.add_roles(role, reason=f"+addrole par {ctx.author}")
        e = discord.Embed(description=f"✅ {role.mention} ajouté à {member.mention}", color=0x2ecc71)
        e.set_footer(text=f"Par {ctx.author}")
        await ctx.reply(embed=e, mention_author=False)
    except Exception as e:
        await ctx.reply(f"❌ Erreur : {e}", mention_author=False, delete_after=5)


@bot.command(name="delrole", aliases=["removerole", "dr"])
async def delrole_cmd(ctx, member: discord.Member = None, *, role: discord.Role = None):
    if not ctx.guild.me.guild_permissions.manage_roles:
        return await ctx.reply("❌ Perm **Gérer les rôles** manquante.", mention_author=False, delete_after=5)
    if not member or not role:
        return await ctx.reply(f"❌ Usage : `{PREFIX}delrole @membre @rôle` (ou IDs)", mention_author=False)
    if member.id == BUYER_ID: return await ctx.reply("❌ Pas touche au **Buyer**.", mention_author=False, delete_after=5)
    if role.is_default(): return await ctx.reply("❌ Impossible avec `@everyone`.", mention_author=False, delete_after=5)
    if role.managed: return await ctx.reply("❌ Rôle géré par une intégration.", mention_author=False, delete_after=5)
    if role >= ctx.guild.me.top_role: return await ctx.reply("❌ Rôle au-dessus du mien.", mention_author=False, delete_after=5)
    if not is_buyer(ctx.author.id) and role >= ctx.author.top_role:
        return await ctx.reply("❌ Rôle au-dessus du tien.", mention_author=False, delete_after=5)
    if role not in member.roles: return await ctx.reply(f"❌ N'a pas ce rôle.", mention_author=False, delete_after=5)
    try:
        await member.remove_roles(role, reason=f"+delrole par {ctx.author}")
        e = discord.Embed(description=f"✅ {role.mention} retiré de {member.mention}", color=0xe67e22)
        e.set_footer(text=f"Par {ctx.author}")
        await ctx.reply(embed=e, mention_author=False)
    except Exception as e:
        await ctx.reply(f"❌ Erreur : {e}", mention_author=False, delete_after=5)


@bot.command(name="derank", aliases=["stripall", "wipe"])
async def derank_cmd(ctx, member: discord.Member = None):
    if not is_owner(ctx.author.id): return
    if not ctx.guild.me.guild_permissions.manage_roles:
        return await ctx.reply("❌ Perm **Gérer les rôles** manquante.", mention_author=False, delete_after=5)
    if not member:
        return await ctx.reply(embed=discord.Embed(
            title="❌ Argument manquant",
            description=f"**Syntaxe :** `{PREFIX}derank @membre`\n\nRetire **tous** les rôles d'un membre d'un coup.\nNe touche pas aux rôles gérés par des intégrations.",
            color=0xed4245
        ), mention_author=False)
    if member.id == BUYER_ID: return await ctx.reply("❌ Pas touche au **Buyer**.", mention_author=False, delete_after=5)
    if member.id == ctx.author.id: return await ctx.reply("❌ Pas toi-même.", mention_author=False, delete_after=5)
    if member.top_role >= ctx.guild.me.top_role:
        return await ctx.reply("❌ Membre au-dessus du bot.", mention_author=False, delete_after=5)
    if not is_buyer(ctx.author.id) and member.top_role >= ctx.author.top_role:
        return await ctx.reply("❌ Membre au-dessus ou égal à toi.", mention_author=False, delete_after=5)
    bot_top = ctx.guild.me.top_role
    removable = [r for r in member.roles if not r.is_default() and not r.managed and r < bot_top]
    if not removable:
        return await ctx.reply(f"❌ Aucun rôle retirable.", mention_author=False, delete_after=5)
    msg = await ctx.reply(f"⏳ Retrait de **{len(removable)}** rôle(s)...", mention_author=False)
    try:
        await member.remove_roles(*removable, reason=f"+derank par {ctx.author}")
        rl = ", ".join(r.mention for r in removable[:15])
        if len(removable) > 15: rl += f" *(+{len(removable)-15} autres)*"
        e = discord.Embed(title="💥 Dérank", description=f"**{len(removable)}** rôle(s) retiré(s) de {member.mention}\n\n{rl}", color=0xed4245)
        e.set_footer(text=f"Par {ctx.author}")
        e.timestamp = discord.utils.utcnow()
        await msg.edit(content=None, embed=e)
    except Exception as e:
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
            f"Donne les permissions admin (whitelist, tickets, dérank).\n"
            f"Si le membre était en whitelist, il passe automatiquement Owner.\n"
            f"*Ex : `{PREFIX}setowner @Michel`*\n\n"
            f"💎 **`{PREFIX}removeowner @membre`** — Retirer Owner\n"
            f">>> Rang requis : **Buyer**\n"
            f"Révoque toutes les permissions Owner.\n"
            f"Le membre perd l'accès au bot entièrement.\n"
            f"*Ex : `{PREFIX}removeowner @Michel`*\n\n"
            f"👑 **`{PREFIX}setwl @membre`** — Ajouter à la whitelist\n"
            f">>> Rang requis : **Owner+**\n"
            f"Donne accès à toutes les commandes de base (embed, emojis, rôles, tickets).\n"
            f"*Ex : `{PREFIX}setwl @NouveauMembre`*\n\n"
            f"👑 **`{PREFIX}removewl @membre`** — Retirer de la whitelist\n"
            f">>> Rang requis : **Owner+**\n"
            f"Le membre perd l'accès au bot.\n"
            f"*Ex : `{PREFIX}removewl @Membre`*"
        )
    },
    "tickets": {
        "emoji": "🎫", "title": "Système de Tickets", "color": 0x5865f2,
        "content": (
            f"*Système de support avec catégories.*\n\n"
            f"👑 **`{PREFIX}ticketsetup`** — Créer un panel de tickets\n"
            f">>> Rang requis : **Owner+**\n"
            f"Ouvre un **constructeur interactif** pour personnaliser le panel (titre, description, couleur, image...).\n"
            f"Le panel envoyé contient un **menu déroulant** avec 5 catégories :\n"
            f"🆘 Support général • 🐛 Bug • 🤝 Partenariat • 💡 Suggestion • 📩 Autre\n\n"
            f"⭐ **`{PREFIX}close`** — Fermer un ticket\n"
            f">>> Rang requis : **Whitelist+** *(ou créateur du ticket)*\n"
            f"Supprime le salon après 5 secondes.\n"
            f"Un bouton 🔒 est aussi disponible dans chaque ticket.\n\n"
            f"⭐ **`{PREFIX}add @membre`** — Ajouter au ticket\n"
            f">>> Rang requis : **Whitelist+**\n"
            f"Donne accès au ticket à un membre extérieur.\n\n"
            f"⭐ **`{PREFIX}remove @membre`** — Retirer du ticket\n"
            f">>> Rang requis : **Whitelist+**\n"
            f"Retire l'accès au ticket.\n\n"
            f"**Dans chaque ticket :**\n"
            f"🔒 **Fermer** — Ferme et supprime le ticket\n"
            f"✋ **Prendre en charge** — Un staff claim le ticket"
        )
    },
    "roles": {
        "emoji": "🎭", "title": "Gestion des Rôles", "color": 0xe67e22,
        "content": (
            f"*Ajouter, retirer ou purger les rôles.*\n\n"
            f"⭐ **`{PREFIX}addrole @membre @rôle`** — Donner un rôle\n"
            f">>> Rang requis : **Whitelist+**\n"
            f"Ajoute le rôle au membre. Le rôle doit être en-dessous du tien et du bot.\n"
            f"Le **Buyer** peut ignorer la restriction de hiérarchie.\n"
            f"Alias : `{PREFIX}giverole`, `{PREFIX}ar`\n"
            f"*Ex : `{PREFIX}addrole @Joueur @VIP`*\n\n"
            f"⭐ **`{PREFIX}delrole @membre @rôle`** — Retirer un rôle\n"
            f">>> Rang requis : **Whitelist+**\n"
            f"Retire le rôle du membre. Mêmes restrictions de hiérarchie.\n"
            f"Alias : `{PREFIX}removerole`, `{PREFIX}dr`\n"
            f"*Ex : `{PREFIX}delrole @Joueur @VIP`*\n\n"
            f"👑 **`{PREFIX}derank @membre`** — Retirer TOUS les rôles\n"
            f">>> Rang requis : **Owner+**\n"
            f"Retire d'un coup tous les rôles du membre (sauf rôles d'intégration et ceux au-dessus du bot).\n"
            f"⚠️ **Action radicale** — impossible sur le Buyer ou soi-même.\n"
            f"Alias : `{PREFIX}stripall`, `{PREFIX}wipe`\n"
            f"*Ex : `{PREFIX}derank @Tricheur`*"
        )
    },
    "utils": {
        "emoji": "🛠️", "title": "Utilitaires", "color": 0x3498db,
        "content": (
            f"*Outils disponibles pour tous les membres staff.*\n\n"
            f"⭐ **`{PREFIX}embed`** — Constructeur d'embed\n"
            f">>> Rang requis : **Whitelist+**\n"
            f"Ouvre un éditeur interactif pour créer un embed personnalisé.\n"
            f"Tu peux modifier : titre, description, couleur, auteur, footer, image, thumbnail, URL, timestamp.\n"
            f"Une fois terminé, choisis le salon de destination.\n"
            f"⏱️ Timeout : 15 min (3 min par modification).\n\n"
            f"⭐ **`{PREFIX}create <emojis>`** — Cloner des emojis\n"
            f">>> Rang requis : **Whitelist+** + perm Discord *Gérer les emojis*\n"
            f"Copie un ou plusieurs emojis d'autres serveurs vers le tien.\n"
            f"Tu peux renommer l'emoji en ajoutant le nom après (si un seul emoji).\n"
            f"Alias : `{PREFIX}steal`, `{PREFIX}addemoji`\n"
            f"*Ex : `{PREFIX}create :pepe: nouveauNom`*\n"
            f"*Ex : `{PREFIX}create :emoji1: :emoji2: :emoji3:`*\n\n"
            f"⭐ **`{PREFIX}staff`** — Voir l'équipe\n"
            f">>> Rang requis : **Whitelist+**\n"
            f"Affiche le Buyer, les Owners et la Whitelist.\n"
            f"Alias : `{PREFIX}perms`, `{PREFIX}team`"
        )
    },
    "hierarchy": {
        "emoji": "📊", "title": "Hiérarchie des Rangs", "color": 0x9b59b6,
        "content": (
            f"*Structure des permissions du bot.*\n\n"
            f"**💎 Buyer** — Contrôle total\n"
            f"C'est le propriétaire du bot (un seul, défini par `BUYER_ID`).\n"
            f"Il peut tout faire et ne peut pas être ciblé par les commandes.\n\n"
            f"**👑 Owner** — Administration\n"
            f"Promu par le Buyer via `{PREFIX}setowner`.\n"
            f"Commandes exclusives : `setwl`, `removewl`, `ticketsetup`, `derank`.\n"
            f"+ toutes les commandes Whitelist.\n\n"
            f"**⭐ Whitelist** — Accès au bot\n"
            f"Ajouté par un Owner via `{PREFIX}setwl`.\n"
            f"Commandes : `embed`, `create`, `addrole`, `delrole`, `add`, `remove`, `staff`, `help`.\n\n"
            f"**🔒 Aucun rang** — Pas d'accès\n"
            f"Sans rang, aucune commande ne fonctionne.\n"
            f"Le bot ignore complètement les messages.\n\n"
            f"**Comment monter en rang ?**\n"
            f"🔒 → ⭐ : un **Owner** t'ajoute en whitelist\n"
            f"⭐ → 👑 : le **Buyer** te promeut Owner"
        )
    },
}


class AdminSelect(ui.Select):
    def __init__(self):
        opts = [
            SelectOption(label=d["title"][:25], value=k, emoji=d["emoji"],
                         description={"perms": "Owner, WL, promotions", "tickets": "Setup, close, add, remove", "roles": "Addrole, delrole, derank", "utils": "Embed, emoji, staff", "hierarchy": "Buyer > Owner > WL"}.get(k, "")[:50])
            for k, d in ADMIN_PAGES.items()
        ]
        super().__init__(placeholder="📂 Choisis une catégorie...", options=opts)

    async def callback(self, interaction):
        page = ADMIN_PAGES[self.values[0]]
        embed = discord.Embed(title=f"{page['emoji']}  {page['title']}", description=page["content"], color=page.get("color", 0x5865f2), timestamp=discord.utils.utcnow())
        embed.set_footer(text=f"Panneau Admin • {interaction.user.display_name}")
        await interaction.response.edit_message(embed=embed)


@bot.command(name="admin")
async def admin_cmd(ctx):
    rank = get_rank(ctx.author.id)
    e = discord.Embed(title="⚙️ Panneau d'Administration", color=0x5865f2, timestamp=discord.utils.utcnow())
    e.description = f"*Toutes les commandes admin expliquées en détail.*\n\n**Ton rang :** {rank}"
    e.add_field(name="💎 Buyer", value=f"`setowner` `removeowner`\n+ tout le reste", inline=True)
    e.add_field(name="👑 Owner+", value=f"`setwl` `removewl`\n`ticketsetup` `derank`", inline=True)
    e.add_field(name="⭐ Whitelist+", value=f"`embed` `create` `addrole`\n`delrole` `staff` `help`", inline=True)
    e.add_field(name="\u200b", value="*Sélectionne une catégorie ci-dessous pour les explications détaillées.*", inline=False)
    e.set_footer(text=f"Rang : {get_rank_short(ctx.author.id)}")
    view = ui.View(timeout=180); view.add_item(AdminSelect())
    await ctx.reply(embed=e, view=view, mention_author=False)


# ============================================================
# HELP
# ============================================================
@bot.command(name="help", aliases=["h", "commands"])
async def help_cmd(ctx):
    rank = get_rank(ctx.author.id)
    e = discord.Embed(title="📖 Liste des commandes", color=0x5865f2, timestamp=discord.utils.utcnow())
    e.description = f"Préfixe : `{PREFIX}` • Ton rang : **{rank}**\n\n💡 *Utilise `{PREFIX}admin` pour le détail de chaque commande.*"
    e.add_field(name="🛠️ Utilitaires",
                value=f"`{PREFIX}embed` — Constructeur d'embed\n`{PREFIX}create <:e:id>` — Cloner emoji(s)\n`{PREFIX}staff` — Voir l'équipe\n`{PREFIX}help` — Cette aide\n`{PREFIX}admin` — Panneau admin détaillé", inline=False)
    e.add_field(name="🎫 Tickets",
                value=f"`{PREFIX}ticketsetup` — Créer un panel *(Owner+)*\n`{PREFIX}close` — Fermer un ticket\n`{PREFIX}add @m` — Ajouter *(WL+)*\n`{PREFIX}remove @m` — Retirer *(WL+)*", inline=False)
    e.add_field(name="🎭 Rôles",
                value=f"`{PREFIX}addrole @m @r` — Donner un rôle\n`{PREFIX}delrole @m @r` — Retirer un rôle\n`{PREFIX}derank @m` — Tout retirer *(Owner+)*", inline=False)
    if is_owner(ctx.author.id):
        e.add_field(name="👑 Owner", value=f"`{PREFIX}setwl @m` — Ajouter whitelist\n`{PREFIX}removewl @m` — Retirer whitelist", inline=False)
    if is_buyer(ctx.author.id):
        e.add_field(name="💎 Buyer", value=f"`{PREFIX}setowner @m` — Promouvoir Owner\n`{PREFIX}removeowner @m` — Retirer Owner", inline=False)
    e.set_footer(text="Bot Team 17\"")
    await ctx.reply(embed=e, mention_author=False)


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
        return  # Pas de perm → ignoré silencieusement
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply(embed=discord.Embed(
            description=f"❌ Argument manquant : `{error.param.name}`\n\nTape `{PREFIX}help` ou `{PREFIX}admin` pour l'aide.",
            color=0xed4245
        ), mention_author=False, delete_after=10)
        return
    if isinstance(error, commands.MemberNotFound):
        await ctx.reply("❌ Membre introuvable. Mentionne-le ou utilise son ID.", mention_author=False, delete_after=5)
        return
    if isinstance(error, commands.RoleNotFound):
        await ctx.reply("❌ Rôle introuvable. Mentionne-le ou utilise son ID.", mention_author=False, delete_after=5)
        return
    if isinstance(error, commands.BadArgument):
        await ctx.reply(f"❌ Argument invalide. Vérifie la syntaxe avec `{PREFIX}help`.", mention_author=False, delete_after=5)
        return
    print(f"[Erreur] {type(error).__name__}: {error}")

@bot.event
async def on_ready():
    print(f"✅ Connecté : {bot.user} | Buyer : {BUYER_ID}")
    print(f"📊 {len(bot.guilds)} serveur(s) | {len(data['owners'])} owners | {len(data['wl'])} wl")
    await bot.change_presence(activity=discord.Game(name=f"{PREFIX}help"))


if __name__ == "__main__":
    bot.run(TOKEN)
