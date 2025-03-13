from flask import Flask, render_template, request, redirect, url_for, jsonify
import logging
import psycopg2
import asyncio
import os
from dotenv import load_dotenv
import discord
from discord.ext import commands, tasks
import datetime
from discord import app_commands
import random
from tabulate import tabulate
import nacl.signing
import nacl.exceptions

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
logging.basicConfig(level=logging.DEBUG)  # Set logging level for Flask

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
    exit()

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
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if not drawing_id and include_archived:
            cursor.execute("SELECT drawing_id FROM archived_drawings WHERE name = %s", (drawing_name,))
            drawing_id = cursor.fetchone()
        return drawing_id[0] if drawing_id else None
    except psycopg2.Error as e:
        print(f"Error getting drawing ID: {e}")
        return None

async def send_message_to_users(drawing_name, entry_id, message):
    """Helper function to send a message to all users in an entry."""
    try:
        cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = %s", (entry_id,))
        user_ids = [row[0] for row in cursor.fetchall()]
        for user_id in user_ids:
            user = bot.get_user(user_id)
            if user:
                try:
                    await user.send(message, ephemeral=True)
                except discord.Forbidden:
                    print(f"Could not send message to user {user_id} due to permissions.")
                except discord.HTTPException as e:
                    print(f"Failed to send message to user {user_id}: {e}")
    except psycopg2.Error as e:
        print(f"Error sending message to users: {e}")

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
        print("Bad signature error")
        return False

    except ValueError as e:
        print(f"ValueError: {e}")
        return False

    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return False


async def convert_users(ctx, users):
    try:
        return [await commands.MemberConverter().convert(ctx, user.strip()) for user in users.split(",") if user.strip()]
    except commands.errors.MemberNotFound as e:
        await ctx.send(f"Error: {e}")
        return

# --- Bot Commands ---

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    try:
        await bot.tree.sync()
    except Exception as e:
        print(f"Error syncing command tree: {e}")
    check_drawings.start()

@tasks.loop(hours=1)
async def check_drawings():
    """
    Periodically checks for drawings that have reached their time limit and closes them.
    """
    try:
        cursor.execute("SELECT drawing_id, name, time_limit_hours FROM drawings WHERE status = 'open' AND time_limit_hours IS NOT NULL")
        drawings = cursor.fetchall()
        for drawing_id, name, time_limit_hours in drawings:
            cursor.execute("SELECT entry_id, entrant_number FROM entries WHERE drawing_id = %s AND status = 'pending' ORDER BY RANDOM() LIMIT 1", (drawing_id,))
            winner = cursor.fetchone()
            if winner is None:
                await send_message_to_users(name, drawing_id, f"Drawing '{name}' has ended with no eligible entries.")
            else:
                winner_entry_id, entrant_number = winner
                cursor.execute("INSERT INTO results (drawing_id, winner_id) VALUES (%s, %s)", (drawing_id, entrant_number))
                mydb.commit()

                await send_message_to_users(name, winner_entry_id, f"Congratulations! You have won the drawing '{name}'!")
    except Exception as e:
        print(f"Error checking drawings: {e}")

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
@has_admin_permissions()
async def create_drawing_slash(interaction: discord.Interaction, name: str):
    """Creates a new drawing (slash command)."""
    try:
        cursor.execute("INSERT INTO drawings (name) VALUES (%s)", (name,))
        mydb.commit()
        await interaction.response.send_message(f"Drawing '{name}' created!")
    except psycopg2.errors.UniqueViolation:
        await interaction.response.send_message(f"A drawing with the name '{name}' already exists.")
        mydb.rollback()
    except psycopg2.Error as e:
        await interaction.response.send_message(f"Error creating drawing: {e}")
        mydb.rollback()

@bot.command(name="create_drawing")
@has_admin_permissions()
async def create_drawing_text(ctx, name: str):
    """Creates a new drawing (text command)."""
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
        mydb.rollback()
    except psycopg2.Error as e:
        await interaction.response.send_message(f"Error creating test drawing: {e}")
        mydb.rollback()

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
        mydb.rollback()
    except psycopg2.Error as e:
        await ctx.send(f"Error creating test drawing: {e}")
        mydb.rollback()

