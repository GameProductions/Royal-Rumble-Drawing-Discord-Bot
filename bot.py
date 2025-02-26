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

#bot = commands.Bot(command_prefix='!') #commented out for testing and can be removed if everything works
intents = discord.Intents.all()

bot = commands.Bot(command_prefix='!',intents=intents)

# Admin role ID (initially None)
admin_role_id = None

# --- Discord bot commands ---

@bot.command()
@commands.has_permissions(administrator=True)
async def set_admin_role(ctx, role: discord.Role):
    """Sets the specified role as the admin role for the bot."""
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

@bot.command()
async def create_drawing(ctx, name: str):
    """Creates a new drawing."""
    try:
        cursor.execute("INSERT INTO drawings (name) VALUES (%s)", (name,))
        mydb.commit()
        await ctx.send(f"Drawing '{name}' created!")
    except psycopg2.errors.UniqueViolation:
        await ctx.send(f"A drawing with the name '{name}' already exists.")
    except Exception as e:
        await ctx.send(f"Error creating drawing: {e}")

@bot.command()
@has_admin_permissions()
async def create_test_drawing(ctx, name: str):
    """Creates a test drawing that does not save results."""
    try:
        cursor.execute("INSERT INTO drawings (name, status) VALUES (%s, 'open')", (f"test_{name}",))
        mydb.commit()
        await ctx.send(f"Test drawing '{name}' created!")
    except psycopg2.errors.UniqueViolation:
        await ctx.send(f"A drawing with the name '{name}' already exists.")
    except Exception as e:
        await ctx.send(f"Error creating test drawing: {e}")

@bot.command()
async def join_drawing(ctx, name: str):
    """Joins a drawing."""
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
        taken_numbers = [row[0] for row in cursor.fetchall()]
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

@bot.command()
async def my_entries(ctx):
    """Displays the user's entries."""
    cursor.execute("SELECT e.entrant_number, e.entrant_name, e.status, e.eliminated_by, d.name FROM entries e JOIN drawings d ON e.drawing_id = d.drawing_id WHERE e.user_id = %s", (ctx.author.id,))
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

@bot.command()
@commands.has_permissions(administrator=True)
async def drawing_entries(ctx, name: str, include_archived: str = "no"):
    """Displays the entries for a specific drawing in a table format.

    include_archived: Whether to include archived entries. Options: 'yes', 'no' (default: 'no')
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

    except Exception as e:
        await ctx.send(f"Error displaying drawing entries: {e}")

@bot.command()
@commands.has_permissions(administrator=True)
async def start_drawing(ctx, name: str):
    """Starts a drawing."""
    try:
        cursor.execute("UPDATE drawings SET status = 'open' WHERE name = %s", (name,))
        mydb.commit()
        if cursor.rowcount > 0:
            await ctx.send(f"Drawing '{name}' started!")
        else:
            await ctx.send(f"Drawing '{name}' not found.")
    except Exception as e:
        await ctx.send(f"Error starting drawing: {e}")

@bot.command()
@commands.has_permissions(administrator=True)
async def stop_drawing(ctx, name: str):
    """Stops a drawing."""
    try:
        cursor.execute("UPDATE drawings SET status = 'closed' WHERE name = %s", (name,))
        mydb.commit()
        if cursor.rowcount > 0:
            await ctx.send(f"Drawing '{name}' stopped!")
        else:
            await ctx.send(f"Drawing '{name}' not found.")
    except Exception as e:
        await ctx.send(f"Error stopping drawing: {e}")

@bot.command()
async def drawing_status(ctx, name: str, include_archived: str = "no"):
    """Displays the status of a drawing.

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

    except Exception as e:
        await ctx.send(f"Error getting drawing status: {e}")

@bot.command()
@commands.has_permissions(administrator=True)
async def change_entrant(ctx, drawing_name: str, old_entrant_number: int, new_entrant_number: int):
    """Changes the entrant number for a drawing entry."""
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

    except Exception as e:
        await ctx.send(f"Error changing entrant number: {e}")

