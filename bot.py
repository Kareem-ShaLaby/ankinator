import os
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