# --- Join Drawing ---

@bot.tree.command(name="join_drawing", description="Joins a drawing.")
@app_commands.describe(name="The name of the drawing to join.")
async def join_drawing_slash(interaction: discord.Interaction, name: str):
    """Joins a drawing (slash command)."""
    try:
        cursor.execute("SELECT drawing_id, status FROM drawings WHERE name = %s", (name,))
        result = cursor.fetchone()
        if result is None:
            await interaction.response.send_message(f"Drawing '{name}' not found.")
            return

        drawing_id, status = result
        if status == 'closed':
            await interaction.response.send_message(f"Drawing '{name}' is currently closed.")
            return

        cursor.execute("SELECT entrant_number FROM entries WHERE drawing_id = %s", (drawing_id,))
        taken_numbers = [row[0] for row in cursor.fetchall()]
        all_numbers = set(range(1, 31))
        available_numbers = all_numbers - set(taken_numbers)

        if not available_numbers:
            await interaction.response.send_message(f"Drawing '{name}' is full.")
            return

        entrant_number = random.choice(list(available_numbers))

        cursor.execute("INSERT INTO entries (entrant_number, drawing_id) VALUES (%s, %s)", (entrant_number, drawing_id))
        entry_id = cursor.lastrowid
        cursor.execute("INSERT INTO entry_users (entry_id, user_id) VALUES (%s, %s)", (entry_id, interaction.user.id))
        mydb.commit()

        await interaction.response.send_message(f"You have joined the drawing '{name}' with entrant number {entrant_number}.", ephemeral=True)

    except psycopg2.errors.UniqueViolation:
        await interaction.response.send_message(f"{interaction.user.mention}, you've already joined this drawing!")
        mydb.rollback()
    except psycopg2.Error as e:
        await interaction.response.send_message(f"Error joining drawing: {e}")
        mydb.rollback()

@bot.command(name="join_drawing")
async def join_drawing_text(ctx, name: str):
    """Joins a drawing (text command)."""
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

        cursor.execute("SELECT entrant_number FROM entries WHERE drawing_id = %s", (drawing_id,))
        taken_numbers = [row[0] for row in cursor.fetchall()]
        all_numbers = set(range(1, 31))
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

# --- My Entries ---

@bot.tree.command(name="my_entries", description="Displays your drawing entries.")
async def my_entries_slash(interaction: discord.Interaction):
    """Displays the user's entries (slash command)."""
    try:
        cursor.execute("SELECT e.entrant_number, e.entrant_name, e.status, e.eliminated_by, d.name, e.drawing_id "
                    "FROM entries e "
                    "JOIN entry_users eu ON e.entry_id = eu.entry_id "
                    "JOIN drawings d ON e.drawing_id = d.drawing_id "
                    "WHERE eu.user_id = %s", (interaction.user.id,))
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
            await interaction.response.send_message(message, ephemeral=True)
        else:
            await interaction.response.send_message("You haven't joined any drawings yet.", ephemeral=True)
    except psycopg2.Error as e:
        await interaction.response.send_message(f"Error retrieving entries: {e}")
    except Exception as e:
        await interaction.response.send_message(f"Error retrieving entries: {e}")

    except Exception as e:
        await interaction.response.send_message(f"An unexpected error occurred: {e}")



    except Exception as e:
        print(f"An unexpected error occurred: {e}")

# --- Drawing Entries ---

@bot.tree.command(name="drawing_entries", description="Displays the entries for a specific drawing.")
@app_commands.describe(name="The name of the drawing.", include_archived="Whether to include archived entries (yes/no).")
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

        cursor.execute("SELECT entrant_number, entrant_name FROM entries WHERE drawing_id = %s", (drawing_id[0],))
        entries = cursor.fetchall()

        if not entries:
            await interaction.response.send_message(f"No entries found for drawing '{name}'.")
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
        await interaction.response.send_message(f"**Entries for drawing '{name}'**:\n```\n{table}\n```")

    except psycopg2.Error as e:
        await interaction.response.send_message(f"Error retrieving drawing entries: {e}")
    except Exception as e:
        await interaction.response.send_message(f"An unexpected error occurred: {e}")

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

