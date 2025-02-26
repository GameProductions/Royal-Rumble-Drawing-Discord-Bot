from flask import Flask, render_template, request, redirect, url_for
import psycopg2  # Import psycopg2 for PostgreSQL
import asyncio
import os
from dotenv import load_dotenv
import discord
from discord.ext import commands, tasks
import datetime

# Load environment variables from .env file
load_dotenv()

# Access environment variables
DB_HOST = os.getenv('DB_HOST')
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_NAME = os.getenv('DB_NAME')
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
PUBLIC_KEY = os.getenv('PUBLIC_KEY') 

# --- Flask App Setup ---
app = Flask(__name__)

# Database setup (using PostgreSQL)
mydb = psycopg2.connect(
    host=os.getenv('DB_HOST'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME')
)
cursor = mydb.cursor()

# Create the tables if they don't exist
cursor.execute('''
    CREATE TABLE IF NOT EXISTS drawings (
        drawing_id SERIAL PRIMARY KEY,
        name VARCHAR(255) UNIQUE,
        status VARCHAR(255) DEFAULT 'closed',
        is_archived BOOLEAN DEFAULT FALSE,
        time_limit_hours INT
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS entries (
        entry_id SERIAL PRIMARY KEY,
        entrant_number INT,
        entrant_name VARCHAR(255),
        drawing_id INT,
        eliminated_by VARCHAR(255),
        status VARCHAR(255) DEFAULT 'pending',
        FOREIGN KEY (drawing_id) REFERENCES drawings (drawing_id)
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS entry_users (
        entry_id INT,
        user_id BIGINT,
        FOREIGN KEY (entry_id) REFERENCES entries (entry_id),
        PRIMARY KEY (entry_id, user_id)
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS archived_drawings (
        drawing_id SERIAL PRIMARY KEY,
        name VARCHAR(255),
        status VARCHAR(255)
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS archived_entries (
        entry_id SERIAL PRIMARY KEY,
        entrant_number INT,
        entrant_name VARCHAR(255),
        drawing_id INT,
        eliminated_by VARCHAR(255),
        status VARCHAR(255)
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS archived_entry_users (
        entry_id INT,
        user_id BIGINT,
        PRIMARY KEY (entry_id, user_id)
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS results (
        result_id SERIAL PRIMARY KEY,
        drawing_id INT,
        winner_id INT,
        FOREIGN KEY (drawing_id) REFERENCES drawings (drawing_id)
    )
''')
mydb.commit()

# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.members = True  # Enable member intents
intents.message_content = True  # Enable message content intents

bot = commands.Bot(command_prefix='!', intents=intents)

# Admin role ID (initially None)
admin_role_id = None

# --- Helper Functions ---

def get_drawing_id(drawing_name, include_archived=False):
    """Helper function to get drawing_id from either drawings or archived_drawings table."""
    cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
    drawing_id = cursor.fetchone()
    if not drawing_id and include_archived:
        cursor.execute("SELECT drawing_id FROM archived_drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
    return drawing_id if drawing_id else None

async def send_message_to_users(drawing_name, entry_id, message):
    """Helper function to send a message to all users in an entry."""
    cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = %s", (entry_id,))
    user_ids = [row for row in cursor.fetchall()]
    for user_id in user_ids:
        user = bot.get_user(user_id)
        if user:
            await user.send(message, ephemeral=True)

def verify_signature(public_key, timestamp, body, signature):
    """Verifies the signature of an interaction request."""
    try:
        # Convert the public key to bytes
        public_key_bytes = bytes.fromhex(public_key)

        # Create a VerifyKey object
        verify_key = nacl.signing.VerifyKey(public_key_bytes)

        # Combine the timestamp and request body
        message = (timestamp + body).encode()

        # Verify the signature
        verify_key.verify(message, bytes.fromhex(signature))
        return True

    except nacl.exceptions.BadSignatureError:
        return False

async def convert_users(ctx, users):
    return [await commands.MemberConverter().convert(ctx, user.strip()) for user in users.split(",") if user.strip()]

# --- Bot Commands ---

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    await bot.tree.sync()
    check_drawings.start()

# --- Admin Role ---

@bot.tree.command(name="set_admin_role", description="Sets the specified role as the admin role for the bot.")
@app_commands.describe(role="The role to set as the admin role.")
@commands.has_permissions(administrator=True)
async def set_admin_role_slash(interaction: discord.Interaction, role: discord.Role):
    """Sets the specified role as the admin role for the bot (slash command)."""
    try:
        global admin_role_id
        admin_role_id = role.id
        await interaction.response.send_message(f"Role '{role.name}' has been set as the admin role.")
    except Exception as e:
        await interaction.response.send_message(f"Error setting admin role: {e}")

@bot.command(name="set_admin_role")
@commands.has_permissions(administrator=True)
async def set_admin_role_text(ctx, role: discord.Role):
    """Sets the specified role as the admin role for the bot (text command)."""
    try:
        global admin_role_id
        admin_role_id = role.id
        await ctx.send(f"Role '{role.name}' has been set as the admin role.")
    except Exception as e:
        await ctx.send(f"Error setting admin role: {e}")

# --- Custom Decorator for Admin Check ---

def has_admin_permissions():
    async def predicate(ctx):
        if admin_role_id is None:
            return ctx.author.guild_permissions.administrator
        return ctx.author.get_role(admin_role_id) is not None
    return commands.check(predicate)

# --- Create Drawing ---

@bot.tree.command(name="create_drawing", description="Creates a new drawing.")
@app_commands.describe(name="The name of the drawing.")
async def create_drawing_slash(interaction: discord.Interaction, name: str):
    """Creates a new drawing (slash command)."""
    try:
        cursor.execute("INSERT INTO drawings (name) VALUES (%s)", (name,))
        mydb.commit()
        await interaction.response.send_message(f"Drawing '{name}' created!")
    except psycopg2.errors.UniqueViolation:
        await interaction.response.send_message(f"A drawing with the name '{name}' already exists.")
    except Exception as e:
        await interaction.response.send_message(f"Error creating drawing: {e}")

@bot.command(name="create_drawing")
async def create_drawing_text(ctx, name: str):
    """Creates a new drawing (text command)."""
    try:
        cursor.execute("INSERT INTO drawings (name) VALUES (%s)", (name,))
        mydb.commit()
        await ctx.send(f"Drawing '{name}' created!")
    except psycopg2.errors.UniqueViolation:
        await ctx.send(f"A drawing with the name '{name}' already exists.")
    except Exception as e:
        await ctx.send(f"Error creating drawing: {e}")

# --- Create Test Drawing ---

@bot.tree.command(name="create_test_drawing", description="Creates a test drawing that does not save results.")
@app_commands.describe(name="The name of the test drawing.")
@has_admin_permissions()
async def create_test_drawing_slash(interaction: discord.Interaction, name: str):
    """Creates a test drawing that does not save results (slash command)."""
    try:
        cursor.execute("INSERT INTO drawings (name, status) VALUES (%s, 'open')", (f"test_{name}",))
        mydb.commit()
        await interaction.response.send_message(f"Test drawing '{name}' created!")
    except psycopg2.errors.UniqueViolation:
        await interaction.response.send_message(f"A drawing with the name '{name}' already exists.")
    except Exception as e:
        await interaction.response.send_message(f"Error creating test drawing: {e}")

@bot.command(name="create_test_drawing")
@has_admin_permissions()
async def create_test_drawing_text(ctx, name: str):
    """Creates a test drawing that does not save results (text command)."""
    try:
        cursor.execute("INSERT INTO drawings (name, status) VALUES (%s, 'open')", (f"test_{name}",))
        mydb.commit()
        await ctx.send(f"Test drawing '{name}' created!")
    except psycopg2.errors.UniqueViolation:
        await ctx.send(f"A drawing with the name '{name}' already exists.")
    except Exception as e:
        await ctx.send(f"Error creating test drawing: {e}")

# --- Join Drawing ---

@bot.tree.command(name="join_drawing", description="Joins a drawing.")
@app_commands.describe(name="The name of the drawing to join.")
async def join_drawing_slash(interaction: discord.Interaction, name: str):
    """Joins a drawing (slash command)."""
    cursor.execute("SELECT drawing_id, status FROM drawings WHERE name = %s", (name,))
    result = cursor.fetchone()
    if result is None:
        await interaction.response.send_message(f"Drawing '{name}' not found.")
        return

    drawing_id, status = result
    if status == 'closed':
        await interaction.response.send_message(f"Drawing '{name}' is currently closed.")
        return

    try:
        # Get all available entrant numbers
        cursor.execute("SELECT entrant_number FROM entries WHERE drawing_id = %s", (drawing_id,))
        taken_numbers = [row[0] for row in cursor.fetchall()]  # Correctly fetch entrant_number
        all_numbers = set(range(1, 31))  # Adjust the range if necessary
        available_numbers = all_numbers - set(taken_numbers)

        if not available_numbers:
            await interaction.response.send_message(f"Drawing '{name}' is full.")
            return

        entrant_number = random.choice(list(available_numbers))

        cursor.execute("INSERT INTO entries (user_id, drawing_id, entrant_number) VALUES (%s, %s, %s)", (interaction.user.id, drawing_id, entrant_number))
        mydb.commit()

        await interaction.response.send_message(f"You have joined the drawing '{name}' with entrant number {entrant_number}.", ephemeral=True)

    except psycopg2.errors.UniqueViolation:
        await interaction.response.send_message(f"{interaction.user.mention}, you've already joined this drawing!")
    except Exception as e:
        await interaction.response.send_message(f"Error joining drawing: {e}")

@bot.command(name="join_drawing")
async def join_drawing_text(ctx, name: str):
    """Joins a drawing (text command)."""
    cursor.execute("SELECT drawing_id, status FROM drawings WHERE name = %s", (name,))
    result = cursor.fetchone()
    if result is None:
        await ctx.send(f"Drawing '{name}' not found.")
        return

    drawing_id, status = result
    if status == 'closed':
        await ctx.send(f"Drawing '{name}' is currently closed.")
        return

    try:
        # Get all available entrant numbers
        cursor.execute("SELECT entrant_number FROM entries WHERE drawing_id = %s", (drawing_id,))
        taken_numbers = [row[0] for row in cursor.fetchall()]  # Correctly fetch entrant_number
        all_numbers = set(range(1, 31))  # Adjust the range if necessary
        available_numbers = all_numbers - set(taken_numbers)

        if not available_numbers:
            await ctx.send(f"Drawing '{name}' is full.")
            return

        entrant_number = random.choice(list(available_numbers))

        cursor.execute("INSERT INTO entries (user_id, drawing_id, entrant_number) VALUES (%s, %s, %s)", (ctx.author.id, drawing_id, entrant_number))
        mydb.commit()

        await ctx.author.send(f"You have joined the drawing '{name}' with entrant number {entrant_number}.", ephemeral=True)

    except psycopg2.errors.UniqueViolation:
        await ctx.send(f"{ctx.author.mention}, you've already joined this drawing!")
    except Exception as e:
        await ctx.send(f"Error joining drawing: {e}")

# --- My Entries ---

@bot.tree.command(name="my_entries", description="Displays your drawing entries.")
async def my_entries_slash(interaction: discord.Interaction):
    """Displays the user's entries (slash command)."""
    cursor.execute("SELECT e.entrant_number, e.entrant_name, e.status, e.eliminated_by, d.name "
                   "FROM entries e "
                   "JOIN drawings d ON e.drawing_id = d.drawing_id "
                   "WHERE e.user_id = %s", (interaction.user.id,))
    entries = cursor.fetchall()
    if entries:
        message = "Your drawing entries:\n"
        for entry in entries:
            message += f"- {entry}: Entrant number {entry}"
            if entry:
                message += f", Name: {entry}"
            message += f", Status: {entry}"
            if entry == 'eliminated' and entry:
                message += f", Eliminated by: {entry}"
            message += "\n"
        await interaction.response.send_message(message, ephemeral=True)
    else:
        await interaction.response.send_message("You haven't joined any drawings yet.", ephemeral=True)

@bot.command(name="my_entries")
async def my_entries_text(ctx):
    """Displays the user's entries (text command)."""
    cursor.execute("SELECT e.entrant_number, e.entrant_name, e.status, e.eliminated_by, d.name "
                   "FROM entries e "
                   "JOIN drawings d ON e.drawing_id = d.drawing_id "
                   "WHERE e.user_id = %s", (ctx.author.id,))
    entries = cursor.fetchall()
    if entries:
        message = "Your drawing entries:\n"
        for entry in entries:
            message += f"- {entry}: Entrant number {entry}"
            if entry:
                message += f", Name: {entry}"
            message += f", Status: {entry}"
            if entry == 'eliminated' and entry:
                message += f", Eliminated by: {entry}"
            message += "\n"
        await ctx.send(message, ephemeral=True)
    else:
        await ctx.send("You haven't joined any drawings yet.", ephemeral=True)

# --- Drawing Entries ---

@bot.tree.command(name="drawing_entries", description="Displays the entries for a specific drawing in a table format.")
@app_commands.describe(name="The name of the drawing.")
@app_commands.describe(include_archived="Whether to include archived entries ('yes' or 'no').")
@commands.has_permissions(administrator=True)
async def drawing_entries_slash(interaction: discord.Interaction, name: str, include_archived: str = "no"):
    """Displays the entries for a specific drawing in a table format (slash command)."""
    try:
        if include_archived.lower() == "yes":
            cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (name,))
            drawing_id = cursor.fetchone()
            if not drawing_id:
                cursor.execute("SELECT drawing_id FROM archived_drawings WHERE name = %s", (name,))
                drawing_id = cursor.fetchone()
                if not drawing_id:
                    await interaction.response.send_message(f"Drawing '{name}' not found.")
                    return
        else:
            cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (name,))
            drawing_id = cursor.fetchone()
            if not drawing_id:
                await interaction.response.send_message(f"Drawing '{name}' not found.")
                return

        cursor.execute("SELECT entrant_number, entrant_name FROM entries WHERE drawing_id = %s", (drawing_id,))
        entries = cursor.fetchall()

        if not entries:
            await interaction.response.send_message(f"No entries found for drawing '{name}'.")

        table_data = []  # Initialize table_data as an empty list
        for entrant_number, entrant_name in entries:
            cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = (SELECT entry_id FROM entries WHERE entrant_number = %s AND drawing_id = %s)", (entrant_number, drawing_id))
            user_ids = [row for row in cursor.fetchall()]
            user_mentions = [interaction.guild.get_member(user_id).mention for user_id in user_ids if interaction.guild.get_member(user_id)]

            cursor.execute("SELECT winner_id FROM results WHERE drawing_id = %s", (drawing_id,))
            winner_entry_id = cursor.fetchone()
            if winner_entry_id and winner_entry_id == entrant_number:
                table_data.append([f"**{entrant_number}**", f"**{entrant_name or ''}** 🏆", f"**{', '.join(user_mentions) or 'No users found'}**"])
            else:
                table_data.append([entrant_number, entrant_name or "", ", ".join(user_mentions) or "No users found"])

        table = tabulate(table_data, headers=["Entrant Number", "Entrant Name", "Users"], tablefmt="simple")
        await interaction.response.send_message(f"**Entries for drawing '{name}'**:\n```\n{table}\n```")

    except Exception as e:
        await interaction.response.send_message(f"Error displaying drawing entries: {e}")

