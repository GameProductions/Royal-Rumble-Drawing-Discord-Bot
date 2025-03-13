# Use a newer Python version
FROM python:3.12-slim

# Define build arguments
ARG DB_USER
ARG DB_PASSWORD
ARG DB_NAME

# Create a directory for the application
RUN mkdir /app

# Create a directory for the database
RUN mkdir /database

# Set the working directory in the container
WORKDIR /app

# Create the initdb.sql file using environment variables
RUN mkdir -p /docker-entrypoint-initdb.d/ && \
    echo "CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASSWORD}';" > /docker-entrypoint-initdb.d/initdb.sql && \
    echo "GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};" >> /docker-entrypoint-initdb.d/initdb.sql

# Copy the requirements file
COPY requirements.txt .

# Install build tools, postgresql development files
RUN apt-get update && apt-get install -y build-essential libpq-dev

# Install dependencies with trusted hosts
RUN pip install --no-cache-dir -r requirements.txt

# Install Git
RUN apt-get update && apt-get install -y git

# Clone the GitHub repository (if applicable)
RUN git clone https://github.com/GameProductions/Royal-Rumble-Drawing-Discord-Bot.git

# Copy the rest of the application code
COPY . .

# Expose the port for the Flask app
EXPOSE 8000

# Define the command to run your bot and web app in the background
CMD ["sh", "-c", "python bot.py & gunicorn --bind 0.0.0.0:8000 --log-level debug app:app"]