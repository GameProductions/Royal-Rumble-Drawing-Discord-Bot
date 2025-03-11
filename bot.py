import discord
from discord.ext import commands, tasks
import psycopg2  # Import psycopg2 for PostgreSQL
import asyncio
import random
from tabulate import tabulate
import os
from dotenv import load_dotenv
import datetime

# Load environment variables from .env file
load_dotenv()

# Access environment variables
DB_HOST = "db"
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_NAME = os.getenv('DB_NAME')
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
DISCORD_CHANNEL_ID = os.getenv('DISCORD_CHANNEL_ID')

# Database setup (using PostgreSQL)
try:
    mydb = psycopg2.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME
    )
    cursor = mydb.cursor()
except psycopg2.Error as e:
    print(f"Database connection error: {e}")
    exit()  # Exit the script if database connection fails

# Create the tables if they don't exist
try:
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
except psycopg2.Error as e:
    print(f"Error creating tables: {e}")
    mydb.rollback()
    exit()

# Initialize the bot
bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

# Variable to store the selected channel ID (Default=None)
selected_channel_id = None

# Variable to store the admin role ID (Default=None)
admin_role_id = None

# --- Discord bot commands ---

# --- Bot Ready Event ---
@bot.event
async def on_ready():
    try:
        print(f"Bot activated. Logged in as {bot.user}")
    except Exception as e:
        print(f"An error occurred during the on_ready event: {e}")

@bot.command()
@commands.has_permissions(administrator=True)
async def set_channel(ctx, channel: discord.TextChannel):
    """Sets the channel for the bot to use. (Admin Only, Text Command)"""
    try:
        global selected_channel_id
        selected_channel_id = channel.id
        await ctx.send(f"Channel set to {channel.mention}")
    except Exception as e:
        await ctx.send(f"Error setting channel: {e}")

@bot.command()
async def test_channel(ctx):
    """Sends a test message to the selected channel. (User, Text Command)"""
    try:
        if selected_channel_id is None:
            await ctx.send("No channel has been selected. Use `!set_channel` to select a channel.")
            return

        channel = bot.get_channel(selected_channel_id)
        if channel is None:
            await ctx.send("The selected channel could not be found. Please check the channel ID or set a new channel.")
            return

        await channel.send("This is a test message from the bot!")
    except Exception as e:
        await ctx.send(f"Error sending test message: {e}")

@bot.command()
@commands.has_permissions(administrator=True)
async def set_admin_role(ctx, role: discord.Role):
    """Sets the specified role as the admin role for the bot. (Admin Only, Text Command)"""
    try:
        global admin_role_id
        admin_role_id = role.id
        await ctx.send(f"Role '{role.name}' has been set as the admin role.")
    except Exception as e:
        await ctx.send(f"Error setting admin role: {e}")


# Custom decorator to check for admin role
def has_admin_permissions():
    async def predicate(ctx):
        if admin_role_id is None:
            return ctx.author.guild_permissions.administrator
        return ctx.author.get_role(admin_role_id) is not None
    return commands.check(predicate)

# --- Create Drawing ---
@bot.command()
async def create_drawing(ctx, name: str):
    """Creates a new drawing. (User, Text Command)"""
    try:
        cursor.execute("INSERT INTO drawings (name) VALUES (%s)", (name,))
        mydb.commit()
        await ctx.send(f"Drawing '{name}' created!")
    except psycopg2.errors.UniqueViolation:
        await ctx.send(f"A drawing with the name '{name}' already exists.")
        mydb.rollback()
    except psycopg2.Error as e:
        await ctx.send(f"Error creating drawing: {e}")
        mydb.rollback()
    except Exception as e:
        await ctx.send(f"An unexpected error occurred: {e}")
        mydb.rollback()

# --- Create Test Drawing ---
@bot.command()
@has_admin_permissions()
async def create_test_drawing(ctx, name: str):
    """Creates a test drawing that does not save results. (Admin Only, Text Command)"""
    try:
        cursor.execute("INSERT INTO drawings (name, status) VALUES (%s, 'open')", (f"test_{name}",))
        mydb.commit()
        await ctx.send(f"Test drawing '{name}' created!")
    except psycopg2.errors.UniqueViolation:
        await ctx.send(f"A drawing with the name '{name}' already exists.")
        mydb.rollback()
    except psycopg2.Error as e:
        await ctx.send(f"Error creating test drawing: {e}")
        mydb.rollback()
    except Exception as e:
        await ctx.send(f"An unexpected error occurred: {e}")
        mydb.rollback()