@bot.command()
@has_admin_permissions()
async def add_entry(ctx, drawing_name: str, users: commands.Greedy[discord.Member], entrant_number: int = None, entrant_name: str = None):
    """Adds an entry with multiple users in a drawing."""
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if not drawing_id:
            await ctx.send(f"Drawing '{drawing_name}' not found.")
            return

        # Check if all available entries have been assigned
        cursor.execute("SELECT COUNT(*) FROM entries WHERE drawing_id = %s", (drawing_id[0],))
        num_entries = cursor.fetchone()[0]
        if num_entries >= 30:  # Assuming 30 is the maximum number of entries
            await ctx.send(f"All available entries for drawing '{drawing_name}' have been assigned.")
            return

        if entrant_number is None:
            cursor.execute("SELECT MAX(entrant_number) FROM entries WHERE drawing_id = %s", (drawing_id[0],))
            max_entrant = cursor.fetchone()[0]
            entrant_number = max_entrant + 1 if max_entrant else 1

        if entrant_name is None:
            entrant_name = f"Entrant {entrant_number}"

        try:
            cursor.execute("INSERT INTO entries (entrant_number, entrant_name, drawing_id) VALUES (%s, %s, %s)",
                           (entrant_number, entrant_name, drawing_id[0]))
            entry_id = cursor.lastrowid

            # Check for duplicate users before adding them to entry_users
            existing_user_ids = []
            cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = %s", (entry_id,))
            for row in cursor.fetchall():
                existing_user_ids.append(row[0])

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

@bot.command()
@has_admin_permissions()
async def remove_entry(ctx, drawing_name: str, user: discord.Member):
    """Removes an entry for a user in a drawing."""
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if not drawing_id:
            await ctx.send(f"Drawing '{drawing_name}' not found.")
            return

        try:
            cursor.execute("SELECT entry_id FROM entries WHERE user_id = %s AND drawing_id = %s", (user.id, drawing_id[0]))
            entry_id = cursor.fetchone()
            if not entry_id:
                await ctx.send(f"{user.mention} does not have an entry in this drawing.")
                return

            cursor.execute("DELETE FROM entry_users WHERE user_id = %s AND entry_id = %s", (user.id, entry_id[0]))
            cursor.execute("DELETE FROM entries WHERE entry_id = %s", (entry_id[0],))
            mydb.commit()
            await ctx.send(f"Removed entry for {user.mention} in drawing '{drawing_name}'.")
        except Exception as e:
            await ctx.send(f"Error removing entry: {e}")

    except Exception as e:
        await ctx.send(f"Error removing entry: {e}")

@bot.command()
@has_admin_permissions()
async def edit_entry(ctx, drawing_name: str, entrant_number: int, new_entrant_number: int = None, new_entrant_name: str = None, eliminated_by: str = None, status: str = None):
    """Edits an entry in a drawing."""
    try:
        cursor.execute("SELECT drawing_id FROM drawings WHERE name = %s", (drawing_name,))
        drawing_id = cursor.fetchone()
        if not drawing_id:
            await ctx.send(f"Drawing '{drawing_name}' not found.")
            return

        try:
            set_clause = []
            if new_entrant_number is not None:
                set_clause.append(f"entrant_number = {new_entrant_number}")
            if new_entrant_name is not None:
                set_clause.append(f"entrant_name = '{new_entrant_name}'")
            if eliminated_by is not None:
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
            cursor.execute(sql, (drawing_id[0], entrant_number))
            mydb.commit()

            if cursor.rowcount > 0:
                await ctx.send(f"Updated entry with entrant number {entrant_number} in drawing '{drawing_name}'.")

                if new_entrant_name is not None:
                    cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = (SELECT entry_id FROM entries WHERE entrant_number = ? AND drawing_id = ?)", (new_entrant_number, drawing_id[0]))
                    user_ids = [row[0] for row in cursor.fetchall()]
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

