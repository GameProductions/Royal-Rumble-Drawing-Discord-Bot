# Use a newer Python version
FROM python:3.12-slim

WORKDIR /app

# Upgrade pip to the latest version
RUN python -m pip install --upgrade pip

# Copy the requirements file
COPY requirements.txt .

# Install dependencies with trusted hosts
#RUN PIP_DISABLE_PIP_VERSION_CHECK=1 pip install -r requirements.txt --trusted-host pypi.org --trusted-host files.pythonhosted.org
RUN pip install --no-cache-dir -r requirements.txt
#RUN pip install -r requirements.txt --index-url https://pypi.org/simple

# Install Git
RUN apt-get update && apt-get install -y git

# Clone the GitHub repository (if applicable)
RUN git clone https://github.com/GameProductions/Royal-Rumble-Drawing-Discord-Bot.git

# Copy the rest of the application code
COPY . .

# Expose the port for the Flask app
EXPOSE 8000

# Define the command to run your bot and web app in the background
CMD ["sh", "-c", "python bot.py & gunicorn --bind 0.0.0.0:8000 app:app"]