# --- Join Drawing ---
async def join_drawing(ctx, name: str):
    try:
        cursor.execute("SELECT drawing_id, status FROM drawings WHERE name = %s", (name,))
        result = cursor.fetchone()
        if result is None:
            await ctx.send(f"Drawing '{name}' not found.")
            return

        drawing_id, status = result
        if status == 'closed':
            await ctx.send(f"Drawing '{name}' is currently closed.")
            return

        # Get all available entrant numbers
        cursor.execute("SELECT entrant_number FROM entries WHERE drawing_id = %s", (drawing_id,))
        taken_numbers = [row[0] for row in cursor.fetchall()]
        all_numbers = set(range(1, 31))  # Adjust the range if necessary
        available_numbers = all_numbers - set(taken_numbers)

        if not available_numbers:
            await ctx.send(f"Drawing '{name}' is full.")
            return

        entrant_number = random.choice(list(available_numbers))

        cursor.execute("INSERT INTO entries (entrant_number, drawing_id) VALUES (%s, %s)", (entrant_number, drawing_id))
        entry_id = cursor.lastrowid
        cursor.execute("INSERT INTO entry_users (entry_id, user_id) VALUES (%s, %s)", (entry_id, ctx.author.id))
        mydb.commit()

        await ctx.author.send(f"You have joined the drawing '{name}' with entrant number {entrant_number}.", ephemeral=True)

    except psycopg2.errors.UniqueViolation:
        await ctx.send(f"{ctx.author.mention}, you've already joined this drawing!")
        mydb.rollback()
    except psycopg2.Error as e:
        await ctx.send(f"Error joining drawing: {e}")
        mydb.rollback()
    except Exception as e:
        await ctx.send(f"An unexpected error occurred: {e}")
        mydb.rollback()

# --- My Entries ---
@bot.command()
async def my_entries(ctx):
    """Displays the user's entries. (User, Text Command)"""
    try:
        cursor.execute("SELECT e.entrant_number, e.entrant_name, e.status, e.eliminated_by, d.name, e.drawing_id "
                    "FROM entries e "
                    "JOIN entry_users eu ON e.entry_id = eu.entry_id "
                    "JOIN drawings d ON e.drawing_id = d.drawing_id "
                    "WHERE eu.user_id = %s", (ctx.author.id,))
        entries = cursor.fetchall()
        if entries:
            message = "Your drawing entries:\n"
            for entry in entries:
                message += f"- {entry[4]}: Entrant number {entry[0]}"
                if entry[1]:
                    message += f", Name: {entry[1]}"
                message += f", Status: {entry[2]}"
                if entry[2] == 'eliminated' and entry[3]:
                    message += f", Eliminated by: {entry[3]}"
                message += "\n"
            await ctx.author.send(message, ephemeral=True)
        else:
            await ctx.author.send("You haven't joined any drawings yet.", ephemeral=True)
    except psycopg2.Error as e:
        await ctx.send(f"Error retrieving entries: {e}")
    except Exception as e:
        await ctx.send(f"Error retrieving entries: {e}")
    except Exception as e:
        await ctx.send(f"An unexpected error occurred: {e}")

# --- Drawing Entries ---
@bot.command()
@commands.has_permissions(administrator=True)
async def drawing_entries(ctx, name: str, include_archived: str = "no"):
    """Displays the entries for a specific drawing in a table format. (Admin Only)

    include_archived: Whether to include archived entries. Options: 'yes', 'no' (default: 'no') (Admin Only, Text Command)
    """
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

        cursor.execute("SELECT entrant_number, entrant_name FROM entries WHERE drawing_id = %s", (drawing_id[0],))
        entries = cursor.fetchall()

        if not entries:
            await ctx.send(f"No entries found for drawing '{name}'.")
            return

        table_data = []
        for entrant_number, entrant_name in entries:
            cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = (SELECT entry_id FROM entries WHERE entrant_number = %s AND drawing_id = %s)", (entrant_number, drawing_id[0]))
            user_ids = [row[0] for row in cursor.fetchall()]
            user_mentions = [bot.get_user(user_id).mention for user_id in user_ids if bot.get_user(user_id)]

            cursor.execute("SELECT winner_id FROM results WHERE drawing_id = %s", (drawing_id[0],))
            winner_entry_id = cursor.fetchone()
            if winner_entry_id and winner_entry_id[0] == entrant_number:
                table_data.append([f"**{entrant_number}**", f"**{entrant_name or ''}** 🏆", f"**{', '.join(user_mentions) or 'No users found'}**"])
            else:
                table_data.append([entrant_number, entrant_name or "", ", ".join(user_mentions) or "No users found"])

        table = tabulate(table_data, headers=["Entrant Number", "Entrant Name", "Users"], tablefmt="simple")
        await ctx.send(f"**Entries for drawing '{name}'**:\n```\n{table}\n```")

    except psycopg2.Error as e:
        await ctx.send(f"Error retrieving drawing entries: {e}")
    except Exception as e:
        await ctx.send(f"An unexpected error occurred: {e}")