@bot.command(name="drawing_entries")
@commands.has_permissions(administrator=True)
async def drawing_entries_text(ctx, name: str, include_archived: str = "no"):
    """Displays the entries for a specific drawing in a table format (text command)."""
    try:
        if include_archived.lower() == "yes":
            cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (name,))
            drawing_id = cursor.fetchone()
            if not drawing_id:
                cursor.execute("SELECT drawing_id FROM archived_drawings WHERE name = %s", (name,))
                drawing_id = cursor.fetchone()
                if not drawing_id:
                    await ctx.send(f"Drawing '{name}' not found.")
                    return
        else:
            cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (name,))
            drawing_id = cursor.fetchone()
            if not drawing_id:
                await ctx.send(f"Drawing '{name}' not found.")
                return

        cursor.execute("SELECT entrant_number, entrant_name FROM entries WHERE drawing_id = %s", (drawing_id,))
        entries = cursor.fetchall()

        if not entries:
            await ctx.send(f"No entries found for drawing '{name}'.")

        table_data = []  # Initialize table_data as an empty list
        for entrant_number, entrant_name in entries:
            cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = (SELECT entry_id FROM entries WHERE entrant_number = %s AND drawing_id = %s)", (entrant_number, drawing_id))
            user_ids = [row for row in cursor.fetchall()]
            user_mentions = [ctx.guild.get_member(user_id).mention for user_id in user_ids if ctx.guild.get_member(user_id)]

            cursor.execute("SELECT winner_id FROM results WHERE drawing_id = %s", (drawing_id,))
            winner_entry_id = cursor.fetchone()
            if winner_entry_id and winner_entry_id == entrant_number:
                table_data.append([f"**{entrant_number}**", f"**{entrant_name or ''}** 🏆", f"**{', '.join(user_mentions) or 'No users found'}**"])
            else:
                table_data.append([entrant_number, entrant_name or "", ", ".join(user_mentions) or "No users found"])

        table = tabulate(table_data, headers=["Entrant Number", "Entrant Name", "Users"], tablefmt="simple")
        await ctx.send(f"**Entries for drawing '{name}'**:\n```\n{table}\n```")

    except Exception as e:
        await ctx.send(f"Error displaying drawing entries: {e}")

# --- Start Drawing ---

@bot.tree.command(name="start_drawing", description="Starts a drawing.")
@app_commands.describe(name="The name of the drawing to start.")
@commands.has_permissions(administrator=True)
async def start_drawing_slash(interaction: discord.Interaction, name: str):
    """Starts a drawing (slash command)."""
    try:
        cursor.execute("UPDATE drawings SET status = 'open', start_time = %s WHERE name = %s", (datetime.datetime.now(), name))
        mydb.commit()
        if cursor.rowcount > 0:
            await interaction.response.send_message(f"Drawing '{name}' started!")
        else:
            await interaction.response.send_message(f"Drawing '{name}' not found.")
    except Exception as e:
        await interaction.response.send_message(f"Error starting drawing: {e}")

@bot.command(name="start_drawing")
@commands.has_permissions(administrator=True)
async def start_drawing_text(ctx, name: str):
    """Starts a drawing (text command)."""
    try:
        cursor.execute("UPDATE drawings SET status = 'open', start_time = %s WHERE name = %s", (datetime.datetime.now(), name))
        mydb.commit()
        if cursor.rowcount > 0:
            await ctx.send(f"Drawing '{name}' started!")
        else:
            await ctx.send(f"Drawing '{name}' not found.")
    except Exception as e:
        await ctx.send(f"Error starting drawing: {e}")

# --- Set Drawing Time Limit ---

@bot.tree.command(name="set_drawing_time_limit", description="Sets the time limit for a drawing in hours.")
@app_commands.describe(drawing_name="The name of the drawing.")
@app_commands.describe(time_limit_hours="The time limit in hours.")
@has_admin_permissions()
async def set_drawing_time_limit_slash(interaction: discord.Interaction, drawing_name: str, time_limit_hours: int):
    """Sets the time limit for a drawing in hours (slash command)."""
    try:
        # Check if the drawing exists
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if not drawing_id:
            await interaction.response.send_message(f"Drawing '{drawing_name}' not found.")
            return

        # Add a new column to store the time limit (if it doesn't exist)
        cursor.execute("ALTER TABLE drawings ADD COLUMN IF NOT EXISTS time_limit_hours INT")
        mydb.commit()

        # Update the time limit for the drawing
        cursor.execute("UPDATE drawings SET time_limit_hours = %s WHERE drawing_id = %s", (time_limit_hours, drawing_id))
        mydb.commit()

        await interaction.response.send_message(f"Time limit for drawing '{drawing_name}' set to {time_limit_hours} hours.")

    except Exception as e:
        await interaction.response.send_message(f"Error setting time limit: {e}")

@bot.command(name="set_drawing_time_limit")
@has_admin_permissions()
async def set_drawing_time_limit_text(ctx, drawing_name: str, time_limit_hours: int):
    """Sets the time limit for a drawing in hours (text command)."""
    try:
        # Check if the drawing exists
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if not drawing_id:
            await ctx.send(f"Drawing '{drawing_name}' not found.")
            return

        # Add a new column to store the time limit (if it doesn't exist)
        cursor.execute("ALTER TABLE drawings ADD COLUMN IF NOT EXISTS time_limit_hours INT")
        mydb.commit()

        # Update the time limit for the drawing
        cursor.execute("UPDATE drawings SET time_limit_hours = %s WHERE drawing_id = %s", (time_limit_hours, drawing_id))
        mydb.commit()

        await ctx.send(f"Time limit for drawing '{drawing_name}' set to {time_limit_hours} hours.")

    except Exception as e:
        await ctx.send(f"Error setting time limit: {e}")

# --- Stop Drawing ---

@bot.tree.command(name="stop_drawing", description="Stops a drawing.")
@app_commands.describe(name="The name of the drawing to stop.")
@commands.has_permissions(administrator=True)
async def stop_drawing_slash(interaction: discord.Interaction, name: str):
    """Stops a drawing (slash command)."""
    try:
        cursor.execute("UPDATE drawings SET status = 'closed', start_time = NULL WHERE name = %s", (name,))
        mydb.commit()
        if cursor.rowcount > 0:
            await interaction.response.send_message(f"Drawing '{name}' stopped!")
        else:
            await interaction.response.send_message(f"Drawing '{name}' not found.")
    except Exception as e:
        await interaction.response.send_message(f"Error stopping drawing: {e}")

