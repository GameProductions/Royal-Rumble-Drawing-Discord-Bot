import discord
from discord.ext import commands, tasks
import psycopg2
import asyncio
import random
from tabulate import tabulate
import os
from dotenv import load_dotenv
import datetime

# Load environment variables from .env file
load_dotenv()

# Database setup (using PostgreSQL)
try:
    mydb = psycopg2.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME')
    )
    cursor = mydb.cursor()
except psycopg2.OperationalError as e:
    print(f"Database connection error: {e}")
    exit(1)

# Initialize the bot with the specified command prefix and intents
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

# Store the allowed channel ID and admin role ID
allowed_channel_id = None  # If None, bot commands can be used in any channel
admin_role_id = None  # If None, any user can use admin commands

# Create the tables (if they don't exist) when the bot starts
@bot.event
async def on_ready():
    """
    Creates the necessary tables in the database if they don't exist.
    This function is called when the bot is ready to connect to Discord.
    """
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
        print(f'{bot.user.name} has connected to Discord!')
    except Exception as e:
        print(f"Error creating tables: {e}")

# Function to check if a drawing exists
def drawing_exists(drawing_name):
    """
    Checks if a drawing with the given name exists in the database.

    Args:
        drawing_name: The name of the drawing to check.

    Returns:
        True if the drawing exists, False otherwise.
    """
    cursor.execute("SELECT 1 FROM drawings WHERE name = %s", (drawing_name,))
    return cursor.fetchone() is not None

# Function to check if an entry exists
def entry_exists(drawing_name, entrant_number):
    """
    Checks if an entry with the given entrant number exists in the specified drawing.

    Args:
        drawing_name: The name of the drawing.
        entrant_number: The entrant number to check.

    Returns:
        True if the entry exists, False otherwise.
    """
    cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
    drawing_id = cursor.fetchone()
    if drawing_id:
        cursor.execute("SELECT 1 FROM entries WHERE drawing_id = %s AND entrant_number = %s", (drawing_id[0], entrant_number))
        return cursor.fetchone() is not None
    return False

# Function to get a drawing's ID
def get_drawing_id(drawing_name):
    """
    Gets the ID of the drawing with the given name.

    Args:
        drawing_name: The name of the drawing.

    Returns:
        The ID of the drawing, or None if it doesn't exist.
    """
    cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
    result = cursor.fetchone()
    return result[0] if result else None

# Function to get an entry's ID
def get_entry_id(drawing_name, entrant_number):
    """
    Gets the ID of the entry with the given entrant number in the specified drawing.

    Args:
        drawing_name: The name of the drawing.
        entrant_number: The entrant number.

    Returns:
        The ID of the entry, or None if it doesn't exist.
    """
    drawing_id = get_drawing_id(drawing_name)
    if drawing_id:
        cursor.execute("SELECT entry_id FROM entries WHERE drawing_id = %s AND entrant_number = %s", (drawing_id, entrant_number))
        result = cursor.fetchone()
        return result[0] if result else None
    return None

# Function to add an entry to a drawing
def add_entry(drawing_name, entrant_name, entrant_number, user_id):
    """
    Adds an entry to the specified drawing.

    Args:
        drawing_name: The name of the drawing.
        entrant_name: The name of the entrant.
        entrant_number: The entrant number.
        user_id: The Discord ID of the user.

    Returns:
        True if the entry was added successfully, False otherwise.
    """
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO entries (entrant_name, entrant_number, drawing_id) VALUES (%s, %s, %s) RETURNING entry_id", (entrant_name, entrant_number, drawing_id))
        entry_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO entry_users (entry_id, user_id) VALUES (%s, %s)", (entry_id, user_id))
        mydb.commit()
        return True
    except psycopg2.errors.UniqueViolation:
        mydb.rollback()
        return False

# Function to eliminate an entry from a drawing
def eliminate_entry(drawing_name, entrant_number, eliminated_by):
    """
    Eliminates an entry from the specified drawing.

    Args:
        drawing_name: The name of the drawing.
        entrant_number: The entrant number to eliminate.
        eliminated_by: The name of the user who eliminated the entry.

    Returns:
        True if the entry was eliminated successfully, False otherwise.
    """
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()[0]
        cursor.execute("UPDATE entries SET status = 'eliminated', eliminated_by = %s WHERE drawing_id = %s AND entrant_number = %s", (eliminated_by, drawing_id, entrant_number))
        mydb.commit()
        return True
    except Exception as e:
        mydb.rollback()
        print(e)
        return False