# --- Start Drawing ---
@bot.command()
@commands.has_permissions(administrator=True)
async def start_drawing(ctx, name: str):
    """Starts a drawing. (Admin Only, Text Command)"""
    try:
        cursor.execute("UPDATE drawings SET status = 'open' WHERE name = %s", (name,))
        mydb.commit()
        if cursor.rowcount > 0:
            await ctx.send(f"Drawing '{name}' started!")
        else:
            await ctx.send(f"Drawing '{name}' not found.")
    except psycopg2.Error as e:
        await ctx.send(f"Error starting drawing: {e}")
        mydb.rollback()
    except Exception as e:
        await ctx.send(f"An unexpected error occurred: {e}")
        mydb.rollback()

# --- Stop Drawing ---
@bot.command()
@commands.has_permissions(administrator=True)
async def stop_drawing(ctx, name: str):
    """Stops a drawing. (Admin Only, Text Command)"""
    try:
        cursor.execute("UPDATE drawings SET status = 'closed' WHERE name = %s", (name,))
        mydb.commit()
        if cursor.rowcount > 0:
            await ctx.send(f"Drawing '{name}' stopped!")
        else:
            await ctx.send(f"Drawing '{name}' not found.")
    except psycopg2.Error as e:
        await ctx.send(f"Error stopping drawing: {e}")
        mydb.rollback()
    except Exception as e:
        await ctx.send(f"An unexpected error occurred: {e}")
        mydb.rollback()

# --- Drawing Status ---
@bot.command()
async def drawing_status(ctx, name: str, include_archived: str = "no"):
    """Displays the status of a drawing. (User)
    
    include_archived: Whether to include archived drawings. Options: 'yes', 'no' (default: 'no')
    """
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

        await ctx.send(f"Drawing '{name}' is currently {status[0]}.")

    except psycopg2.Error as e:
        await ctx.send(f"Error getting drawing status: {e}")
    except Exception as e:
        await ctx.send(f"An unexpected error occurred: {e}")

# --- Change Entrant ---
@bot.command()
@commands.has_permissions(administrator=True)
async def change_entrant(ctx, drawing_name: str, old_entrant_number: int, new_entrant_number: int):
    """Changes the entrant number for a drawing entry. (Admin Only, Text Command)"""
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if not drawing_id:
            await ctx.send(f"Drawing '{drawing_name}' not found.")
            return

        cursor.execute("UPDATE entries SET entrant_number = %s WHERE drawing_id = %s AND entrant_number = %s", (new_entrant_number, drawing_id[0], old_entrant_number))
        mydb.commit()

        if cursor.rowcount > 0:
            await ctx.send(f"Entrant number changed from {old_entrant_number} to {new_entrant_number} in drawing '{drawing_name}'.")
        else:
            await ctx.send(f"No entry found with entrant number {old_entrant_number} in drawing '{drawing_name}'.")

    except psycopg2.Error as e:
        await ctx.send(f"Error changing entrant number: {e}")
        mydb.rollback()
    except Exception as e:
        await ctx.send(f"An unexpected error occurred: {e}")
        mydb.rollback()

# --- Add Entry ---
@bot.command()
@has_admin_permissions()
async def add_entry(ctx, drawing_name: str, users: commands.Greedy[discord.Member], entrant_number: int = None, entrant_name: str = None):
    """Adds an entry with multiple users in a drawing. (Admin Only, Text Command)"""
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if not drawing_id:
            await ctx.send(f"Drawing '{drawing_name}' not found.")
            return
    except psycopg2.Error as e:
        await ctx.send(f"Error adding entry: {e}")
        mydb.rollback()
    except Exception as e:
        await ctx.send(f"An unexpected error occurred: {e}")
        mydb.rollback()

bot.run(DISCORD_BOT_TOKEN)  # Run the bot with the specified token