@bot.command(name="stop_drawing")
@commands.has_permissions(administrator=True)
async def stop_drawing_text(ctx, name: str):
    """Stops a drawing (text command)."""
    try:
        cursor.execute("UPDATE drawings SET status = 'closed', start_time = NULL WHERE name = %s", (name,))
        mydb.commit()
        if cursor.rowcount > 0:
            await ctx.send(f"Drawing '{name}' stopped!")
        else:
            await ctx.send(f"Drawing '{name}' not found.")
    except Exception as e:
        await ctx.send(f"Error stopping drawing: {e}")

# --- Drawing Status ---

@bot.tree.command(name="drawing_status", description="Displays the status of a drawing.")
@app_commands.describe(name="The name of the drawing.")
@app_commands.describe(include_archived="Whether to include archived drawings ('yes' or 'no').")
async def drawing_status_slash(interaction: discord.Interaction, name: str, include_archived: str = "no"):
    """Displays the status of a drawing (slash command)."""
    try:
        if include_archived.lower() == "yes":
            cursor.execute("SELECT status FROM drawings WHERE name = %s", (name,))
            status = cursor.fetchone()
            if not status:
                cursor.execute("SELECT status FROM archived_drawings WHERE name = %s", (name,))
                status = cursor.fetchone()
                if not status:
                    await interaction.response.send_message(f"Drawing '{name}' not found.")
                    return
        else:
            cursor.execute("SELECT status FROM drawings WHERE name = %s", (name,))
            status = cursor.fetchone()
            if not status:
                await interaction.response.send_message(f"Drawing '{name}' not found.")
                return

        await interaction.response.send_message(f"Drawing '{name}' is currently {status}.")

    except Exception as e:
        await interaction.response.send_message(f"Error getting drawing status: {e}")

@bot.command(name="drawing_status")
async def drawing_status_text(ctx, name: str, include_archived: str = "no"):
    """Displays the status of a drawing (text command)."""
    try:
        if include_archived.lower() == "yes":
            cursor.execute("SELECT status FROM drawings WHERE name = %s", (name,))
            status = cursor.fetchone()
            if not status:
                cursor.execute("SELECT status FROM archived_drawings WHERE name = %s", (name,))
                status = cursor.fetchone()
                if not status:
                    await ctx.send(f"Drawing '{name}' not found.")
                    return
        else:
            cursor.execute("SELECT status FROM drawings WHERE name = %s", (name,))
            status = cursor.fetchone()
            if not status:
                await ctx.send(f"Drawing '{name}' not found.")
                return

        await ctx.send(f"Drawing '{name}' is currently {status}.")

    except Exception as e:
        await ctx.send(f"Error getting drawing status: {e}")

# --- Change Entrant ---

@bot.tree.command(name="change_entrant", description="Changes the entrant number for a drawing entry.")
@app_commands.describe(drawing_name="The name of the drawing.")
@app_commands.describe(old_entrant_number="The current entrant number to change.")
@app_commands.describe(new_entrant_number="The new entrant number to assign.")
@commands.has_permissions(administrator=True)
async def change_entrant_slash(interaction: discord.Interaction, drawing_name: str, old_entrant_number: int, new_entrant_number: int):
    """Changes the entrant number for a drawing entry (slash command)."""
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if not drawing_id:
            await interaction.response.send_message(f"Drawing '{drawing_name}' not found.")
            return

        cursor.execute("UPDATE entries SET entrant_number = %s WHERE drawing_id = %s AND entrant_number = %s", (new_entrant_number, drawing_id, old_entrant_number))
        mydb.commit()

        if cursor.rowcount > 0:
            await interaction.response.send_message(f"Entrant number changed from {old_entrant_number} to {new_entrant_number} in drawing '{drawing_name}'.")
        else:
            await interaction.response.send_message(f"No entry found with entrant number {old_entrant_number} in drawing '{drawing_name}'.")

    except Exception as e:
        await interaction.response.send_message(f"Error changing entrant number: {e}")

@bot.command(name="change_entrant")
@commands.has_permissions(administrator=True)
async def change_entrant_text(ctx, drawing_name: str, old_entrant_number: int, new_entrant_number: int):
    """Changes the entrant number for a drawing entry (text command)."""
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if not drawing_id:
            await ctx.send(f"Drawing '{drawing_name}' not found.")
            return

        cursor.execute("UPDATE entries SET entrant_number = %s WHERE drawing_id = %s AND entrant_number = %s", (new_entrant_number, drawing_id, old_entrant_number))
        mydb.commit()

        if cursor.rowcount > 0:
            await ctx.send(f"Entrant number changed from {old_entrant_number} to {new_entrant_number} in drawing '{drawing_name}'.")
        else:
            await ctx.send(f"No entry found with entrant number {old_entrant_number} in drawing '{drawing_name}'.")

    except Exception as e:
        await ctx.send(f"Error changing entrant number: {e}")

# --- Add Entry ---

@bot.tree.command(name="add_entry", description="Adds an entry with multiple users in a drawing.")
@app_commands.describe(drawing_name="The name of the drawing.")
@app_commands.describe(users="The users to add to the entry (comma-separated).")
@app_commands.describe(entrant_number="The entrant number (optional).")
@app_commands.describe(entrant_name="The entrant name (optional).")
@has_admin_permissions()
async def add_entry_slash(interaction: discord.Interaction, drawing_name: str, users: str, entrant_number: int = None, entrant_name: str = None):
    """Adds an entry with multiple users in a drawing (slash command)."""
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if not drawing_id:
            await interaction.response.send_message(f"Drawing '{drawing_name}' not found.")
            return

        # Check if all available entries have been assigned
        cursor.execute("SELECT COUNT(*) FROM entries WHERE drawing_id = %s", (drawing_id,))
        num_entries = cursor.fetchone()
        if num_entries >= 30:  # Assuming 30 is the maximum number of entries
            await interaction.response.send_message(f"All available entries for drawing '{drawing_name}' have been assigned.")
            return

        if entrant_number is None:
            cursor.execute("SELECT MAX(entrant_number) FROM entries WHERE drawing_id = %s", (drawing_id,))
            max_entrant = cursor.fetchone()
            entrant_number = max_entrant + 1 if max_entrant else 1

        if entrant_name is None:
            entrant_name = f"Entrant {entrant_number}"

        try:
            cursor.execute("INSERT INTO entries (entrant_number, entrant_name, drawing_id) VALUES (%s, %s, %s)", 
                           (entrant_number, entrant_name, drawing_id))
            entry_id = cursor.lastrowid

            # Check for duplicate users before adding them to entry_users
            existing_user_ids = []
            cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = %s", (entry_id,))
            for row in cursor.fetchall():
                existing_user_ids.append(row)

            added_users = []
            for user in users:
                if user.id not in existing_user_ids:
                    cursor.execute("INSERT INTO entry_users (entry_id, user_id) VALUES (%s, %s)", (entry_id, user.id))
                    added_users.append(user.mention)
                else:
                    await interaction.response.send_message(f"{user.mention} is already in this entry.")

            mydb.commit()
            if added_users:
                await interaction.response.send_message(f"Added entry with entrant number {entrant_number} and name '{entrant_name}' in drawing '{drawing_name}' for {', '.join(added_users)}.")
                for user in users:
                    await user.send(f"You have been added to the drawing '{drawing_name}' with entrant number {entrant_number} and name '{entrant_name}'.", ephemeral=True)
            else:
                await interaction.response.send_message("No new users were added to this entry.")

        except psycopg2.errors.UniqueViolation:
            await interaction.response.send_message("One or more users already have an entry in this drawing with that entrant number.")
        except Exception as e:
            await interaction.response.send_message(f"Error adding entry: {e}")

    except Exception as e:
        await interaction.response.send_message(f"Error adding entry: {e}")

@bot.command(name="add_entry")
@has_admin_permissions()
async def add_entry_text(ctx, drawing_name: str, users: str, entrant_number: int = None, entrant_name: str = None):
    """Adds an entry with multiple users in a drawing (text command)."""
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if not drawing_id:
            await ctx.send(f"Drawing '{drawing_name}' not found.")
            return

        # Check if all available entries have been assigned
        cursor.execute("SELECT COUNT(*) FROM entries WHERE drawing_id = %s", (drawing_id,))
        num_entries = cursor.fetchone()
        if num_entries >= 30:  # Assuming 30 is the maximum number of entries
            await ctx.send(f"All available entries for drawing '{drawing_name}' have been assigned.")
            return

        if entrant_number is None:
            cursor.execute("SELECT MAX(entrant_number) FROM entries WHERE drawing_id = %s", (drawing_id,))
            max_entrant = cursor.fetchone()
            entrant_number = max_entrant + 1 if max_entrant else 1

        if entrant_name is None:
            entrant_name = f"Entrant {entrant_number}"

        try:
            cursor.execute("INSERT INTO entries (entrant_number, entrant_name, drawing_id) VALUES (%s, %s, %s)", 
                           (entrant_number, entrant_name, drawing_id))
            entry_id = cursor.lastrowid

            # Check for duplicate users before adding them to entry_users
            existing_user_ids = []
            cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = %s", (entry_id,))
            for row in cursor.fetchall():
                existing_user_ids.append(row)

            # Split the users string into a list of Member objects
            users = [await commands.MemberConverter().convert(ctx, user.strip()) for user in users.split(",") if user.strip()]

            added_users = []
            for user in users:
                if user.id not in existing_user_ids:
                    cursor.execute("INSERT INTO entry_users (entry_id, user_id) VALUES (%s, %s)", (entry_id, user.id))
                    added_users.append(user.mention)
                else:
                    await ctx.send(f"{user.mention} is already in this entry.")

            mydb.commit()
            if added_users:
                await ctx.send(f"Added entry with entrant number {entrant_number} and name '{entrant_name}' in drawing '{drawing_name}' for {', '.join(added_users)}.")
                for user in users:
                    await user.send(f"You have been added to the drawing '{drawing_name}' with entrant number {entrant_number} and name '{entrant_name}'.", ephemeral=True)
            else:
                await ctx.send("No new users were added to this entry.")

        except psycopg2.errors.UniqueViolation:
            await ctx.send("One or more users already have an entry in this drawing with that entrant number.")
        except Exception as e:
            await ctx.send(f"Error adding entry: {e}")

    except Exception as e:
        await ctx.send(f"Error adding entry: {e}")

# --- Remove Entry ---

