# Royal-Rumble-Drawing-Discord-Bot

This bot manages drawings and raffles within a Discord server.

## Features

* Create, open, and close drawings
* Add and eliminate entries
* Draw winners
* Archive past drawings
* Set time limits for drawings
* Admin-only commands for managing drawings
* User notifications for entries and wins
* Slash commands and text commands

## Requirements

* Docker
* Docker Compose
* A Discord bot token
* A PostgreSQL database

## Installation

1. Clone this repository: `git clone https://github.com/GameProductions/Royal-Rumble-Drawing-Discord-Bot.git`
2. Create a `.env` file with the following variables:

DB_HOST=db
DB_USER=your_db_user
DB_PASSWORD=your_db_password
DB_NAME=your_db_name
DISCORD_BOT_TOKEN=your_bot_token

Note: You can also rename the existing `rename_to_.env` file to `.env` and update your variables in that file accordingly.

3. Build and run the Docker containers: `docker-compose up -d --build`

## Usage

* Use slash commands or text commands to interact with the bot.
* See the `commands.md` file for a list of available commands and their usage.

## Contributing

Contributions are welcome! Please feel free to submit pull requests or open issues for any bugs or feature requests.

## License

This project is licensed under the MIT License - see the `LICENSE` file for details.