# --- Open Drawing ---

@bot.tree.command(name="open_drawing", description="Opens an existing drawing for entries.")
@app_commands.describe(drawing_name="The name of the drawing.")
@has_admin_permissions()
async def open_drawing_slash(interaction: discord.Interaction, drawing_name: str):
    """Opens an existing drawing for entries (slash command)."""
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if drawing_id is None:
            await interaction.response.send_message(f"Drawing '{drawing_name}' not found.")
            return

        cursor.execute("UPDATE drawings SET status = 'open' WHERE drawing_id = %s", (drawing_id[0],))
        mydb.commit()
        await interaction.response.send_message(f"Drawing '{drawing_name}' opened successfully.")
    except Exception as e:
        mydb.rollback()
        await interaction.response.send_message(f"Error opening drawing: {e}")

@bot.command(name="open_drawing")
@has_admin_permissions()
async def open_drawing_text(ctx, drawing_name: str):
    """Opens an existing drawing for entries (text command)."""
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if drawing_id is None:
            await ctx.send(f"Drawing '{drawing_name}' not found.")
            return

        cursor.execute("UPDATE drawings SET status = 'open' WHERE drawing_id = %s", (drawing_id[0],))
        mydb.commit()
        await ctx.send(f"Drawing '{drawing_name}' opened successfully.")
    except Exception as e:
        mydb.rollback()
        await ctx.send(f"Error opening drawing: {e}")

# --- Close Drawing ---

@bot.tree.command(name="close_drawing", description="Closes an existing drawing, preventing new entries.")
@app_commands.describe(drawing_name="The name of the drawing.")
@has_admin_permissions()
async def close_drawing_slash(interaction: discord.Interaction, drawing_name: str):
    """Closes an existing drawing, preventing new entries (slash command)."""
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if drawing_id is None:
            await interaction.response.send_message(f"Drawing '{drawing_name}' not found.")
            return

        cursor.execute("UPDATE drawings SET status = 'closed' WHERE drawing_id = %s", (drawing_id[0],))
        mydb.commit()
        await interaction.response.send_message(f"Drawing '{drawing_name}' closed successfully.")
    except Exception as e:
        mydb.rollback()
        await interaction.response.send_message(f"Error closing drawing: {e}")

@bot.command(name="close_drawing")
@has_admin_permissions()
async def close_drawing_text(ctx, drawing_name: str):
    """Closes an existing drawing, preventing new entries (text command)."""
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if drawing_id is None:
            await ctx.send(f"Drawing '{drawing_name}' not found.")
            return

        cursor.execute("UPDATE drawings SET status = 'closed' WHERE drawing_id = %s", (drawing_id[0],))
        mydb.commit()
        await ctx.send(f"Drawing '{drawing_name}' closed successfully.")
    except Exception as e:
        mydb.rollback()
        await ctx.send(f"Error closing drawing: {e}")

# --- Add Entry ---