@bot.tree.command(name="remove_entry", description="Removes an entry for a user in a drawing.")
@app_commands.describe(drawing_name="The name of the drawing.")
@app_commands.describe(user="The user to remove from the drawing.")
@has_admin_permissions()
async def remove_entry_slash(interaction: discord.Interaction, drawing_name: str, user: discord.Member):
    """Removes an entry for a user in a drawing (slash command)."""
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if not drawing_id:
            await interaction.response.send_message(f"Drawing '{drawing_name}' not found.")
            return

        try:
            cursor.execute("SELECT entry_id FROM entries WHERE user_id = %s AND drawing_id = %s", (user.id, drawing_id))
            entry_id = cursor.fetchone()
            if not entry_id:
                await interaction.response.send_message(f"{user.mention} does not have an entry in this drawing.")
                return

            cursor.execute("DELETE FROM entry_users WHERE user_id = %s AND entry_id = %s", (user.id, entry_id))
            cursor.execute("DELETE FROM entries WHERE entry_id = %s", (entry_id,))
            mydb.commit()
            await interaction.response.send_message(f"Removed entry for {user.mention} in drawing '{drawing_name}'.")
        except Exception as e:
            await interaction.response.send_message(f"Error removing entry: {e}")

    except Exception as e:
        await interaction.response.send_message(f"Error removing entry: {e}")

@bot.command(name="remove_entry")
@has_admin_permissions()
async def remove_entry_text(ctx, drawing_name: str, user: discord.Member):
    """Removes an entry for a user in a drawing (text command)."""
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if not drawing_id:
            await ctx.send(f"Drawing '{drawing_name}' not found.")
            return

        try:
            cursor.execute("SELECT entry_id FROM entries WHERE user_id = %s AND drawing_id = %s", (user.id, drawing_id))
            entry_id = cursor.fetchone()
            if not entry_id:
                await ctx.send(f"{user.mention} does not have an entry in this drawing.")
                return

            cursor.execute("DELETE FROM entry_users WHERE user_id = %s AND entry_id = %s", (user.id, entry_id))
            cursor.execute("DELETE FROM entries WHERE entry_id = %s", (entry_id,))
            mydb.commit()
            await ctx.send(f"Removed entry for {user.mention} in drawing '{drawing_name}'.")
        except Exception as e:
            await ctx.send(f"Error removing entry: {e}")

    except Exception as e:
        await ctx.send(f"Error removing entry: {e}")

# --- Edit Entry ---

@bot.tree.command(name="edit_entry", description="Edits an entry in a drawing.")
@app_commands.describe(drawing_name="The name of the drawing.")
@app_commands.describe(entrant_number="The current entrant number of the entry to edit.")
@app_commands.describe(new_entrant_number="The new entrant number (optional).")
@app_commands.describe(new_entrant_name="The new entrant name (optional).")
@app_commands.describe(eliminated_by="The reason for elimination (optional, can be multiple values).")
@app_commands.describe(status="The new status of the entry (optional).")
@has_admin_permissions()
async def edit_entry_slash(interaction: discord.Interaction, drawing_name: str, entrant_number: int, new_entrant_number: int = None, new_entrant_name: str = None, eliminated_by: str = None, status: str = None):
    """Edits an entry in a drawing (slash command)."""
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if not drawing_id:
            await interaction.response.send_message(f"Drawing '{drawing_name}' not found.")
            return

        try:
            set_clause = []  # Initialize set_clause as an empty list
            if new_entrant_number is not None:
                set_clause.append(f"entrant_number = {new_entrant_number}")
            if new_entrant_name is not None:
                set_clause.append(f"entrant_name = '{new_entrant_name}'")
            if eliminated_by is not None:
                eliminated_by_list = eliminated_by.split(",")  # Split the string by commas
                for eliminated_by_item in eliminated_by:
                    try:
                        # Check if eliminated_by_item contains an entrant number
                        eliminated_by_number = int(eliminated_by_item)
                        # Fetch the entrant_name for the given entrant number
                        cursor.execute("SELECT entrant_name FROM entries WHERE drawing_id = %s AND entrant_number = %s", (drawing_id, eliminated_by_number))
                        eliminated_by_name = cursor.fetchone()
                        if eliminated_by_name:
                            eliminated_by_list.append(eliminated_by_name)
                        else:
                            eliminated_by_list.append(eliminated_by_item)
                    except ValueError:
                        eliminated_by_list.append(eliminated_by_item)

                eliminated_by_str = ", ".join(eliminated_by_list)
                set_clause.append(f"eliminated_by = '{eliminated_by_str}'")
            if status is not None:
                if status.lower() in ('active', 'inactive', 'eliminated'):
                    set_clause.append(f"status = '{status.lower()}'")
                else:
                    await interaction.response.send_message("Invalid status value. Please use 'active', 'inactive', or 'eliminated'.")
                    return

            if not set_clause:
                await interaction.response.send_message("Please provide at least one field to edit.")
                return

            sql = f"UPDATE entries SET {', '.join(set_clause)} WHERE drawing_id = %s AND entrant_number = %s"
            cursor.execute(sql, (drawing_id, entrant_number))
            mydb.commit()

            if cursor.rowcount > 0:
                await interaction.response.send_message(f"Updated entry with entrant number {entrant_number} in drawing '{drawing_name}'.")

                if new_entrant_name is not None:
                    cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = (SELECT entry_id FROM entries WHERE entrant_number = %s AND drawing_id = %s)", (new_entrant_number, drawing_id))
                    user_ids = [row for row in cursor.fetchall()]
                    for user_id in user_ids:
                        user = bot.get_user(user_id)
                        if user:
                            await user.send(f"Your entry in drawing '{drawing_name}' has been updated. Your new entrant name is '{new_entrant_name}'.", ephemeral=True)
            else:
                await interaction.response.send_message(f"No entry found with entrant number {entrant_number} in drawing '{drawing_name}'.")
        except Exception as e:
            await interaction.response.send_message(f"Error editing entry: {e}")

    except Exception as e:
        await interaction.response.send_message(f"Error editing entry: {e}")


@bot.command()
@has_admin_permissions()
async def edit_entry(ctx, drawing_name: str, entrant_number: int, new_entrant_number: int = None, new_entrant_name: str = None, eliminated_by: str = None, status: str = None):  # Change eliminated_by to str
    """Edits an entry in a drawing."""
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if not drawing_id:
            await ctx.send(f"Drawing '{drawing_name}' not found.")
            return

        try:
            set_clause = []  # Initialize set_clause as an empty list
            if new_entrant_number is not None:
                set_clause.append(f"entrant_number = {new_entrant_number}")
            if new_entrant_name is not None:
                set_clause.append(f"entrant_name = '{new_entrant_name}'")
            if eliminated_by is not None:
                eliminated_by_list = eliminated_by.split(",")  # Split the string by commas
                eliminated_by_processed = []  # Initialize eliminated_by_processed as an empty list
                for eliminated_by_item in eliminated_by_list:
                    try:
                        # Check if eliminated_by_item contains an entrant number
                        eliminated_by_number = int(eliminated_by_item)
                        # Fetch the entrant_name for the given entrant number
                        cursor.execute("SELECT entrant_name FROM entries WHERE drawing_id = %s AND entrant_number = %s", (drawing_id, eliminated_by_number))
                        eliminated_by_name = cursor.fetchone()
                        if eliminated_by_name:
                            eliminated_by_processed.append(eliminated_by_name)
                        else:
                            eliminated_by_processed.append(eliminated_by_item)
                    except ValueError:
                        eliminated_by_processed.append(eliminated_by_item)

                eliminated_by = ", ".join(eliminated_by_processed)  # Join the processed list back into a string
                set_clause.append(f"eliminated_by = '{eliminated_by}'")
            if status is not None:
                if status.lower() in ('active', 'inactive', 'eliminated'):
                    set_clause.append(f"status = '{status.lower()}'")
                else:
                    await ctx.send("Invalid status value. Please use 'active', 'inactive', or 'eliminated'.")
                    return

            if not set_clause:
                await ctx.send("Please provide at least one field to edit.")
                return

            sql = f"UPDATE entries SET {', '.join(set_clause)} WHERE drawing_id = %s AND entrant_number = %s"
            cursor.execute(sql, (drawing_id, entrant_number))
            mydb.commit()

            if cursor.rowcount > 0:
                await ctx.send(f"Updated entry with entrant number {entrant_number} in drawing '{drawing_name}'.")

                if new_entrant_name is not None:
                    cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = (SELECT entry_id FROM entries WHERE entrant_number = %s AND drawing_id = %s)", (new_entrant_number, drawing_id))
                    user_ids = [row for row in cursor.fetchall()]
                    for user_id in user_ids:
                        user = bot.get_user(user_id)
                        if user:
                            await user.send(f"Your entry in drawing '{drawing_name}' has been updated. Your new entrant name is '{new_entrant_name}'.", ephemeral=True)
            else:
                await ctx.send(f"No entry found with entrant number {entrant_number} in drawing '{drawing_name}'.")
        except Exception as e:
            await ctx.send(f"Error editing entry: {e}")

    except Exception as e:
        await ctx.send(f"Error editing entry: {e}")

# --- Assign Winner ---

@bot.tree.command(name="assign_winner", description="Assigns the winner of a drawing by user mention or entrant number.")
@app_commands.describe(drawing_name="The name of the drawing.")
@app_commands.describe(winner_identifier="The user mention or entrant number of the winner.")
@has_admin_permissions()
async def assign_winner_slash(interaction: discord.Interaction, drawing_name: str, winner_identifier: str):
    """Assigns the winner of a drawing by user mention or entrant number (slash command)."""
    try:
        # Check both drawings and archived_drawings tables
        cursor.execute("SELECT drawing_id, name FROM drawings WHERE name = %s", (drawing_name,))
        result = cursor.fetchone()
        if not result:
            cursor.execute("SELECT drawing_id, name FROM archived_drawings WHERE name = %s", (drawing_name,))
            result = cursor.fetchone()
            if not result:
                await interaction.response.send_message(f"Drawing '{drawing_name}' not found.")
                return
        drawing_id, actual_drawing_name = result

        try:
            # Attempt to convert winner_identifier to an integer (entrant number)
            entrant_number = int(winner_identifier)
            cursor.execute("SELECT entry_id FROM entries WHERE drawing_id = %s AND entrant_number = %s", (drawing_id, entrant_number))
            winner_id = cursor.fetchone()
            if not winner_id:
                await interaction.response.send_message(f"No entry found with entrant number {entrant_number} in drawing '{drawing_name}'.")
                return

            # Get the users associated with the winning entry
            cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = %s", (winner_id,))
            winner_user_ids = [row for row in cursor.fetchall()]
            winner_str = ', '.join([interaction.guild.get_member(user_id).mention for user_id in winner_user_ids if interaction.guild.get_member(user_id)])
            if not winner_str:
                winner_str = "Unknown User(s)"
        except ValueError:
            # If conversion to integer fails, assume it's a user mention
            try:
                winner = await commands.MemberConverter().convert(interaction, winner_identifier)
                winner_id = winner.id
                winner_str = winner.mention
            except commands.MemberNotFound:
                await interaction.response.send_message(f"Invalid user or entrant number: {winner_identifier}")
                return

        # Check if it's a test drawing
        if actual_drawing_name.startswith("test_"):
            await interaction.response.send_message(f"This is a test drawing. Winner will not be saved.")
            return

        # Announce the winner
        await interaction.response.send_message(f"@here Congratulations to {winner_str} for winning the drawing '{drawing_name}'!")

        # Save the result and update the winner's status
        cursor.execute("INSERT INTO results (drawing_id, winner_id) VALUES (%s, %s)", (drawing_id, winner_id))
        cursor.execute("UPDATE entries SET status = 'winner' WHERE entry_id = %s", (winner_id,))
        mydb.commit()

    except Exception as e:
        await interaction.response.send_message(f"Error assigning winner: {e}")


