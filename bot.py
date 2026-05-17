import os
import uuid
import shutil
import random
import genanki

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters,
)


# =====================================================
# BOT TOKEN
# =====================================================

BOT_TOKEN = "8674572158:AAGdPzN8vC8pqeY_QcfSeq9Ee2n0uRsbvKY"


# =====================================================
# USER SESSIONS
# =====================================================

# Structure:
# {
#   user_id: {
#       "state": "waiting_question",
#       "cards": [],
#       "current": {
#           "question": "",
#           "answer": "",
#           "image": None
#       }
#   }
# }

sessions = {}


# =====================================================
# HELPERS
# =====================================================


def get_user_session(user_id):
    if user_id not in sessions:
        sessions[user_id] = {
            "state": None,
            "cards": [],
            "current": {
                "question": "",
                "answer": "",
                "image": None,
            },
        }

    return sessions[user_id]



def reset_current_card(session):
    session["current"] = {
        "question": "",
        "answer": "",
        "image": None,
    }



def create_deck(cards, output_file, media_dir):
    """
    Generate .apkg file from cards.
    """

    model = genanki.Model(
        random.randint(1000000000, 9999999999),
        "Telegram Bot Model",
        fields=[
            {"name": "Question"},
            {"name": "Answer"},
        ],
        templates=[
            {
                "name": "Card 1",
                "qfmt": "{{Question}}",
                "afmt": "{{FrontSide}}<hr id='answer'>{{Answer}}",
            }
        ],
        css="""
        .card {
            font-family: arial;
            font-size: 22px;
            text-align: center;
            color: black;
            background-color: white;
        }
        img {
            max-width: 95%;
            height: auto;
            border-radius: 12px;
        }
        """,
    )

    deck = genanki.Deck(
        random.randint(1000000000, 9999999999),
        "Telegram Generated Deck",
    )

    media_files = []

    for card in cards:
        question = card["question"]
        answer = card["answer"]

        if card["image"]:
            image_filename = os.path.basename(card["image"])

            question += f"<br><img src='{image_filename}'>"

            media_files.append(card["image"])

        note = genanki.Note(
            model=model,
            fields=[question, answer],
        )

        deck.add_note(note)

    package = genanki.Package(deck)
    package.media_files = media_files
    package.write_to_file(output_file)


# =====================================================
# COMMANDS
# =====================================================


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📚 Telegram → Anki Bot\n\n"
        "Commands:\n"
        "/newdeck - Start making a deck\n"
        "/done - Generate .apkg\n"
        "/cancel - Cancel everything"
    )

    await update.message.reply_text(text)


async def new_deck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    sessions[user_id] = {
        "state": "waiting_question",
        "cards": [],
        "current": {
            "question": "",
            "answer": "",
            "image": None,
        },
    }

    await update.message.reply_text(
        "📝 Send the question for the first card."
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id in sessions:
        del sessions[user_id]

    await update.message.reply_text("❌ Session cancelled.")


async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in sessions:
        await update.message.reply_text("No active deck.")
        return

    session = sessions[user_id]

    if len(session["cards"]) == 0:
        await update.message.reply_text("No cards added.")
        return

    await update.message.reply_text("⚙️ Generating .apkg file...")

    os.makedirs("generated", exist_ok=True)

    deck_id = str(uuid.uuid4())

    output_file = f"generated/{deck_id}.apkg"

    create_deck(
        cards=session["cards"],
        output_file=output_file,
        media_dir="media",
    )

    await update.message.reply_document(
        document=open(output_file, "rb"),
        filename="telegram_deck.apkg",
        caption="✅ Deck generated successfully!",
    )

    # cleanup
    try:
        os.remove(output_file)
    except:
        pass

    if os.path.exists("media"):
        shutil.rmtree("media")

    del sessions[user_id]


# =====================================================
# TEXT HANDLER
# =====================================================


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in sessions:
        return

    session = get_user_session(user_id)

    state = session["state"]

    text = update.message.text

    # ---------------------------------
    # WAITING QUESTION
    # ---------------------------------

    if state == "waiting_question":
        session["current"]["question"] = text
        session["state"] = "waiting_answer"

        await update.message.reply_text(
            "✅ Question saved.\n\nNow send the answer."
        )

    # ---------------------------------
    # WAITING ANSWER
    # ---------------------------------

    elif state == "waiting_answer":
        session["current"]["answer"] = text
        session["state"] = "waiting_image"

        keyboard = [
            [InlineKeyboardButton("Skip Image", callback_data="skip_image")]
        ]

        await update.message.reply_text(
            "🖼 Send an image for this card or press Skip.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


# =====================================================
# IMAGE HANDLER
# =====================================================


async def image_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in sessions:
        return

    session = get_user_session(user_id)

    if session["state"] != "waiting_image":
        return

    photo = update.message.photo[-1]

    os.makedirs("media", exist_ok=True)

    file = await photo.get_file()

    filename = f"{uuid.uuid4()}.jpg"
    path = os.path.join("media", filename)

    await file.download_to_drive(path)

    session["current"]["image"] = path

    # Save card
    session["cards"].append(session["current"].copy())

    reset_current_card(session)

    session["state"] = "waiting_question"

    await update.message.reply_text(
        f"✅ Card saved!\n"
        f"Current cards: {len(session['cards'])}\n\n"
        f"Send next question or use /done"
    )


# =====================================================
# BUTTON HANDLER
# =====================================================


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    if user_id not in sessions:
        return

    session = get_user_session(user_id)

    if query.data == "skip_image":
        session["cards"].append(session["current"].copy())

        reset_current_card(session)

        session["state"] = "waiting_question"

        await query.message.reply_text(
            f"✅ Card saved!\n"
            f"Current cards: {len(session['cards'])}\n\n"
            f"Send next question or use /done"
        )


# =====================================================
# MAIN
# =====================================================


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("newdeck", new_deck))
    app.add_handler(CommandHandler("done", done))
    app.add_handler(CommandHandler("cancel", cancel))

    app.add_handler(CallbackQueryHandler(button_handler))

    app.add_handler(
        MessageHandler(filters.PHOTO, image_handler)
    )

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )

    print("Bot is running...")

    app.run_polling()


if __name__ == "__main__":
    main()