@bot.tree.command(name="add_entry", description="Adds entries to the specified drawing for the mentioned users.")
@app_commands.describe(drawing_name="The name of the drawing.", users="A comma-separated list of users to add to the drawing.")
@has_admin_permissions()
async def add_entry_slash(interaction: discord.Interaction, drawing_name: str, users: str):
    """Adds entries to the specified drawing for the mentioned users (slash command)."""
    try:
        cursor.execute("SELECT drawing_id, status FROM drawings WHERE name = %s", (drawing_name,))
        result = cursor.fetchone()
        if result is None:
            await interaction.response.send_message(f"Drawing '{drawing_name}' not found.")
            return

        drawing_id, status = result
        if status == 'closed':
            await interaction.response.send_message(f"Drawing '{drawing_name}' is closed.")
            return

        user_mentions = [user.strip() for user in users.split(",") if user.strip()]
        converted_users = []
        not_found_users = []

        for user_mention in user_mentions:
            try:
                member = await commands.MemberConverter().convert(interaction, user_mention)
                converted_users.append(member)
            except commands.errors.MemberNotFound:
                not_found_users.append(user_mention)

        if not_found_users:
            await interaction.response.send_message(f"Users not found: {', '.join(not_found_users)}")

        for user in converted_users:
            try:
                cursor.execute("SELECT entrant_number FROM entries WHERE drawing_id = %s", (drawing_id,))
                taken_numbers = [row[0] for row in cursor.fetchall()]
                all_numbers = set(range(1, 31))
                available_numbers = all_numbers - set(taken_numbers)

                if not available_numbers:
                    await interaction.response.send_message(f"Drawing '{drawing_name}' is full.")
                    return

                entrant_number = random.choice(list(available_numbers))

                cursor.execute("INSERT INTO entries (entrant_number, drawing_id) VALUES (%s, %s)", (entrant_number, drawing_id))
                entry_id = cursor.lastrowid
                cursor.execute("INSERT INTO entry_users (entry_id, user_id) VALUES (%s, %s)", (entry_id, user.id))
                mydb.commit()

                await interaction.response.send_message(f"Entry added for {user.mention} in '{drawing_name}' with entrant number {entrant_number}.")

            except Exception as e:
                mydb.rollback()
                await interaction.response.send_message(f"Error adding entry for {user.mention}: {e}")

    except Exception as e:
        mydb.rollback()
        await interaction.response.send_message(f"Error adding entries: {e}")

@bot.command(name="add_entry")
@has_admin_permissions()
async def add_entry_text(ctx, drawing_name: str, *, users: str):
    """Adds entries to the specified drawing for the mentioned users (text command)."""
    try:
        cursor.execute("SELECT drawing_id, status FROM drawings WHERE name = %s", (drawing_name,))
        result = cursor.fetchone()
        if result is None:
            await ctx.send(f"Drawing '{drawing_name}' not found.")
            return

        drawing_id, status = result
        if status == 'closed':
            await ctx.send(f"Drawing '{drawing_name}' is closed.")
            return

        user_mentions = [user.strip() for user in users.split(",") if user.strip()]
        converted_users = []
        not_found_users = []

        for user_mention in user_mentions:
            try:
                member = await commands.MemberConverter().convert(ctx, user_mention)
                converted_users.append(member)
            except commands.errors.MemberNotFound:
                not_found_users.append(user_mention)

        if not_found_users:
            await ctx.send(f"Users not found: {', '.join(not_found_users)}")

        for user in converted_users:
            try:
                cursor.execute("SELECT entrant_number FROM entries WHERE drawing_id = %s", (drawing_id,))
                taken_numbers = [row[0] for row in cursor.fetchall()]
                all_numbers = set(range(1, 31))
                available_numbers = all_numbers - set(taken_numbers)

                if not available_numbers:
                    await ctx.send(f"Drawing '{drawing_name}' is full.")
                    return

                entrant_number = random.choice(list(available_numbers))

                cursor.execute("INSERT INTO entries (entrant_number, drawing_id) VALUES (%s, %s)", (entrant_number, drawing_id))
                entry_id = cursor.lastrowid
                cursor.execute("INSERT INTO entry_users (entry_id, user_id) VALUES (%s, %s)", (entry_id, user.id))
                mydb.commit()

                await ctx.send(f"Entry added for {user.mention} in '{drawing_name}' with entrant number {entrant_number}.")

            except Exception as e:
                mydb.rollback()
                await ctx.send(f"Error adding entry for {user.mention}: {e}")

    except Exception as e:
        mydb.rollback()
        await ctx.send(f"Error adding entries: {e}")

# --- View Entries ---

@bot.tree.command(name="view_entries", description="Displays the list of entries for the specified drawing.")
@app_commands.describe(drawing_name="The name of the drawing.")
async def view_entries_slash(interaction: discord.Interaction, drawing_name: str):
    """Displays the list of entries for the specified drawing (slash command)."""
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if drawing_id is None:
            await interaction.response.send_message(f"Drawing '{drawing_name}' not found.")
            return

        cursor.execute("SELECT entrant_number, entrant_name, status, eliminated_by FROM entries WHERE drawing_id = %s", (drawing_id[0],))
        entries = cursor.fetchall()

        if entries:
            headers = ["Entrant Number", "Entrant Name", "Status", "Eliminated By"]
            table = tabulate(entries, headers=headers, tablefmt="fancy_grid")
            await interaction.response.send_message(f"Entries for '{drawing_name}':\n```\n{table}\n```")
        else:
            await interaction.response.send_message(f"No entries found for '{drawing_name}'.")
    except Exception as e:
        await interaction.response.send_message(f"Error viewing entries: {e}")

