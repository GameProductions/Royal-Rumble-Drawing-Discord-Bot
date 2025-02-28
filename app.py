from flask import Flask, render_template, request, redirect, url_for
import psycopg2  # Import psycopg2 for PostgreSQL
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
        return False
    except ValueError as e:
        print(f"Error in verify_signature - Value Error: {e}")
        return False
    except Exception as e:
        print(f"Error in verify_signature - General Error: {e}")
        return False


async def convert_users(ctx, users):
    try:
        return [await commands.MemberConverter().convert(ctx, user.strip()) for user in users.split(",") if user.strip()]
    except commands.errors.MemberNotFound as e:
        await ctx.send(f"Error: {e}")
        return []

# --- Bot Commands ---

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    try:
        await bot.tree.sync()
    except Exception as e:
        print(f"Error syncing command tree: {e}")
    # Removed check drawings due to issues, will replace in the future
    # check_drawings.start()

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
        mydb.rollback()  # Rollback on constraint violation
    except psycopg2.Error as e:
        await interaction.response.send_message(f"Error creating drawing: {e}")
        mydb.rollback() #rollback on error

@bot.command(name="create_drawing")
async def create_drawing_text(ctx, name: str):
    """Creates a new drawing (text command)."""
    try:
        cursor.execute("INSERT INTO drawings (name) VALUES (%s)", (name,))
        mydb.commit()
        await ctx.send(f"Drawing '{name}' created!")
    except psycopg2.errors.UniqueViolation:
        await ctx.send(f"A drawing with the name '{name}' already exists.")
        mydb.rollback() #rollback on constraint violation
    except psycopg2.Error as e:
        await ctx.send(f"Error creating drawing: {e}")
        mydb.rollback() #rollback on error

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
        mydb.rollback() #rollback on constraint violation
    except psycopg2.Error as e:
        await interaction.response.send_message(f"Error creating test drawing: {e}")
        mydb.rollback() #rollback on error

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
        mydb.rollback() #rollback on constraint violation
    except psycopg2.Error as e:
        await ctx.send(f"Error creating test drawing: {e}")
        mydb.rollback() #rollback on error

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

        # Get all available entrant numbers
        cursor.execute("SELECT entrant_number FROM entries WHERE drawing_id = %s", (drawing_id,))
        taken_numbers = [row[0] for row in cursor.fetchall()]  # Correctly fetch entrant_number
        all_numbers = set(range(1, 31))  # Adjust the range if necessary
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
        mydb.rollback() #rollback on constraint violation
    except psycopg2.Error as e:
        await interaction.response.send_message(f"Error joining drawing: {e}")
        mydb.rollback() #rollback on error

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

        # Get all available entrant numbers
        cursor.execute("SELECT entrant_number FROM entries WHERE drawing_id = %s", (drawing_id,))
        taken_numbers = [row[0] for row in cursor.fetchall()]  # Correctly fetch entrant_number
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
        mydb.rollback() #rollback on constraint violation
    except psycopg2.Error as e:
        await ctx.send(f"Error joining drawing: {e}")
        mydb.rollback() #rollback on error

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

    except Exception as e:
        print(f"An unexpected error occurred: {e}")