# Function to draw a winner for a drawing
def draw_winner(drawing_name):
    """
    Draws a winner for the specified drawing.

    Args:
        drawing_name: The name of the drawing.

    Returns:
        The ID of the winner, or None if no eligible entries were found.
    """
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()[0]
        cursor.execute("SELECT entry_id FROM entries WHERE drawing_id = %s AND status = 'pending' ORDER BY RANDOM() LIMIT 1", (drawing_id,))
        winner_id = cursor.fetchone()
        if winner_id:
            winner_id = winner_id[0]
            cursor.execute("INSERT INTO results (drawing_id, winner_id) VALUES (%s, %s)", (drawing_id, winner_id))
            mydb.commit()
            return winner_id
        else:
            return None
    except Exception as e:
        mydb.rollback()
        print(e)
        return None

# Function to get the winner of a drawing
def get_winner(drawing_name):
    """
    Gets the winner of the specified drawing.

    Args:
        drawing_name: The name of the drawing.

    Returns:
        The ID of the winner, or None if no winner was found.
    """
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()[0]
        cursor.execute("SELECT winner_id FROM results WHERE drawing_id = %s", (drawing_id,))
        winner_id = cursor.fetchone()
        if winner_id:
            return winner_id[0]
        else:
            return None
    except Exception as e:
        print(e)
        return None