@bot.command(name="assign_winner")
@has_admin_permissions()
async def assign_winner_text(ctx, drawing_name: str, winner_identifier: str):
    """Assigns the winner of a drawing by user mention or entrant number (text command)."""
    try:
        # Check both drawings and archived_drawings tables
        cursor.execute("SELECT drawing_id, name FROM drawings WHERE name = %s", (drawing_name,))
        result = cursor.fetchone()
        if not result:
            cursor.execute("SELECT drawing_id, name FROM archived_drawings WHERE name = %s", (drawing_name,))
            result = cursor.fetchone()
            if not result:
                await ctx.send(f"Drawing '{drawing_name}' not found.")
                return
        drawing_id, actual_drawing_name = result

        try:
            # Attempt to convert winner_identifier to an integer (entrant number)
            entrant_number = int(winner_identifier)
            cursor.execute("SELECT entry_id FROM entries WHERE drawing_id = %s AND entrant_number = %s", (drawing_id, entrant_number))
            winner_id = cursor.fetchone()
            if not winner_id:
                await ctx.send(f"No entry found with entrant number {entrant_number} in drawing '{drawing_name}'.")
                return

            # Get the users associated with the winning entry
            cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = %s", (winner_id,))
            winner_user_ids = [row for row in cursor.fetchall()]
            winner_str = ', '.join([ctx.guild.get_member(user_id).mention for user_id in winner_user_ids if ctx.guild.get_member(user_id)])
            if not winner_str:
                winner_str = "Unknown User(s)"
        except ValueError:
            # If conversion to integer fails, assume it's a user mention
            try:
                winner = await commands.MemberConverter().convert(ctx, winner_identifier)
                winner_id = winner.id
                winner_str = winner.mention
            except commands.MemberNotFound:
                await ctx.send(f"Invalid user or entrant number: {winner_identifier}")
                return

        # Check if it's a test drawing
        if actual_drawing_name.startswith("test_"):
            await ctx.send(f"This is a test drawing. Winner will not be saved.")
            return

        # Announce the winner
        await ctx.send(f"@here Congratulations to {winner_str} for winning the drawing '{drawing_name}'!")

        # Save the result and update the winner's status
        cursor.execute("INSERT INTO results (drawing_id, winner_id) VALUES (%s, %s)", (drawing_id, winner_id))
        cursor.execute("UPDATE entries SET status = 'winner' WHERE entry_id = %s", (winner_id,))
        mydb.commit()

    except Exception as e:
        await ctx.send(f"Error assigning winner: {e}")

# --- Drawing Results ---

@bot.tree.command(name="drawing_results", description="Displays the results of a drawing.")
@app_commands.describe(drawing_name="The name of the drawing.")
@app_commands.describe(include_archived="Whether to include archived drawings ('yes' or 'no').")
async def drawing_results_slash(interaction: discord.Interaction, drawing_name: str, include_archived: str = "no"):
    """Displays the results of a drawing (slash command)."""
    try:
        if include_archived.lower() == "yes":
            cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
            drawing_id = cursor.fetchone()
            if not drawing_id:
                cursor.execute("SELECT drawing_id FROM archived_drawings WHERE name = %s", (drawing_name,))
                drawing_id = cursor.fetchone()
                if not drawing_id:
                    await interaction.response.send_message(f"Drawing '{drawing_name}' not found.")
                    return
        else:
            cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
            drawing_id = cursor.fetchone()
            if not drawing_id:
                await interaction.response.send_message(f"Drawing '{drawing_name}' not found.")
                return

        cursor.execute("SELECT winner_id FROM results WHERE drawing_id = %s", (drawing_id,))
        winner_id = cursor.fetchone()
        if winner_id:
            cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = %s", (winner_id,))
            winner_user_ids = [row for row in cursor.fetchall()]
            winner_str = ', '.join([interaction.guild.get_member(user_id).mention for user_id in winner_user_ids if interaction.guild.get_member(user_id)])
            if not winner_str:
                winner_str = "Unknown User(s)"
            await interaction.response.send_message(f"The winner of drawing '{drawing_name}' was {winner_str}.")
        else:
            await interaction.response.send_message(f"No winner found for drawing '{drawing_name}'.")

    except Exception as e:
        await interaction.response.send_message(f"Error getting drawing results: {e}")


@bot.command(name="drawing_results")
async def drawing_results_text(ctx, drawing_name: str, include_archived: str = "no"):
    """Displays the results of a drawing (text command)."""
    try:
        if include_archived.lower() == "yes":
            cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
            drawing_id = cursor.fetchone()
            if not drawing_id:
                cursor.execute("SELECT drawing_id FROM archived_drawings WHERE name = %s", (drawing_name,))
                drawing_id = cursor.fetchone()
                if not drawing_id:
                    await ctx.send(f"Drawing '{drawing_name}' not found.")
                    return
        else:
            cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
            drawing_id = cursor.fetchone()
            if not drawing_id:
                await ctx.send(f"Drawing '{drawing_name}' not found.")
                return

        cursor.execute("SELECT winner_id FROM results WHERE drawing_id = %s", (drawing_id,))
        winner_id = cursor.fetchone()
        if winner_id:
            cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = %s", (winner_id,))
            winner_user_ids = [row for row in cursor.fetchall()]
            winner_str = ', '.join([ctx.guild.get_member(user_id).mention for user_id in winner_user_ids if ctx.guild.get_member(user_id)])
            if not winner_str:
                winner_str = "Unknown User(s)"
            await ctx.send(f"The winner of drawing '{drawing_name}' was {winner_str}.")
        else:
            await ctx.send(f"No winner found for drawing '{drawing_name}'.")

    except Exception as e:
        await ctx.send(f"Error getting drawing results: {e}")

# --- Drawing Status ---

@bot.tree.command(name="set_drawing_status", description="Sets the status of a drawing.")
@app_commands.describe(drawing_name="The name of the drawing.")
@app_commands.describe(status="The new status for the drawing ('open' or 'closed').")
@has_admin_permissions()
async def set_drawing_status_slash(interaction: discord.Interaction, drawing_name: str, status: str):
    """Sets the status of a drawing (slash command)."""
    try:
        if status.lower() not in ('open', 'closed'):
            await interaction.response.send_message("Invalid status value. Please use 'open' or 'closed'.")
            return

        cursor.execute("UPDATE drawings SET status = %s WHERE name = %s", (status.lower(), drawing_name))
        mydb.commit()

        if cursor.rowcount > 0:
            await interaction.response.send_message(f"Drawing '{drawing_name}' status set to '{status.lower()}'.")
        else:
            await interaction.response.send_message(f"Drawing '{drawing_name}' not found.")

    except Exception as e:
        await interaction.response.send_message(f"Error setting drawing status: {e}")

@bot.command(name="set_drawing_status")
@has_admin_permissions()
async def set_drawing_status_text(ctx, drawing_name: str, status: str):
    """Sets the status of a drawing (text command)."""
    try:
        if status.lower() not in ('open', 'closed'):
            await ctx.send("Invalid status value. Please use 'open' or 'closed'.")
            return

        cursor.execute("UPDATE drawings SET status = %s WHERE name = %s", (status.lower(), drawing_name))
        mydb.commit()

        if cursor.rowcount > 0:
            await ctx.send(f"Drawing '{drawing_name}' status set to '{status.lower()}'.")
        else:
            await ctx.send(f"Drawing '{drawing_name}' not found.")

    except Exception as e:
        await ctx.send(f"Error setting drawing status: {e}")

# --- Drawing List ---

@bot.tree.command(name="drawings_by_status", description="Lists all drawings with the specified status, or all drawings if no status is provided.")
@app_commands.describe(status="The status of the drawings to list (optional).")
@app_commands.describe(include_archived="Whether to include archived drawings ('yes' or 'no', optional, default: 'no').")
async def drawings_by_status_slash(interaction: discord.Interaction, status: str = None, include_archived: str = "no"):
    """Lists all drawings with the specified status, or all drawings if no status is provided (slash command)."""
    try:
        if status:
            if status.lower() not in ('open', 'closed', 'pending', 'active', 'inactive', 'eliminated', 'winner'):
                await interaction.response.send_message("Invalid status value. Please use one of: 'open', 'closed', 'pending', 'active', 'inactive', 'eliminated', 'winner'.")
                return

            drawings = []
            cursor.execute("SELECT name FROM drawings WHERE status = %s", (status.lower(),))
            drawings.extend([row for row in cursor.fetchall()])
            if include_archived.lower() == "yes":
                cursor.execute("SELECT name FROM archived_drawings WHERE status = %s", (status.lower(),))
                drawings.extend([row for row in cursor.fetchall()])

            if drawings:
                await interaction.response.send_message(f"Drawings with status '{status.lower()}':\n" + "\n".join(drawings))
            else:
                await interaction.response.send_message(f"No drawings found with status '{status.lower()}'.")

        else:
            drawings = []
            cursor.execute("SELECT name, status FROM drawings")
            drawings.extend([(row, row) for row in cursor.fetchall()])
            if include_archived.lower() == "yes":
                cursor.execute("SELECT name, status FROM archived_drawings")
                drawings.extend([(row, row) for row in cursor.fetchall()])

            if drawings:
                message = "**Drawings by status:**\n"
                for name, status in drawings:
                    message += f"- {name}: {status}\n"
                await interaction.response.send_message(message)
            else:
                await interaction.response.send_message("No drawings found.")

    except Exception as e:
        await interaction.response.send_message(f"Error listing drawings by status: {e}")


