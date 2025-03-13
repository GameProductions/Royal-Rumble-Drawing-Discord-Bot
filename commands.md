# Discord Drawing Bot Commands

This document lists the available commands for the Discord Drawing Bot.

## Slash Commands (Available in a future update)

* `/add_entry <drawing_name> <users>`: Adds entries to the specified drawing for the mentioned users (Admin only).
    * `<drawing_name>`: The name of the drawing.
    * `<users>`: A comma-separated list of users to add to the drawing.
* `/archive_drawing <drawing_name>`: Archives the specified drawing (Admin only).
    * `<drawing_name>`: The name of the drawing to archive.
* `/available_commands`: Shows the commands you can use.
* `/close_drawing <name>`: Closes an existing drawing, preventing new entries (Admin only).
    * `<name>`: The name of the drawing.
* `/create_drawing <name>`: Creates a new drawing.
    * `<name>`: The name of the drawing.
* `/create_test_drawing <name>`: Creates a test drawing that does not save results (Admin only).
    * `<name>`: The name of the drawing.
* `/draw_winner <drawing_name>`: Randomly draws a winner from the remaining entries (Admin only).
    * `<drawing_name>`: The name of the drawing.
* `/drawing_entries <name> [include_archived]`: Displays the entries for a specific drawing (Admin only).
    * `<name>`: The name of the drawing.
    * `[include_archived]`: (Optional) Whether to include archived entries (yes/no). Defaults to "no".
* `/eliminate_entry <drawing_name> <entrant_number>`: Eliminates an entry from the specified drawing (Admin only).
    * `<drawing_name>`: The name of the drawing.
    * `<entrant_number>`: The entrant number to eliminate.
* `/join_drawing <name>`: Joins a drawing.
    * `<name>`: The name of the drawing to join.
* `/my_entries`: Displays your drawing entries.
* `/open_drawing <name>`: Opens an existing drawing for entries (Admin only).
    * `<name>`: The name of the drawing.
* `/set_admin_role <role>`: Sets the specified role as the admin role for the bot (Admin only).
    * `<role>`: The role to set as the admin role.
* `/view_entries <drawing_name>`: Displays the list of entries for the specified drawing.
    * `<drawing_name>`: The name of the drawing.


## Text Commands

* `!add_entry <drawing_name> <users>`: Adds entries to the specified drawing for the mentioned users (Admin only).
    * `<drawing_name>`: The name of the drawing.
    * `<users>`: A comma-separated list of users to add to the drawing.
* `!archive_drawing <drawing_name>`: Archives the specified drawing (Admin only).
    * `<drawing_name>`: The name of the drawing to archive.
* `!available_commands`: Shows the commands you can use.
* `!close_drawing <name>`: Closes an existing drawing, preventing new entries (Admin only).
    * `<name>`: The name of the drawing.
* `!create_drawing <name>`: Creates a new drawing (Admin only).
    * `<name>`: The name of the drawing.
* `!create_test_drawing <name>`: Creates a test drawing that does not save results (Admin only).
    * `<name>`: The name of the drawing.
* `!draw_winner <drawing_name>`: Randomly draws a winner from the remaining entries (Admin only).
    * `<drawing_name>`: The name of the drawing.
* `!drawing_entries <name> [include_archived]`: Displays the entries for a specific drawing (Admin only).
    * `<name>`: The name of the drawing.
    * `[include_archived]`: (Optional) Whether to include archived entries (yes/no). Defaults to "no".
* `!eliminate_entry <drawing_name> <entrant_number>`: Eliminates an entry from the specified drawing (Admin only).
    * `<drawing_name>`: The name of the drawing.
    * `<entrant_number>`: The entrant number to eliminate.
* `!join_drawing <name>`: Joins a drawing.
    * `<name>`: The name of the drawing to join.
* `!open_drawing <name>`: Opens an existing drawing for entries (Admin only).
    * `<name>`: The name of the drawing.
* `!set_admin_role <role>`: Sets the specified role as the admin role for the bot (Admin only).
    * `<role>`: The role to set as the admin role.
* `!test`: Test command.
* `!test_admin`: Test command for admin role.
* `!view_entries <drawing_name>`: Displays the list of entries for the specified drawing.
    * `<drawing_name>`: The name of the drawing.


## Notes

* All commands must be executed in a Discord server where the bot is present.
* Only users with administrator permissions or the specified admin role can execute commands that modify drawings or entries.
* The bot will send direct messages to users to notify them about being added to a drawing or winning a drawing.