@bot.command(name="view_entries")
async def view_entries_text(ctx, drawing_name: str):
    """Displays the list of entries for the specified drawing (text command)."""
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if drawing_id is None:
            await ctx.send(f"Drawing '{drawing_name}' not found.")
            return

        cursor.execute("SELECT entrant_number, entrant_name, status, eliminated_by FROM entries WHERE drawing_id = %s", (drawing_id[0],))
        entries = cursor.fetchall()

        if entries:
            headers = ["Entrant Number", "Entrant Name", "Status", "Eliminated By"]
            table = tabulate(entries, headers=headers, tablefmt="fancy_grid")
            await ctx.send(f"Entries for '{drawing_name}':\n```\n{table}\n```")
        else:
            await ctx.send(f"No entries found for '{drawing_name}'.")
    except Exception as e:
        await ctx.send(f"Error viewing entries: {e}")

# --- Eliminate Entry ---

@bot.tree.command(name="eliminate_entry", description="Eliminates an entry from the specified drawing.")
@app_commands.describe(drawing_name="The name of the drawing.", entrant_number="The entrant number to eliminate.")
@has_admin_permissions()
async def eliminate_entry_slash(interaction: discord.Interaction, drawing_name: str, entrant_number: int):
    """Eliminates an entry from the specified drawing (slash command)."""
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if drawing_id is None:
            await interaction.response.send_message(f"Drawing '{drawing_name}' not found.")
            return

        cursor.execute("UPDATE entries SET status = 'eliminated', eliminated_by = %s WHERE drawing_id = %s AND entrant_number = %s", (interaction.user.name, drawing_id[0], entrant_number))
        mydb.commit()
        await interaction.response.send_message(f"Entry {entrant_number} eliminated from '{drawing_name}'.")
    except Exception as e:
        mydb.rollback()
        await interaction.response.send_message(f"Error eliminating entry: {e}")

@bot.command(name="eliminate_entry")
@has_admin_permissions()
async def eliminate_entry_text(ctx, drawing_name: str, entrant_number: int):
    """Eliminates an entry from the specified drawing (text command)."""
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if drawing_id is None:
            await ctx.send(f"Drawing '{drawing_name}' not found.")
            return

        cursor.execute("UPDATE entries SET status = 'eliminated', eliminated_by = %s WHERE drawing_id = %s AND entrant_number = %s", (ctx.author.name, drawing_id[0], entrant_number))
        mydb.commit()
        await ctx.send(f"Entry {entrant_number} eliminated from '{drawing_name}'.")
    except Exception as e:
        mydb.rollback()
        await ctx.send(f"Error eliminating entry: {e}")

# --- Draw Winner ---

@bot.tree.command(name="draw_winner", description="Randomly draws a winner from the remaining entries.")
@app_commands.describe(drawing_name="The name of the drawing.")
@has_admin_permissions()
async def draw_winner_slash(interaction: discord.Interaction, drawing_name: str):
    """Randomly draws a winner from the remaining entries (slash command)."""
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if drawing_id is None:
            await interaction.response.send_message(f"Drawing '{drawing_name}' not found.")
            return

        cursor.execute("SELECT entry_id, entrant_number FROM entries WHERE drawing_id = %s AND status = 'pending' ORDER BY RANDOM() LIMIT 1", (drawing_id[0],))
        winner = cursor.fetchone()
        if winner is None:
            await interaction.response.send_message(f"No eligible entries found for '{drawing_name}'.")
            return

        winner_entry_id, entrant_number = winner
        cursor.execute("INSERT INTO results (drawing_id, winner_id) VALUES (%s, %s)", (drawing_id[0], entrant_number))
        mydb.commit()

        await send_message_to_users(drawing_name, winner_entry_id, f"Congratulations! You have won the drawing '{drawing_name}'!")
        await interaction.response.send_message(f"The winner of '{drawing_name}' is entrant number {entrant_number}!")

    except Exception as e:
        mydb.rollback()
        await interaction.response.send_message(f"Error drawing winner: {e}")