@bot.command(name="drawings_by_status")
async def drawings_by_status_text(ctx, status: str = None, include_archived: str = "no"):
    """Lists all drawings with the specified status, or all drawings if no status is provided (text command)."""
    try:
        if status:
            if status.lower() not in ('open', 'closed', 'pending', 'active', 'inactive', 'eliminated', 'winner'):
                await ctx.send("Invalid status value. Please use one of: 'open', 'closed', 'pending', 'active', 'inactive', 'eliminated', 'winner'.")
                return

            drawings = []
            cursor.execute("SELECT name FROM drawings WHERE status = %s", (status.lower(),))
            drawings.extend([row for row in cursor.fetchall()])
            if include_archived.lower() == "yes":
                cursor.execute("SELECT name FROM archived_drawings WHERE status = %s", (status.lower(),))
                drawings.extend([row for row in cursor.fetchall()])

            if drawings:
                await ctx.send(f"Drawings with status '{status.lower()}':\n" + "\n".join(drawings))
            else:
                await ctx.send(f"No drawings found with status '{status.lower()}'.")

        else:
            drawings = []
            cursor.execute("SELECT name, status FROM drawings")
            drawings.extend([(row, row) for row in cursor.fetchall()])
            if include_archived.lower() == "yes":
                cursor.execute("SELECT name, status FROM archived_drawings")
                drawings.extend([(row, row) for row in cursor.fetchall()])

            if drawings:
                message = "**Drawings by status:**\n"
                for name, status in drawings:
                    message += f"- {name}: {status}\n"
                await ctx.send(message)
            else:
                await ctx.send("No drawings found.")

    except Exception as e:
        await ctx.send(f"Error listing drawings by status: {e}")

# --- Delete Drawing ---

@bot.tree.command(name="delete_drawing", description="Deletes a drawing after confirmation.")
@app_commands.describe(drawing_name="The name of the drawing to delete.")
@has_admin_permissions()
async def delete_drawing_slash(interaction: discord.Interaction, drawing_name: str):
    """Deletes a drawing after confirmation (slash command)."""

    def check(m):
        return m.author == interaction.user and m.channel == interaction.channel and m.content.lower() in ['yes', 'no']

    await interaction.response.send_message(f"Are you sure you want to delete drawing '{drawing_name}'? This action cannot be undone. (yes/no)")

    try:
        msg = await bot.wait_for('message', check=check, timeout=30)
        if msg.content.lower() == 'yes':
            try:
                cursor.execute("DELETE FROM drawings WHERE name = %s", (drawing_name,))
                mydb.commit()

                if cursor.rowcount > 0:
                    await interaction.followup.send(f"Drawing '{drawing_name}' deleted successfully.")  # Use followup.send
                else:
                    await interaction.followup.send(f"Drawing '{drawing_name}' not found.")  # Use followup.send

            except Exception as e:
                await interaction.followup.send(f"Error deleting drawing: {e}")  # Use followup.send
        else:
            await interaction.followup.send("Deletion cancelled.")  # Use followup.send

    except asyncio.TimeoutError:
        await interaction.followup.send("Confirmation timed out. Deletion cancelled.")  # Use followup.send


@bot.command(name="delete_drawing")
@has_admin_permissions()
async def delete_drawing_text(ctx, drawing_name: str):
    """Deletes a drawing after confirmation (text command)."""

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() in ['yes', 'no']

    await ctx.send(f"Are you sure you want to delete drawing '{drawing_name}'? This action cannot be undone. (yes/no)")

    try:
        msg = await bot.wait_for('message', check=check, timeout=30)
        if msg.content.lower() == 'yes':
            try:
                cursor.execute("DELETE FROM drawings WHERE name = %s", (drawing_name,))
                mydb.commit()

                if cursor.rowcount > 0:
                    await ctx.send(f"Drawing '{drawing_name}' deleted successfully.")
                else:
                    await ctx.send(f"Drawing '{drawing_name}' not found.")

            except Exception as e:
                await ctx.send(f"Error deleting drawing: {e}")
        else:
            await ctx.send("Deletion cancelled.")

    except asyncio.TimeoutError:
        await ctx.send("Confirmation timed out. Deletion cancelled.")

# --- Archive Drawing ---

