# gpt-telegram-bot

A Telegram bot for ChatGPT.

## Deployment

This bot can either run locally (with polling) or on AWS Lambda with DynamoDB for persistence (as a webhook).

### Local

```bash
$ pip install -r requirements.txt
$ TELEGRAM_API_TOKEN=your_bot_token python main.py
```

### AWS Lambda

The bot uses a DynamoDB table to store the chat history. Make sure the table exists and its name is set in an environment variable called `DYNAMODB_TABLE`.

Expected environment variables:

- `TELEGRAM_API_TOKEN`: Telegram bot api token
- `TELEGRAM_BOT_API_SECRET_TOKEN` (only when run as webhook in Lambda): Telegram bot api secret token (see [Telegram docs](https://core.telegram.org/bots/api#setwebhook))
- `DYNAMODB_TABLE`: DynamoDB table name

Point your lambda to `main.lambda_handler`.

See also [GitHub Actions](.github/workflows/main.yml) for an example.

## Usage

After starting the bot, set the OpenAI API key with the `/token <api_key>` command.