@bot.command()
@has_admin_permissions()
async def assign_winner(ctx, drawing_name: str, winner_identifier: str):
    """Assigns the winner of a drawing by user mention or entrant number."""
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
            cursor.execute("SELECT entry_id FROM entries WHERE drawing_id = %s AND entrant_number = %s", (drawing_id[0], entrant_number))
            winner_id = cursor.fetchone()
            if not winner_id:
                await ctx.send(f"No entry found with entrant number {entrant_number} in drawing '{drawing_name}'.")
                return

            # Get the users associated with the winning entry
            cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = %s", (winner_id[0],))
            winner_user_ids = [row[0] for row in cursor.fetchall()]
            winner_str = ', '.join([bot.get_user(user_id).mention for user_id in winner_user_ids if bot.get_user(user_id)])
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

@bot.command()
async def drawing_results(ctx, drawing_name: str, include_archived: str = "no"):
    """Displays the results of a drawing.

    include_archived: Whether to include archived drawings. Options: 'yes', 'no' (default: 'no')
    """
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

        cursor.execute("SELECT winner_id FROM results WHERE drawing_id = %s", (drawing_id[0],))
        winner_id = cursor.fetchone()
        if winner_id:
            cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = %s", (winner_id[0],))
            winner_user_ids = [row[0] for row in cursor.fetchall()]
            winner_str = ', '.join([bot.get_user(user_id).mention for user_id in winner_user_ids if bot.get_user(user_id)])
            if not winner_str:
                winner_str = "Unknown User(s)"
            await ctx.send(f"The winner of drawing '{drawing_name}' was {winner_str}.")
        else:
            await ctx.send(f"No winner found for drawing '{drawing_name}'.")

    except Exception as e:
        await ctx.send(f"Error getting drawing results: {e}")

@bot.command()
@commands.has_permissions(administrator=True)
async def set_drawing_status(ctx, drawing_name: str, status: str):
    """Sets the status of a drawing."""
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

@bot.command()
async def drawings_by_status(ctx, status: str = None, include_archived: str = "no"):
    """Lists all drawings with the specified status.
    If no status is provided, lists all drawings with their statuses.

    include_archived: Whether to include archived drawings. Options: 'yes', 'no' (default: 'no')
    """
    try:
        if status:
            if status.lower() not in ('open', 'closed', 'pending', 'active', 'inactive', 'eliminated', 'winner'):
                await ctx.send("Invalid status value. Please use one of: 'open', 'closed', 'pending', 'active', 'inactive', 'eliminated', 'winner'.")
                return

            drawings = []
            cursor.execute("SELECT name FROM drawings WHERE status = %s", (status.lower(),))
            drawings.extend([row[0] for row in cursor.fetchall()])
            if include_archived.lower() == "yes":
                cursor.execute("SELECT name FROM archived_drawings WHERE status = %s", (status.lower(),))
                drawings.extend([row[0] for row in cursor.fetchall()])

            if drawings:
                await ctx.send(f"Drawings with status '{status.lower()}':\n" + "\n".join(drawings))
            else:
                await ctx.send(f"No drawings found with status '{status.lower()}'.")

        else:
            drawings = []
            cursor.execute("SELECT name, status FROM drawings")
            drawings.extend([(row[0], row[1]) for row in cursor.fetchall()])
            if include_archived.lower() == "yes":
                cursor.execute("SELECT name, status FROM archived_drawings")
                drawings.extend([(row[0], row[1]) for row in cursor.fetchall()])

            if drawings:
                message = "**Drawings by status:**\n"
                for name, status in drawings:
                    message += f"- {name}: {status}\n"
                await ctx.send(message)
            else:
                await ctx.send("No drawings found.")

    except Exception as e:
        await ctx.send(f"Error listing drawings by status: {e}")

@bot.command()
@has_admin_permissions()
async def delete_drawing(ctx, drawing_name: str):
    """Deletes a drawing after confirmation."""

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
        drawing_id = drawing[0]

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
            mydb.rollback()

    except Exception as e:
        await ctx.send(f"Error archiving drawing: {e}")

