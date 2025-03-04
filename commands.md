# Discord Drawing Bot Commands

This document lists the available commands for the Discord Drawing Bot.

## Slash Commands

* `/create_drawing <drawing_name> [time_limit_hours]`: Creates a new drawing.
    * `<drawing_name>`: The name of the drawing.
    * `[time_limit_hours]`: (Optional) The time limit for the drawing in hours.
* `/open_drawing <drawing_name>`: Opens an existing drawing for entries.
    * `<drawing_name>`: The name of the drawing.
* `/close_drawing <drawing_name>`: Closes an existing drawing, preventing new entries.
    * `<drawing_name>`: The name of the drawing.
* `/add_entry <drawing_name> <users>`: Adds an entry to a drawing.
    * `<drawing_name>`: The name of the drawing.
    * `<users>`: A comma-separated list of users to add to the entry.
* `/view_entries <drawing_name>`: Displays the list of entries for a drawing.
    * `<drawing_name>`: The name of the drawing.
* `/eliminate_entry <drawing_name> <entrant_number>`: Eliminates an entry from a drawing.
    * `<drawing_name>`: The name of the drawing.
    * `<entrant_number>`: The entrant number to eliminate.
* `/draw_winner <drawing_name>`: Randomly draws a winner from the remaining entries.
    * `<drawing_name>`: The name of the drawing.
* `/archive_drawing <drawing_name>`: Archives a drawing.
    * `<drawing_name>`: The name of the drawing.

## Text Commands

* `!create_drawing <drawing_name> [time_limit_hours]`: Creates a new drawing.
    * `<drawing_name>`: The name of the drawing.
    * `[time_limit_hours]`: (Optional) The time limit for the drawing in hours.
* `!open_drawing <drawing_name>`: Opens an existing drawing for entries.
    * `<drawing_name>`: The name of the drawing.
* `!close_drawing <drawing_name>`: Closes an existing drawing, preventing new entries.
    * `<drawing_name>`: The name of the drawing.
* `!add_entry <drawing_name> <users>`: Adds an entry to a drawing.
    * `<drawing_name>`: The name of the drawing.
    * `<users>`: A comma-separated list of users to add to the entry.
* `!view_entries <drawing_name>`: Displays the list of entries for a drawing.
    * `<drawing_name>`: The name of the drawing.
* `!eliminate_entry <drawing_name> <entrant_number>`: Eliminates an entry from a drawing.
    * `<drawing_name>`: The name of the drawing.
    * `<entrant_number>`: The entrant number to eliminate.
* `!draw_winner <drawing_name>`: Randomly draws a winner from the remaining entries.
    * `<drawing_name>`: The name of the drawing.
* `!archive_drawing <drawing_name>`: Archives a drawing.
    * `<drawing_name>`: The name of the drawing.

## Notes

* All commands must be executed in a Discord server where the bot is present.
* Only users with administrator permissions can execute commands that modify drawings or entries.
* The bot will send direct messages to users to notify them about being added to a drawing or winning a drawing.