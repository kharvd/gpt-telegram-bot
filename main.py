import logging
import os
from typing import Optional
import openai
import json
import asyncio
import boto3
from telegram import Bot, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
    BasePersistence,
    DictPersistence,
    PersistenceInput,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)


async def complete_chat(messages, config):
    response_iter = await openai.ChatCompletion.acreate(
        messages=messages,
        stream=True,
        model=config.get("model", "gpt-3.5-turbo"),
        temperature=float(config.get("temperature", 0.7)),
        top_p=float(config.get("top_p", 1.0)),
    )

    async for response in response_iter:
        next_choice = response["choices"][0]
        if next_choice["finish_reason"] is None and "content" in next_choice["delta"]:
            yield next_choice["delta"]["content"]


async def respond(update: Update, context: ContextTypes.DEFAULT_TYPE, messages, config):
    if "openai_api_key" in context.user_data:
        openai.api_key = context.user_data["openai_api_key"]
    else:
        await update.message.reply_text(
            "Please set the OpenAI API key using the `/token OPENAI_API_KEY` command.",
            parse_mode="Markdown",
        )
        raise Exception("OpenAI API key not set")

    completion_iter = complete_chat(messages, config)
    message_buffer = ""
    message = ""
    response = await update.message.reply_text("...")

    async def maybe_edit():
        nonlocal message, message_buffer
        prev_message = message.strip()
        message += message_buffer
        message_buffer = ""

        stripped_message = message.strip()
        if stripped_message != "" and stripped_message != prev_message:
            await response.edit_text(stripped_message)

    async for completion in completion_iter:
        message_buffer += completion
        if len(message_buffer) > 30:
            await maybe_edit()

    await maybe_edit()

    return message


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! How can I help you today?",
    )


async def chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    messages = context.user_data.get("messages", [])
    messages.append({"role": "user", "content": update.message.text})
    context.user_data["messages"] = messages

    response = await respond(
        update, context, messages, context.user_data.get("overrides", {})
    )
    messages.append({"role": "assistant", "content": response})
    context.user_data["messages"] = messages


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["messages"] = []
    await update.message.reply_text("🗑️ Chat cleared.")


async def rerun_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    messages = context.user_data.get("messages", [])

    while len(messages) > 0 and messages[-1]["role"] == "bot":
        messages.pop()

    if len(messages) == 0:
        await update.message.reply_text("Nothing to rerun.")
        return

    response = await respond(
        update, context, messages, context.user_data.get("overrides", {})
    )
    messages.append({"role": "assistant", "content": response})
    context.user_data["messages"] = messages


async def set_override(
    parameter_name: str, update: Update, context: ContextTypes.DEFAULT_TYPE
):
    overrides = context.user_data.get("overrides", {})
    overrides[parameter_name] = context.args[0]
    context.user_data["overrides"] = overrides
    await update.message.reply_text(
        f"Set {parameter_name} to {context.args[0]}. Current overrides: {overrides}"
    )


async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await set_override("model", update, context)


async def temperature_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await set_override("temperature", update, context)


async def top_p_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await set_override("top_p", update, context)


async def params_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    overrides = context.user_data.get("overrides", {})
    await update.message.reply_text(f"Current params: {overrides}")


async def token_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["openai_api_key"] = context.args[0]
    await update.message.reply_text(f"Set token to {context.args[0]}.")


async def post_init(app: Application):
    await app.bot.set_my_commands(
        [
            ("start", "Start the conversation"),
            ("token", "Set OpenAI API token"),
            ("clear", "Clear the conversation"),
            ("rerun", "Rerun the conversation"),
            ("model", "Set the model"),
            ("temp", "Set the temperature"),
            ("top_p", "Set the top_p"),
            ("params", "Show the current parameters"),
        ]
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a telegram message to notify the developer."""
    # Log the error before we do anything else, so we can see it even if something breaks.
    logging.error(msg="Exception while handling an update:", exc_info=context.error)


def init_application(persistence: Optional[BasePersistence] = None) -> Application:
    bot = Bot(token=os.environ["TELEGRAM_API_TOKEN"])
    application_builder = ApplicationBuilder()
    application_builder.bot(bot).post_init(post_init)

    if persistence:
        application_builder.persistence(persistence)

    application = application_builder.build()

    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, chat_message)
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("token", token_command))
    application.add_handler(CommandHandler("clear", clear_command))
    application.add_handler(CommandHandler("rerun", rerun_command))
    application.add_handler(CommandHandler("model", model_command))
    application.add_handler(CommandHandler("temp", temperature_command))
    application.add_handler(CommandHandler("top_p", top_p_command))
    application.add_handler(CommandHandler("params", params_command))

    application.add_error_handler(error_handler)
    return application


class DynamoDBPersistence(BasePersistence):
    def __init__(self):
        super().__init__(
            PersistenceInput(
                bot_data=False, chat_data=False, user_data=True, callback_data=False
            )
        )
        dynamodb = boto3.resource("dynamodb")
        self.table = dynamodb.Table("gptcli")

    async def update_user_data(self, user_id: int, data) -> None:
        logging.info(f"update_user_data: {user_id}, {data}")
        self.table.put_item(
            Item={
                "id": str(user_id),
                "user_data": data,
            }
        )

    async def drop_user_data(self, user_id: int) -> None:
        logging.info(f"drop_user_data: {user_id}")
        self.table.delete_item(Key={"id": str(user_id)})

    async def refresh_user_data(self, user_id: int, user_data) -> None:
        response = self.table.get_item(Key={"id": str(user_id)})
        data = response.get("Item", {}).get("user_data", {})
        logging.info(f"refresh_user_data: {user_id}, {data}")
        for key, value in data.items():
            user_data[key] = value

    async def get_user_data(self):
        return {}

    async def get_chat_data(self):
        pass

    async def get_bot_data(self):
        pass

    async def get_callback_data(self):
        pass

    async def get_conversations(self, name: str):
        pass

    async def update_conversation(self, name, key, new_state) -> None:
        pass

    async def update_chat_data(self, chat_id: int, data) -> None:
        pass

    async def update_bot_data(self, data) -> None:
        pass

    async def update_callback_data(self, data) -> None:
        pass

    async def drop_chat_data(self, chat_id: int) -> None:
        pass

    async def refresh_chat_data(self, chat_id: int, chat_data) -> None:
        pass

    async def refresh_bot_data(self, bot_data) -> None:
        pass

    async def flush(self) -> None:
        pass


async def handler(event, context):
    logging.info(f"event: {event}")
    telegram_security_token = event.get("headers", {}).get(
        "x-telegram-bot-api-secret-token"
    )
    expected_token = os.environ.get("TELEGRAM_BOT_API_SECRET_TOKEN")
    if expected_token is None:
        logging.error("TELEGRAM_BOT_API_SECRET_TOKEN not set")
        return {"statusCode": 500}

    if telegram_security_token != expected_token:
        logging.error("Invalid security token")
        return {"statusCode": 401}

    application = init_application(DynamoDBPersistence())
    await application.initialize()
    await application.post_init(application)

    update = Update.de_json(json.loads(event["body"]), application.bot)
    await application.process_update(update)
    await application.shutdown()

    return {"statusCode": 200}


def lambda_handler(event, context):
    return asyncio.run(handler(event, context))


def main():
    application = init_application(
        persistence=DictPersistence(
            store_data=PersistenceInput(
                bot_data=False, chat_data=False, user_data=True, callback_data=False
            )
        )
    )
    application.run_polling()


if __name__ == "__main__":
    main()