@bot.command(name="draw_winner")
@has_admin_permissions()
async def draw_winner_text(ctx, drawing_name: str):
    """Randomly draws a winner from the remaining entries (text command)."""
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if drawing_id is None:
            await ctx.send(f"Drawing '{drawing_name}' not found.")
            return

        cursor.execute("SELECT entry_id, entrant_number FROM entries WHERE drawing_id = %s AND status = 'pending' ORDER BY RANDOM() LIMIT 1", (drawing_id[0],))
        winner = cursor.fetchone()
        if winner is None:
            await ctx.send(f"No eligible entries found for '{drawing_name}'.")
            return

        winner_entry_id, entrant_number = winner
        cursor.execute("INSERT INTO results (drawing_id, winner_id) VALUES (%s, %s)", (drawing_id[0], entrant_number))
        mydb.commit()

        await send_message_to_users(drawing_name, winner_entry_id, f"Congratulations! You have won the drawing '{drawing_name}'!")
        await ctx.send(f"The winner of '{drawing_name}' is entrant number {entrant_number}!")

    except Exception as e:
        mydb.rollback()
        await ctx.send(f"Error drawing winner: {e}")

# --- Archive Drawing ---

@bot.tree.command(name="archive_drawing", description="Archives the specified drawing.")
@app_commands.describe(drawing_name="The name of the drawing to archive.")
@has_admin_permissions()
async def archive_drawing_slash(interaction: discord.Interaction, drawing_name: str):
    """Archives the specified drawing (slash command)."""
    try:
        cursor.execute("SELECT drawing_id, status FROM drawings WHERE name = %s", (drawing_name,))
        drawing = cursor.fetchone()
        if drawing is None:
            await interaction.response.send_message(f"Drawing '{drawing_name}' not found.")
            return

        drawing_id, status = drawing
        cursor.execute("INSERT INTO archived_drawings (drawing_id, name, status) VALUES (%s, %s, %s)", (drawing_id, drawing_name, status))
        cursor.execute("INSERT INTO archived_entries (entry_id, entrant_number, entrant_name, drawing_id, eliminated_by, status) SELECT entry_id, entrant_number, entrant_name, drawing_id, eliminated_by, status FROM entries WHERE drawing_id = %s", (drawing_id,))
        cursor.execute("INSERT INTO archived_entry_users (entry_id, user_id) SELECT entry_id, user_id FROM entry_users WHERE entry_id IN (SELECT entry_id FROM entries WHERE drawing_id = %s)", (drawing_id,))
        cursor.execute("DELETE FROM entries WHERE drawing_id = %s", (drawing_id,))
        cursor.execute("DELETE FROM entry_users WHERE entry_id IN (SELECT entry_id FROM entries WHERE drawing_id = %s)", (drawing_id,))
        cursor.execute("DELETE FROM drawings WHERE drawing_id = %s", (drawing_id,))
        mydb.commit()
        await interaction.response.send_message(f"Drawing '{drawing_name}' archived successfully.")
    except Exception as e:
        mydb.rollback()
        await interaction.response.send_message(f"Error archiving drawing: {e}")