# Function to archive a drawing
def archive_drawing(drawing_name):
    """
    Archives the specified drawing.

    Args:
        drawing_name: The name of the drawing to archive.

    Returns:
        True if the drawing was archived successfully, False otherwise.
    """
    try:
        # Get the drawing ID
        cursor.execute("SELECT drawing_id, status FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id, status = cursor.fetchone()

        # Insert the drawing into archived_drawings
        cursor.execute("INSERT INTO archived_drawings (drawing_id, name, status) VALUES (%s, %s, %s)", (drawing_id, drawing_name, status))

        # Copy entries to archived_entries
        cursor.execute("INSERT INTO archived_entries (entry_id, entrant_number, entrant_name, drawing_id, eliminated_by, status) SELECT entry_id, entrant_number, entrant_name, drawing_id, eliminated_by, status FROM entries WHERE drawing_id = %s", (drawing_id,))

        # Copy entry_users to archived_entry_users
        cursor.execute("INSERT INTO archived_entry_users (entry_id, user_id) SELECT entry_id, user_id FROM entry_users WHERE entry_id IN (SELECT entry_id FROM entries WHERE drawing_id = %s)", (drawing_id,))

        # Delete entries from entries table
        cursor.execute("DELETE FROM entries WHERE drawing_id = %s", (drawing_id,))

        # Delete entries from entry_users table
        cursor.execute("DELETE FROM entry_users WHERE entry_id IN (SELECT entry_id FROM entries WHERE drawing_id = %s)", (drawing_id,))

        # Delete the drawing from drawings table
        cursor.execute("DELETE FROM drawings WHERE drawing_id = %s", (drawing_id,))

        mydb.commit()
        return True
    except Exception as e:
        mydb.rollback()
        print(e)
        return False

# Command to set the allowed channel (Admin only)
@bot.command(name="set_channel")
@commands.has_permissions(administrator=True)
async def set_channel(ctx, channel: discord.TextChannel):
    """
    Sets the channel where the bot commands can be used.

    Args:
        ctx: The command context.
        channel: The channel to restrict the bot commands to.
    """
    global allowed_channel_id
    allowed_channel_id = channel.id
    await ctx.send(f"Bot commands are now restricted to channel: {channel.mention}")

# Command to set the admin role (Admin only)
@bot.command(name="set_admin_role")
@commands.has_permissions(administrator=True)
async def set_admin_role(ctx, role: discord.Role):
    """
    Sets the admin role for the bot.

    Args:
        ctx: The command context.
        role: The role to assign as the admin role.
    """
    global admin_role_id
    admin_role_id = role.id
    await ctx.send(f"Admin commands are now restricted to users with the role: {role.mention}")

# Check if the command is being used in the allowed channel
def check_channel(ctx):
    """
    Checks if the command is being used in the allowed channel.

    Args:
        ctx: The command context.

    Returns:
        True if the command is allowed in the channel, False otherwise.
    """
    if allowed_channel_id is None or ctx.channel.id == allowed_channel_id:
        return True
    else:
        return False

# Check if the user has the admin role
def check_admin_role(ctx):
    """
    Checks if the user has the admin role.

    Args:
        ctx: The command context.

    Returns:
        True if the user has the admin role, False otherwise.
    """
    if admin_role_id is None or admin_role_id in [role.id for role in ctx.author.roles]:
        return True
    else:
        return False

# Command to create a new drawing (Admin only)
@bot.command(name="create_drawing")
@commands.has_permissions(administrator=True)
@commands.check(check_channel)
async def create_drawing(ctx, drawing_name, time_limit_hours=None):
    """
    Creates a new drawing.

    Args:
        ctx: The command context.
        drawing_name: The name of the drawing.
        time_limit_hours: (Optional) The time limit for the drawing in hours.
    """
    try:
        cursor.execute("INSERT INTO drawings (name, time_limit_hours) VALUES (%s, %s)", (drawing_name, time_limit_hours))
        mydb.commit()
        await ctx.send(f"Drawing '{drawing_name}' created successfully.")
    except psycopg2.errors.UniqueViolation:
        await ctx.send(f"Drawing '{drawing_name}' already exists.")
    except Exception as e:
        mydb.rollback()
        print(f"Error creating drawing: {e}")
        await ctx.send(f"Failed to create drawing '{drawing_name}'.")

# Command to open a drawing (Admin only)
@bot.command(name="open_drawing")
@commands.has_permissions(administrator=True)
@commands.check(check_channel)
async def open_drawing(ctx, drawing_name):
    """
    Opens an existing drawing for entries.

    Args:
        ctx: The command context.
        drawing_name: The name of the drawing.
    """
    if not drawing_exists(drawing_name):
        await ctx.send(f"Drawing '{drawing_name}' does not exist.")
        return

    cursor.execute("UPDATE drawings SET status = 'open' WHERE name = %s", (drawing_name,))
    mydb.commit()
    await ctx.send(f"Drawing '{drawing_name}' opened successfully.")

# Command to close a drawing (Admin only)
@bot.command(name="close_drawing")
@commands.has_permissions(administrator=True)
@commands.check(check_channel)
async def close_drawing(ctx, drawing_name):
    """
    Closes an existing drawing, preventing new entries.

    Args:
        ctx: The command context.
        drawing_name: The name of the drawing.
    """
    if not drawing_exists(drawing_name):
        await ctx.send(f"Drawing '{drawing_name}' does not exist.")
        return

    cursor.execute("UPDATE drawings SET status = 'closed' WHERE name = %s", (drawing_name,))
    mydb.commit()
    await ctx.send(f"Drawing '{drawing_name}' closed successfully.")

# Command to add an entry to a drawing (Admin only)
@bot.command(name="add_entry")
@commands.has_permissions(administrator=True)
@commands.check(check_channel)
async def add_entry_command(ctx, drawing_name, *, users):
    """
    Adds entries to the specified drawing for the mentioned users.

    Args:
        ctx: The command context.
        drawing_name: The name of the drawing.
        users: A space-separated list of users to add to the drawing.
    """
    try:
        # Check if the drawing exists
        if not drawing_exists(drawing_name):
            await ctx.send(f"Drawing '{drawing_name}' does not exist.")
            return

        # Check if the drawing is open
        cursor.execute("SELECT status FROM drawings WHERE name = %s", (drawing_name,))
        status = cursor.fetchone()[0]
        if status == 'closed':
            await ctx.send(f"Drawing '{drawing_name}' is closed.")
            return

        # Get the user IDs from the mentions
        user_ids = [user.id for user in ctx.message.mentions]

        # Calculate the next entrant number
        entrant_number = 1
        cursor.execute("SELECT MAX(entrant_number) FROM entries WHERE drawing_id = %s", (get_drawing_id(drawing_name),))
        result = cursor.fetchone()[0]
        if result:
            entrant_number = result + 1

        # Add an entry for each user
        for user_id in user_ids:
            entrant_name = bot.get_user(user_id).name
            if add_entry(drawing_name, entrant_name, entrant_number, user_id):
                await ctx.send(f"Entry added for {entrant_name} in '{drawing_name}'.")
                try:
                    # Notify the user that they have been added to the drawing
                    user = bot.get_user(user_id)
                    await user.send(f"You have been added to the drawing '{drawing_name}'.")
                except Exception as e:
                    print(f"Error sending DM to user: {e}")
            else:
                await ctx.send(f"Failed to add entry for {entrant_name} in '{drawing_name}' (already exists).")
    except Exception as e:
        print(f"Error adding entry: {e}")
        await ctx.send(f"An error occurred while adding the entry.")

# Command to view entries of a drawing (Anyone can use this)
@bot.command(name="view_entries")
@commands.check(check_channel)
async def view_entries(ctx, drawing_name):
    """
    Displays the list of entries for the specified drawing.

    Args:
        ctx: The command context.
        drawing_name: The name of the drawing.
    """
    try:
        if not drawing_exists(drawing_name):
            await ctx.send(f"Drawing '{drawing_name}' does not exist.")
            return

        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()[0]
        cursor.execute("SELECT entrant_number, entrant_name, status, eliminated_by FROM entries WHERE drawing_id = %s", (drawing_id,))
        entries = cursor.fetchall()

        if entries:
            headers = ["Entrant Number", "Entrant Name", "Status", "Eliminated By"]
            table = tabulate(entries, headers=headers, tablefmt="fancy_grid")
            await ctx.send(f"Entries for '{drawing_name}':\n```\n{table}\n```")
        else:
            await ctx.send(f"No entries found for '{drawing_name}'.")
    except Exception as e:
        print(f"Error viewing entries: {e}")
        await ctx.send(f"An error occurred while viewing the entries.")

# Command to eliminate an entry from a drawing (Admin only)
@bot.command(name="eliminate_entry")
@commands.has_permissions(administrator=True)
@commands.check(check_channel)
async def eliminate_entry_command(ctx, drawing_name, entrant_number):
    """
    Eliminates an entry from the specified drawing.

    Args:
        ctx: The command context.
        drawing_name: The name of the drawing.
        entrant_number: The entrant number to eliminate.
    """
    try:
        if not drawing_exists(drawing_name):
            await ctx.send(f"Drawing '{drawing_name}' does not exist.")
            return

        if not entry_exists(drawing_name, entrant_number):
            await ctx.send(f"Entry {entrant_number} does not exist in '{drawing_name}'.")
            return

        eliminated_by = ctx.author.name
        if eliminate_entry(drawing_name, entrant_number, eliminated_by):
            await ctx.send(f"Entry {entrant_number} eliminated from '{drawing_name}'.")
        else:
            await ctx.send(f"Failed to eliminate entry {entrant_number} from '{drawing_name}'.")
    except Exception as e:
        print(f"Error eliminating entry: {e}")
        await ctx.send(f"An error occurred while eliminating the entry.")

# Command to draw a winner for a drawing (Admin only)
@bot.command(name="draw_winner")
@commands.has_permissions(administrator=True)
@commands.check(check_channel)
async def draw_winner_command(ctx, drawing_name):
    """
    Draws a winner for the specified drawing.

    Args:
        ctx: The command context.
        drawing_name: The name of the drawing.
    """
    try:
        if not drawing_exists(drawing_name):
            await ctx.send(f"Drawing '{drawing_name}' does not exist.")
            return

        winner_id = draw_winner(drawing_name)
        if winner_id:
            winner_name = bot.get_user(winner_id).name
            await ctx.send(f"The winner of '{drawing_name}' is {winner_name}!")
            try:
                winner = bot.get_user(winner_id)
                await winner.send(f"Congratulations! You have won the drawing '{drawing_name}'.")
            except Exception as e:
                print(f"Error sending DM to user: {e}")
        else:
            await ctx.send(f"No eligible entries found for '{drawing_name}'.")
    except Exception as e:
        print(f"Error drawing winner: {e}")
        await ctx.send(f"An error occurred while drawing the winner.")

# Command to get the winner of a drawing (Anyone can use this)
@bot.command(name="get_winner")
@commands.check(check_channel)
async def get_winner_command(ctx, drawing_name):
    """
    Gets the winner of the specified drawing.

    Args:
        ctx: The command context.
        drawing_name: The name of the drawing.
    """
    try:
        if not drawing_exists(drawing_name):
            await ctx.send(f"Drawing '{drawing_name}' does not exist.")
            return

        winner_id = get_winner(drawing_name)
        if winner_id:
            winner_name = bot.get_user(winner_id).name
            await ctx.send(f"The winner of '{drawing_name}' is {winner_name}!")
        else:
            await ctx.send(f"No winner found for '{drawing_name}'.")
    except Exception as e:
        print(f"Error getting winner: {e}")
        await ctx.send(f"An error occurred while getting the winner.")

# Command to archive a drawing (Admin only)
@bot.command(name="archive_drawing")
@commands.has_permissions(administrator=True)
@commands.check(check_channel)
async def archive_drawing_command(ctx, drawing_name):
    """
    Archives the specified drawing.

    Args:
        ctx: The command context.
        drawing_name: The name of the drawing to archive.
    """
    try:
        if not drawing_exists(drawing_name):
            await ctx.send(f"Drawing '{drawing_name}' does not exist.")
            return

        if archive_drawing(drawing_name):
            await ctx.send(f"Drawing '{drawing_name}' archived successfully.")
        else:
            await ctx.send(f"Failed to archive drawing '{drawing_name}'.")
    except Exception as e:
        print(f"Error archiving drawing: {e}")
        await ctx.send(f"An error occurred while archiving the drawing.")

# Run the bot with the token from the environment variables
bot.run(os.getenv('DISCORD_BOT_TOKEN'))