@bot.command()
@has_admin_permissions()
async def restore_drawing(ctx, drawing_name: str):
    """Restores an archived drawing."""
    try:
        # Check if the drawing exists in the archive
        cursor.execute("SELECT drawing_id FROM archived_drawings WHERE name = %s", (drawing_name,))
        drawing = cursor.fetchone()
        if not drawing:
            await ctx.send(f"Archived drawing '{drawing_name}' not found.")
            return
        drawing_id = drawing[0]

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

@bot.command()
async def drawing_winners(ctx, drawing_name: str = None, include_archived: str = "no"):
    """Displays the winners of a drawing.
    If no drawing name is provided, shows all historical winners.

    include_archived: Whether to include archived drawings. Options: 'yes', 'no' (default: 'no')
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

            cursor.execute("SELECT winner_id FROM results WHERE drawing_id = %s", (drawing_id[0],))
            winner_entry_ids = [row[0] for row in cursor.fetchall()]

            if winner_entry_ids:
                message = f"**Winners of drawing '{drawing_name}'**:\n"
                for entry_id in winner_entry_ids:
                    cursor.execute("SELECT entrant_number, entrant_name FROM entries WHERE entry_id = %s", (entry_id,))
                    entrant_number, entrant_name = cursor.fetchone()
                    cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = %s", (entry_id,))
                    user_ids = [row[0] for row in cursor.fetchall()]
                    user_mentions = [bot.get_user(user_id).mention for user_id in user_ids if bot.get_user(user_id)]

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
            drawing_ids.extend([row[0] for row in cursor.fetchall()])
            if include_archived.lower() == "yes":
                cursor.execute("SELECT DISTINCT drawing_id FROM archived_drawings")
                drawing_ids.extend([row[0] for row in cursor.fetchall()])

            if drawing_ids:
                message = "**Historical drawing winners**:\n"
                for drawing_id in drawing_ids:
                    # Check both drawings and archived_drawings tables
                    cursor.execute("SELECT name FROM drawings WHERE drawing_id = %s", (drawing_id,))
                    drawing_name = cursor.fetchone()
                    if not drawing_name:
                        cursor.execute("SELECT name FROM archived_drawings WHERE drawing_id = %s", (drawing_id,))
                        drawing_name = cursor.fetchone()
                    drawing_name = drawing_name[0] if drawing_name else "Unknown Drawing"

                    cursor.execute("SELECT winner_id FROM results WHERE drawing_id = %s", (drawing_id,))
                    winner_entry_ids = [row[0] for row in cursor.fetchall()]

                    message += f"\n**{drawing_name}:**\n"
                    for entry_id in winner_entry_ids:
                        cursor.execute("SELECT entrant_number, entrant_name FROM entries WHERE entry_id = %s", (entry_id,))
                        entrant_number, entrant_name = cursor.fetchone()
                        cursor.execute("SELECT user_id FROM entry_users WHERE entry_id = %s", (entry_id,))
                        user_ids = [row[0] for row in cursor.fetchall()]
                        user_mentions = [bot.get_user(user_id).mention for user_id in user_ids if bot.get_user(user_id)]

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
    cursor.execute("SELECT drawing_id, name FROM drawings WHERE status = 'open'")
    open_drawings = cursor.fetchall()

    for drawing_id, name in open_drawings:
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

@check_drawings.before_loop
async def before_check_drawings():
    """Ensures the bot is ready before starting the task."""
    await bot.wait_until_ready()

async def main():  # Define an async main function
    @bot.event
    async def on_ready():
        print(f'{bot.user} has connected to Discord!')
        check_drawings.start()  # Start the task inside on_ready

    await bot.start(os.getenv('DISCORD_BOT_TOKEN'))  # Use bot.start instead of bot.run

if __name__ == '__main__':
    asyncio.run(main())  # Run the main function with asyncio.run

# Run the bot
bot.run(os.getenv('DISCORD_BOT_TOKEN'))