@bot.command(name="archive_drawing")
@has_admin_permissions()
async def archive_drawing_text(ctx, drawing_name: str):
    """Archives the specified drawing (text command)."""
    try:
        cursor.execute("SELECT drawing_id, status FROM drawings WHERE name = %s", (drawing_name,))
        drawing = cursor.fetchone()
        if drawing is None:
            await ctx.send(f"Drawing '{drawing_name}' not found.")
            return

        drawing_id, status = drawing
        cursor.execute("INSERT INTO archived_drawings (drawing_id, name, status) VALUES (%s, %s, %s)", (drawing_id, drawing_name, status))
        cursor.execute("INSERT INTO archived_entries (entry_id, entrant_number, entrant_name, drawing_id, eliminated_by, status) SELECT entry_id, entrant_number, entrant_name, drawing_id, eliminated_by, status FROM entries WHERE drawing_id = %s", (drawing_id,))
        cursor.execute("INSERT INTO archived_entry_users (entry_id, user_id) SELECT entry_id, user_id FROM entry_users WHERE entry_id IN (SELECT entry_id FROM entries WHERE drawing_id = %s)", (drawing_id,))
        cursor.execute("DELETE FROM entries WHERE drawing_id = %s", (drawing_id,))
        cursor.execute("DELETE FROM entry_users WHERE entry_id IN (SELECT entry_id FROM entries WHERE drawing_id = %s)", (drawing_id,))
        cursor.execute("DELETE FROM drawings WHERE drawing_id = %s", (drawing_id,))
        mydb.commit()
        await ctx.send(f"Drawing '{drawing_name}' archived successfully.")
    except Exception as e:
        mydb.rollback()
        await ctx.send(f"Error archiving drawing: {e}")

# --- Available Commands ---

async def get_available_commands(ctx):
    """
    Returns a list of available commands for the user based on their permissions.
    """
    available_commands = []
    for command in bot.commands:
        try:
            # Check if the user has permission to run the command
            if await command.can_run(ctx):  # Use 'await' since can_run is a coroutine
                available_commands.append(command.name)
        except commands.errors.CommandError:
            # Ignore commands the user doesn't have permission for
            pass

    # Sort the list of commands alphabetically
    available_commands.sort()

    return available_commands


@bot.tree.command(name="available_commands", description="Shows the commands you can use.")
async def available_commands_slash(interaction: discord.Interaction):
    """
    Shows the available commands for the user (slash command).
    """
    try:
        # Get the available commands for the user in the current context
        available_commands = await get_available_commands(interaction)  # Use 'await' since get_available_commands is a coroutine

        if available_commands:
            # Format the commands into a message
            commands_message = "Available commands:\n"
            for command in available_commands:
                commands_message += f"- `{command}`\n"
            await interaction.response.send_message(commands_message, ephemeral=True)
        else:
            await interaction.response.send_message("You have no available commands in this context.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error getting available commands: {e}")


@bot.command(name="available_commands")
async def available_commands_text(ctx):
    """
    Shows the available commands for the user (text command).
    """
    try:
        # Get the available commands for the user in the current context
        available_commands = await get_available_commands(ctx)  # Use 'await' since get_available_commands is a coroutine

        if available_commands:
            # Format the commands into a message
            commands_message = "Available commands:\n"
            for command in available_commands:
                commands_message += f"- `{command}`\n"
            await ctx.send(commands_message)
        else:
            await ctx.send("You have no available commands in this context.")
    except Exception as e:
        await ctx.send(f"Error getting available commands: {e}")

# --- Flask Routes ---

@app.route('/interactions', methods=['POST'])
async def interactions():
    """
    Route for handling Discord interactions.
    """
    # Log the request
    logging.debug("Received request:")
    logging.debug(f"Headers: {request.headers}")
    logging.debug(f"Body: {await request.get_data()}")  # Log the raw body

    try:
        # Verify the signature
        signature = request.headers.get('X-Signature-Ed25519')
        timestamp = request.headers.get('X-Signature-Timestamp')
        body = request.data.decode("utf-8")

        if verify_signature(PUBLIC_KEY, timestamp, body, signature):
            # Handle the interaction
            interaction = discord.Interaction.from_json(body)
            await bot.process_application_commands(interaction)
            return ('', 200)
        else:
            return ('invalid request signature', 401)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return ('', 500)

@app.route("/test")
async def test():
    """
    Route for testing the Flask app.
    """
    return "Test route works!"

if __name__ == '__main__':
    # Run the Flask app and the Discord bot concurrently
    loop = asyncio.get_event_loop()
    loop.create_task(bot.start(DISCORD_BOT_TOKEN))
    app.run(debug=True, use_reloader=False)