@bot.command()
@has_admin_permissions()
async def archive_drawing(ctx, drawing_name: str):
    """Archives a drawing."""
    try:
        # Check if the drawing exists
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing = cursor.fetchone()
        if not drawing:
            await ctx.send(f"Drawing '{drawing_name}' not found.")
            return
        drawing_id = drawing[0]  # Unpack drawing_id correctly

        # Create a separate table to store archived drawings (if it doesn't exist)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS archived_drawings (
                drawing_id INT PRIMARY KEY,
                name VARCHAR(255),
                status VARCHAR(255)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS archived_entries (
                entry_id INT PRIMARY KEY,
                entrant_number INT,
                entrant_name VARCHAR(255),
                drawing_id INT,
                eliminated_by VARCHAR(255),
                status VARCHAR(255)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS archived_entry_users (
                entry_id INT,
                user_id BIGINT,
                PRIMARY KEY (entry_id, user_id)
            )
        ''')
        mydb.commit()

        # Move the drawing data to the archive tables
        try:
            # Move drawing
            cursor.execute("INSERT INTO archived_drawings SELECT * FROM drawings WHERE drawing_id = %s", (drawing_id,))
            # Move entries
            cursor.execute("INSERT INTO archived_entries SELECT * FROM entries WHERE drawing_id = %s", (drawing_id,))
            # Move entry_users
            cursor.execute("INSERT INTO archived_entry_users SELECT * FROM entry_users WHERE entry_id IN (SELECT entry_id FROM entries WHERE drawing_id = %s)", (drawing_id,))
            
            # Delete the drawing and related data from the main tables
            cursor.execute("DELETE FROM entry_users WHERE entry_id IN (SELECT entry_id FROM entries WHERE drawing_id = %s)", (drawing_id,))
            cursor.execute("DELETE FROM entries WHERE drawing_id = %s", (drawing_id,))
            cursor.execute("DELETE FROM drawings WHERE drawing_id = %s", (drawing_id,))
            mydb.commit()

            await ctx.send(f"Drawing '{drawing_name}' archived successfully.")

        except Exception as e:
            await ctx.send(f"Error archiving drawing: {e}")
            mydb.rollback()  # Rollback the transaction if there's an error

    except Exception as e:
        await ctx.send(f"Error archiving drawing: {e}")

# --- Restore Drawing ---

@bot.tree.command(name="restore_drawing", description="Restores an archived drawing.")
@app_commands.describe(drawing_name="The name of the drawing to restore.")
@has_admin_permissions()
async def restore_drawing_slash(interaction: discord.Interaction, drawing_name: str):
    """Restores an archived drawing (slash command)."""
    try:
        # Check if the drawing exists in the archive
        cursor.execute("SELECT drawing_id FROM archived_drawings WHERE name = %s", (drawing_name,))
        drawing = cursor.fetchone()
        if not drawing:
            await interaction.response.send_message(f"Archived drawing '{drawing_name}' not found.")
            return
        drawing_id = drawing[0]  # Unpack drawing_id correctly

        # Move the drawing data back to the main tables
        try:
            # Move drawing
            cursor.execute("INSERT INTO drawings SELECT * FROM archived_drawings WHERE drawing_id = %s", (drawing_id,))
            # Move entries
            cursor.execute("INSERT INTO entries SELECT * FROM archived_entries WHERE drawing_id = %s", (drawing_id,))
            # Move entry_users
            cursor.execute("INSERT INTO archived_entry_users SELECT * FROM archived_entry_users WHERE entry_id IN (SELECT entry_id FROM archived_entries WHERE drawing_id = %s)", (drawing_id,))

            # Delete the drawing and related data from the archive tables
            cursor.execute("DELETE FROM archived_entry_users WHERE entry_id IN (SELECT entry_id FROM archived_entries WHERE drawing_id = %s)", (drawing_id,))
            cursor.execute("DELETE FROM archived_entries WHERE drawing_id = %s", (drawing_id,))
            cursor.execute("DELETE FROM archived_drawings WHERE drawing_id = %s", (drawing_id,))
            mydb.commit()

            await interaction.response.send_message(f"Drawing '{drawing_name}' restored successfully.")

        except Exception as e:
            await interaction.response.send_message(f"Error restoring drawing: {e}")
            mydb.rollback()

    except Exception as e:
        await interaction.response.send_message(f"Error restoring drawing: {e}")


@bot.command(name="restore_drawing")
@has_admin_permissions()
async def restore_drawing_text(ctx, drawing_name: str):
    """Restores an archived drawing (text command)."""
    try:
        # Check if the drawing exists in the archive
        cursor.execute("SELECT drawing_id FROM archived_drawings WHERE name = %s", (drawing_name,))
        drawing = cursor.fetchone()
        if not drawing:
            await ctx.send(f"Archived drawing '{drawing_name}' not found.")
            return
        drawing_id = drawing[0]  # Unpack drawing_id correctly

        # Move the drawing data back to the main tables
        try:
            # Move drawing
            cursor.execute("INSERT INTO drawings SELECT * FROM archived_drawings WHERE drawing_id = %s", (drawing_id,))
            # Move entries
            cursor.execute("INSERT INTO entries SELECT * FROM archived_entries WHERE drawing_id = %s", (drawing_id,))
            # Move entry_users
            cursor.execute("INSERT INTO archived_entry_users SELECT * FROM archived_entry_users WHERE entry_id IN (SELECT entry_id FROM archived_entries WHERE drawing_id = %s)", (drawing_id,))

            # Delete the drawing and related data from the archive tables
            cursor.execute("DELETE FROM archived_entry_users WHERE entry_id IN (SELECT entry_id FROM archived_entries WHERE drawing_id = %s)", (drawing_id,))
            cursor.execute("DELETE FROM archived_entries WHERE drawing_id = %s", (drawing_id,))
            cursor.execute("DELETE FROM archived_drawings WHERE drawing_id = %s", (drawing_id,))
            mydb.commit()

            await ctx.send(f"Drawing '{drawing_name}' restored successfully.")

        except Exception as e:
            await ctx.send(f"Error restoring drawing: {e}")
            mydb.rollback()

    except Exception as e:
        await ctx.send(f"Error restoring drawing: {e}")

# --- Drawing Winners ---

@bot.tree.command(name="drawing_winners", description="Displays the winners of a drawing. If no drawing name is provided, shows all historical winners.")
@app_commands.describe(drawing_name="The name of the drawing (optional).")
@app_commands.describe(include_archived="Whether to include archived drawings ('yes' or 'no').")
async def drawing_winners_slash(interaction: discord.Interaction, drawing_name: str = None, include_archived: str = "no"):
    """Displays the winners of a drawing. 
    If no drawing name is provided, shows all historical winners.
    (Slash command)
    """
    try:
        if drawing_name:
            # If drawing_name is provided, check both drawings and archived_drawings tables
            if include_archived.lower() == "yes":
                cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
                drawing_id = cursor.fetchone()
                if not drawing_id:
                    cursor.execute("SELECT drawing_id FROM archived_drawings WHERE name = %s", (drawing_name,))
                    drawing_id = cursor.fetchone()
                    if not drawing_id:
                        await interaction.response.send_message(f"Drawing '{drawing_name}' not found.")
                        return
            else:
                cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
                drawing_id = cursor.fetchone()
                if not drawing_id:
                    await interaction.response.send_message(f"Drawing '{drawing_name}' not found.")
                    return

            cursor.execute("SELECT winner_id FROM results WHERE drawing_id = %s", (drawing_id,))
            winner_entry_ids = [row for row in cursor.fetchall()]

            if winner_entry_ids:
                message = f"**Winners of drawing '{drawing_name}'**:\n"
                for entry_id in winner_entry_ids:
                    cursor.execute("SELECT entrant_number, entrant_name FROM entries WHERE entry_id = %s", (entry_id,))
                    entrant_number, entrant_name = cursor.fetchone()
                    cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = %s", (entry_id,))
                    user_ids = [row for row in cursor.fetchall()]
                    user_mentions = [interaction.guild.get_member(user_id).mention for user_id in user_ids if interaction.guild.get_member(user_id)]

                    message += f"- Entrant Number: {entrant_number}"
                    if entrant_name:
                        message += f", Name: {entrant_name}"
                    message += f": {', '.join(user_mentions) or 'No users found'}\n"

                await interaction.response.send_message(message)
            else:
                await interaction.response.send_message(f"No winners found for drawing '{drawing_name}'.")

        else:
            # If no drawing_name is provided, show all historical winners (including archived)
            drawing_ids = []
            cursor.execute("SELECT DISTINCT drawing_id FROM results")
            drawing_ids.extend([row for row in cursor.fetchall()])
            if include_archived.lower() == "yes":
                cursor.execute("SELECT DISTINCT drawing_id FROM archived_drawings")
                drawing_ids.extend([row for row in cursor.fetchall()])

            if drawing_ids:
                message = "**Historical drawing winners**:\n"
                for drawing_id in drawing_ids:
                    # Check both drawings and archived_drawings tables
                    cursor.execute("SELECT name FROM drawings WHERE drawing_id = %s", (drawing_id,))
                    drawing_name = cursor.fetchone()
                    if not drawing_name:
                        cursor.execute("SELECT name FROM archived_drawings WHERE drawing_id = %s", (drawing_id,))
                        drawing_name = cursor.fetchone()
                    drawing_name = drawing_name if drawing_name else "Unknown Drawing"

                    cursor.execute("SELECT winner_id FROM results WHERE drawing_id = %s", (drawing_id,))
                    winner_entry_ids = [row for row in cursor.fetchall()]

                    message += f"\n**{drawing_name}:**\n"
                    for entry_id in winner_entry_ids:
                        cursor.execute("SELECT entrant_number, entrant_name FROM entries WHERE entry_id = %s", (entry_id,))
                        entrant_number, entrant_name = cursor.fetchone()
                        cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = %s", (entry_id,))
                        user_ids = [row for row in cursor.fetchall()]
                        user_mentions = [interaction.guild.get_member(user_id).mention for user_id in user_ids if interaction.guild.get_member(user_id)]

                        message += f"- Entrant Number: {entrant_number}"
                        if entrant_name:
                            message += f", Name: {entrant_name}"
                        message += f": {', '.join(user_mentions) or 'No users found'}\n"

                await interaction.response.send_message(message)
            else:
                await interaction.response.send_message("No historical winners found.")

    except Exception as e:
        await interaction.response.send_message(f"Error getting drawing winners: {e}")

@bot.command(name="drawing_winners")
async def drawing_winners_text(ctx, drawing_name: str = None, include_archived: str = "no"):
    """Displays the winners of a drawing. 
    If no drawing name is provided, shows all historical winners.
    (Text command)
    """
    try:
        if drawing_name:
            # If drawing_name is provided, check both drawings and archived_drawings tables
            if include_archived.lower() == "yes":
                cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
                drawing_id = cursor.fetchone()
                if not drawing_id:
                    cursor.execute("SELECT drawing_id FROM archived_drawings WHERE name = %s", (drawing_name,))
                    drawing_id = cursor.fetchone()
                    if not drawing_id:
                        await ctx.send(f"Drawing '{drawing_name}' not found.")
                        return
            else:
                cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
                drawing_id = cursor.fetchone()
                if not drawing_id:
                    await ctx.send(f"Drawing '{drawing_name}' not found.")
                    return

            cursor.execute("SELECT winner_id FROM results WHERE drawing_id = %s", (drawing_id,))
            winner_entry_ids = [row for row in cursor.fetchall()]

            if winner_entry_ids:
                message = f"**Winners of drawing '{drawing_name}'**:\n"
                for entry_id in winner_entry_ids:
                    cursor.execute("SELECT entrant_number, entrant_name FROM entries WHERE entry_id = %s", (entry_id,))
                    entrant_number, entrant_name = cursor.fetchone()
                    cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = %s", (entry_id,))
                    user_ids = [row for row in cursor.fetchall()]
                    user_mentions = [ctx.guild.get_member(user_id).mention for user_id in user_ids if ctx.guild.get_member(user_id)]

                    message += f"- Entrant Number: {entrant_number}"
                    if entrant_name:
                        message += f", Name: {entrant_name}"
                    message += f": {', '.join(user_mentions) or 'No users found'}\n"

                await ctx.send(message)
            else:
                await ctx.send(f"No winners found for drawing '{drawing_name}'.")

        else:
            # If no drawing_name is provided, show all historical winners (including archived)
            drawing_ids = []
            cursor.execute("SELECT DISTINCT drawing_id FROM results")
            drawing_ids.extend([row for row in cursor.fetchall()])
            if include_archived.lower() == "yes":
                cursor.execute("SELECT DISTINCT drawing_id FROM archived_drawings")
                drawing_ids.extend([row for row in cursor.fetchall()])

            if drawing_ids:
                message = "**Historical drawing winners**:\n"
                for drawing_id in drawing_ids:
                    # Check both drawings and archived_drawings tables
                    cursor.execute("SELECT name FROM drawings WHERE drawing_id = %s", (drawing_id,))
                    drawing_name = cursor.fetchone()
                    if not drawing_name:
                        cursor.execute("SELECT name FROM archived_drawings WHERE drawing_id = %s", (drawing_id,))
                        drawing_name = cursor.fetchone()
                    drawing_name = drawing_name if drawing_name else "Unknown Drawing"

                    cursor.execute("SELECT winner_id FROM results WHERE drawing_id = %s", (drawing_id,))
                    winner_entry_ids = [row for row in cursor.fetchall()]

                    message += f"\n**{drawing_name}:**\n"
                    for entry_id in winner_entry_ids:
                        cursor.execute("SELECT entrant_number, entrant_name FROM entries WHERE entry_id = %s", (entry_id,))
                        entrant_number, entrant_name = cursor.fetchone()
                        cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = %s", (entry_id,))
                        user_ids = [row for row in cursor.fetchall()]
                        user_mentions = [ctx.guild.get_member(user_id).mention for user_id in user_ids if ctx.guild.get_member(user_id)]

                        message += f"- Entrant Number: {entrant_number}"
                        if entrant_name:
                            message += f", Name: {entrant_name}"
                        message += f": {', '.join(user_mentions) or 'No users found'}\n"

                await ctx.send(message)
            else:
                await ctx.send("No historical winners found.")

    except Exception as e:
        await ctx.send(f"Error getting drawing winners: {e}")

@tasks.loop(seconds=60)  # Check every 60 seconds
async def check_drawings():
    """Checks drawings for automatic closure."""
    cursor.execute("SELECT drawing_id, name, start_time FROM drawings WHERE status = 'open'")
    open_drawings = cursor.fetchall()

    for drawing_id, name, start_time in open_drawings:
        # Check if drawing has reached maximum entries
        cursor.execute("SELECT COUNT(*) FROM entries WHERE drawing_id = %s", (drawing_id,))
        num_entries = cursor.fetchone()[0]
        if num_entries >= 30:
            try:
                cursor.execute("UPDATE drawings SET status = 'closed' WHERE drawing_id = %s", (drawing_id,))
                mydb.commit()
                # Announce the drawing closure
                channel = bot.get_channel(int(os.getenv('DISCORD_CHANNEL_ID')))
                await channel.send(f"Drawing '{name}' has been automatically closed due to reaching the maximum number of entries.")
            except Exception as e:
                print(f"Error closing drawing: {e}")

        # Check if only one active entry remains (excluding 'pending' entries)
        cursor.execute("SELECT COUNT(*) FROM entries WHERE drawing_id = %s AND status = 'active'", (drawing_id,))
        num_active = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM entries WHERE drawing_id = %s AND status IN ('inactive', 'eliminated')", (drawing_id,))
        num_inactive = cursor.fetchone()[0]

        if num_active == 1 and num_inactive > 0:
            try:
                cursor.execute("SELECT entry_id FROM entries WHERE drawing_id = %s AND status = 'active'", (drawing_id,))
                winner_entry_id = cursor.fetchone()[0]

                # Announce the winner
                cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = %s", (winner_entry_id,))
                winner_user_ids = [row[0] for row in cursor.fetchall()]
                winner_str = ', '.join([bot.get_user(user_id).mention for user_id in winner_user_ids if bot.get_user(user_id)])
                if not winner_str:
                    winner_str = "Unknown User(s)"
                await channel.send(f"@here Congratulations to {winner_str} for winning the drawing '{name}'!")

                # Save the result and update the winner's status
                cursor.execute("INSERT INTO results (drawing_id, winner_id) VALUES (%s, %s)", (drawing_id, winner_entry_id))
                cursor.execute("UPDATE entries SET status = 'winner' WHERE entry_id = %s", (winner_entry_id,))
                mydb.commit()

            except Exception as e:
                print(f"Error automatically assigning winner: {e}")

        # Check if drawing has exceeded time limit (example: 1 hour)
        if start_time:
            cursor.execute("SELECT time_limit_hours FROM drawings WHERE drawing_id = %s", (drawing_id,))
            time_limit_hours = cursor.fetchone()
            if time_limit_hours:
                time_limit = time_limit_hours[0] * 60 * 60  # Convert hours to seconds
                elapsed_time = (datetime.datetime.now() - start_time).total_seconds()
                if elapsed_time > time_limit:
                    try:
                        cursor.execute("UPDATE drawings SET status = 'closed' WHERE drawing_id = %s", (drawing_id,))
                        mydb.commit()
                        # Announce the drawing closure
                        channel = bot.get_channel(int(os.getenv('DISCORD_CHANNEL_ID')))
                        await channel.send(f"Drawing '{name}' has been automatically closed due to exceeding the time limit.")
                    except Exception as e:
                        print(f"Error closing drawing: {e}")

@check_drawings.before_loop
async def before_check_drawings():
    """Ensures the bot is ready before starting the task."""
    await bot.wait_until_ready()

# --- Run the Bot ---

async def main():
    @bot.event
    async def on_ready():
        print(f'{bot.user} has connected to Discord!')
        await bot.tree.sync()
        check_drawings.start()

    await bot.start(os.getenv('DISCORD_BOT_TOKEN'))

if __name__ == '__main__':
    asyncio.run(main())

# --- Web portal routes ---

@app.route('/')
@has_admin_permissions_web()
def index():
    cursor.execute("SELECT drawing_id, name, status FROM drawings")
    drawings = cursor.fetchall()
    return render_template('index.html', drawings=drawings)

@app.route('/drawing/<int:drawing_id>')
@has_admin_permissions_web()
def drawing(drawing_id):
    cursor.execute("SELECT * FROM drawings WHERE drawing_id = %s", (drawing_id,))
    drawing = cursor.fetchone()
    if not drawing:
        return "Drawing not found."

    cursor.execute("SELECT e.entrant_number, e.entrant_name, e.status, e.eliminated_by, eu.user_id "
                   "FROM entries e "
                   "LEFT JOIN entry_users eu ON e.entry_id = eu.entry_id "
                   "WHERE e.drawing_id = %s", (drawing_id,))
    entries = cursor.fetchall()

    # Format entries for display
    formatted_entries = []
    for entry in entries:
        entrant_number, entrant_name, status, eliminated_by, user_id = entry
        user = bot.get_user(user_id) if user_id else None
        formatted_entries.append({
            'entrant_number': entrant_number,
            'entrant_name': entrant_name,
            'status': status,
            'eliminated_by': eliminated_by,
            'user': user.mention if user else 'No user assigned'
        })

    return render_template('drawing.html', drawing=drawing, entries=formatted_entries)

@app.route('/start_drawing/<int:drawing_id>')
@has_admin_permissions_web()
def start_drawing_web(drawing_id):
    cursor.execute("SELECT name FROM drawings WHERE drawing_id = %s", (drawing_id,))
    drawing_name = cursor.fetchone()[0]
    asyncio.run_coroutine_threadsafe(start_drawing_cmd(drawing_name), bot.loop)
    return redirect(url_for('index'))

@app.route('/stop_drawing/<int:drawing_id>')
@has_admin_permissions_web()
def stop_drawing_web(drawing_id):
    cursor.execute("SELECT name FROM drawings WHERE drawing_id = %s", (drawing_id,))
    drawing_name = cursor.fetchone()[0]
    asyncio.run_coroutine_threadsafe(stop_drawing_cmd(drawing_name), bot.loop)
    return redirect(url_for('index'))

@app.route('/interactions', methods=['POST'])  # Route for Discord interactions
def interactions():
    # Verify the request signature (see Discord API documentation for details)
    signature = request.headers.get('X-Signature-Ed25519')
    timestamp = request.headers.get('X-Signature-Timestamp')
    body = request.data

    if verify_signature(PUBLIC_KEY, timestamp, body, signature):
        # Parse the interaction data
        data = json.loads(request.data)
        interaction_type = data['type']

        # Handle different interaction types (e.g., PING, APPLICATION_COMMAND)
        if interaction_type == 1:  # PING
            return jsonify({'type': 1})  # Acknowledge ping

        elif interaction_type == 2:  # APPLICATION_COMMAND
            command_name = data['data']['name']
            # Trigger the corresponding bot command
            if command_name == 'start_drawing':
                drawing_name = data['data']['options']['value']
                asyncio.run_coroutine_threadsafe(start_drawing_cmd(drawing_name), bot.loop)
            #... handle other commands...

            return jsonify({'type': 5})  # Acknowledge command

        else:
            return jsonify({'error': 'Unknown interaction type'}), 400

    else:
        return jsonify({'error': 'Invalid request signature'}), 401

@app.route('/add_entry/<int:drawing_id>', methods=['GET', 'POST'])
@has_admin_permissions_web()
def add_entry_web(drawing_id):
    if request.method == 'POST':
        # Get data from the form
        entrant_number = request.form.get('entrant_number')
        entrant_name = request.form.get('entrant_name')
        #user_ids = request.form.getlist('user_ids')  # Get multiple selected users
        users = request.form.get('users') # Get a comma-separated list of user IDs

        # Split the users string into a list of Member objects
#        users = [await commands.MemberConverter().convert(ctx, user.strip()) for user in users.split(",") if user.strip()]

        # Trigger the bot command (using asyncio)
        asyncio.run_coroutine_threadsafe(
            add_entry_cmd(drawing_id, entrant_number, entrant_name, user_ids), 
            bot.loop
        )
        return redirect(url_for('drawing', drawing_id=drawing_id))

    # If GET request, render the form
    return render_template('add_entry.html', drawing_id=drawing_id)

@app.route('/edit_entry/<int:drawing_id>/<int:entrant_number>', methods=['GET', 'POST'])
@has_admin_permissions_web()
def edit_entry_web(drawing_id, entrant_number):
    if request.method == 'POST':
        # Get data from the form
        new_entrant_number = request.form.get('new_entrant_number')
        new_entrant_name = request.form.get('new_entrant_name')
        eliminated_by = request.form.get('eliminated_by')
        status = request.form.get('status')

        # Trigger the bot command (using asyncio)
        asyncio.run_coroutine_threadsafe(
            edit_entry_cmd(drawing_id, entrant_number, new_entrant_number, new_entrant_name, eliminated_by, status), 
            bot.loop
        )
        return redirect(url_for('drawing', drawing_id=drawing_id))

    # If GET request, render the form
    return render_template('edit_entry.html', drawing_id=drawing_id, entrant_number=entrant_number)

#... helper functions for the commands...

async def add_entry_cmd(drawing_id, entrant_number, entrant_name, user_ids):
    """Helper function to add an entry with multiple users in a drawing."""
    try:
        # Check if all available entries have been assigned
        cursor.execute("SELECT COUNT(*) FROM entries WHERE drawing_id = %s", (drawing_id,))
        num_entries = cursor.fetchone()[0]
        if num_entries >= 30:  # Assuming 30 is the maximum number of entries
            print(f"All available entries for drawing '{drawing_id}' have been assigned.")
            return

        if entrant_number is None:
            cursor.execute("SELECT MAX(entrant_number) FROM entries WHERE drawing_id = %s", (drawing_id,))
            max_entrant = cursor.fetchone()[0]
            entrant_number = max_entrant + 1 if max_entrant else 1

        if entrant_name is None:
            entrant_name = f"Entrant {entrant_number}"

        cursor.execute("INSERT INTO entries (entrant_number, entrant_name, drawing_id) VALUES (%s, %s, %s)", 
                       (entrant_number, entrant_name, drawing_id))
        entry_id = cursor.lastrowid

        # Check for duplicate users before adding them to entry_users
        existing_user_ids = []  # Initialize existing_user_ids as an empty list
        cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = %s", (entry_id,))
        for row in cursor.fetchall():
            existing_user_ids.append(row[0])

        added_users = []  # Initialize added_users as an empty list
        for user_id in user_ids:
            if user_id not in existing_user_ids:
                cursor.execute("INSERT INTO entry_users (entry_id, user_id) VALUES (%s, %s)", (entry_id, user_id))
                added_users.append(user_id)
            else:
                print(f"User {user_id} is already in this entry.")

        mydb.commit()
        if added_users:
            print(f"Added entry with entrant number {entrant_number} and name '{entrant_name}' in drawing '{drawing_id}' for {', '.join(map(str, added_users))}.")
        else:
            print("No new users were added to this entry.")

    except psycopg2.errors.UniqueViolation:
        print("One or more users already have an entry in this drawing with that entrant number.")
    except Exception as e:
        print(f"Error adding entry: {e}")

async def edit_entry_cmd(drawing_id, entrant_number, new_entrant_number, new_entrant_name, eliminated_by, status):
    """Helper function to edit an entry in a drawing."""
    try:
        set_clause = []  # Initialize set_clause as an empty list
        if new_entrant_number is not None:
            set_clause.append(f"entrant_number = {new_entrant_number}")
        if new_entrant_name is not None:
            set_clause.append(f"entrant_name = '{new_entrant_name}'")
        if eliminated_by is not None:
            eliminated_by_list = eliminated_by.split(",")
            eliminated_by_processed = []  # Initialize eliminated_by_processed as an empty list
            for eliminated_by_item in eliminated_by_list:
                try:
                    # Check if eliminated_by_item contains an entrant number
                    eliminated_by_number = int(eliminated_by_item)
                    # Fetch the entrant_name for the given entrant number
                    cursor.execute("SELECT entrant_name FROM entries WHERE drawing_id = %s AND entrant_number = %s", (drawing_id, eliminated_by_number))
                    eliminated_by_name = cursor.fetchone()[0]
                    if eliminated_by_name:
                        eliminated_by_processed.append(eliminated_by_name)
                    else:
                        eliminated_by_processed.append(eliminated_by_item)
                except ValueError:
                    eliminated_by_processed.append(eliminated_by_item)

            eliminated_by_str = ", ".join(eliminated_by_processed)
            set_clause.append(f"eliminated_by = '{eliminated_by_str}'")
        if status is not None:
            if status.lower() in ('active', 'inactive', 'eliminated'):
                set_clause.append(f"status = '{status.lower()}'")
            else:
                print("Invalid status value. Please use 'active', 'inactive', or 'eliminated'.")
                return

        if not set_clause:
            print("Please provide at least one field to edit.")
            return

        sql = f"UPDATE entries SET {', '.join(set_clause)} WHERE drawing_id = %s AND entrant_number = %s"
        cursor.execute(sql, (drawing_id, entrant_number))
        mydb.commit()

        if cursor.rowcount > 0:
            print(f"Updated entry with entrant number {entrant_number} in drawing '{drawing_id}'.")

        else:
            print(f"No entry found with entrant number {entrant_number} in drawing '{drawing_id}'.")

    except Exception as e:
        print(f"Error editing entry: {e}")

# Helper functions to simulate the bot command context
async def start_drawing_cmd(drawing_name):
    ctx = lambda: None
    ctx.send = lambda message: print(f"Bot would send: {message}")
    await start_drawing(ctx, drawing_name)

async def stop_drawing_cmd(drawing_name):
    ctx = lambda: None
    ctx.send = lambda message: print(f"Bot would send: {message}")
    await stop_drawing(ctx, drawing_name)

# ... (Add similar helper functions for other commands)

# Admin check for web routes (replace with your authentication mechanism)
def has_admin_permissions_web():
    def decorator(func):
        def wrapper(*args, **kwargs):
            # Add your authentication logic here (e.g., check for a session or API key)
            # If authenticated, return func(*args, **kwargs)
            # Otherwise, return "Access denied" or redirect to a login page
            return func(*args, **kwargs)
        return wrapper
    return decorator

# Run the Flask app and the Discord bot in separate threads
if __name__ == '__main__':
    import threading
    threading.Thread(target=app.run, kwargs={'debug': True}).start()
    bot.run(os.getenv('DISCORD_BOT_TOKEN'))