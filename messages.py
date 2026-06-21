"""Single source of truth for all user-facing strings (EN and RU)."""

_DEFAULT_LANG = "en"

_MESSAGES = {
    "en": {
        "welcome": (
            "Welcome! To connect your Todoist account, find your API token at "
            "Todoist Settings -> Integrations -> Developer, then send it to me here. "
            "Your token message will be deleted immediately after I process it for "
            "your security."
        ),
        "token_accepted": "Token accepted. Your message has been deleted. You're all set!",
        "token_invalid": "That token didn't work. Please check it and send it again.",
        "token_network_error": "Couldn't reach Todoist to verify your token. Please try again.",
        "token_deletion_failed": "I couldn't delete your token message. Please delete it manually.",
        "token_accidental": "I detected what looked like a Todoist token and deleted it for your safety.",
        "already_registered": "You're already connected to Todoist.",
        "unregistered": "You're not set up yet. Send /start to get started.",
        "rate_limit_session": "Session limit reached. Please try again in {retry_time}.",
        "rate_limit_week": "Weekly limit reached. Please try again in {retry_time}.",
        "llm_timeout": "The assistant is taking too long to respond. Please try again.",
        "tool_failure": "Couldn't reach Todoist right now. Please try again later.",
        "reset_prompt": "Type 'confirm' to clear your conversation history, or anything else to cancel.",
        "reset_confirmed": "History cleared. {count} messages removed.",
        "reset_confirmed_empty": "Your history was already empty.",
        "reset_cancelled": "Reset cancelled.",
        "refresh_confirmed": "Reconnected to Todoist.",
        "group_chat_rejected": "I only work in private chats.",
        "help_text": (
            "Available commands:\n"
            "/start - Onboarding for new users; connection status for existing users\n"
            "/token - Update your Todoist API token\n"
            "/reset - Clear your conversation history\n"
            "/refresh - Reconnect to Todoist\n"
            "/help - Show this list of commands"
        ),
        "please_wait": "Processing your request, please wait...",
        "decrypt_error": "There was a problem with your stored token. Please re-register with /token.",
        "send_error": "Something went wrong sending the message.",
        "db_error": "A temporary error occurred. Please try again.",
    },
    "ru": {
        "welcome": (
            "Добро пожаловать! Чтобы подключить Todoist, найдите свой API-токен в "
            "Todoist: Настройки -> Интеграции -> Разработчик, и отправьте его мне сюда. "
            "Ваше сообщение с токеном будет немедленно удалено после обработки в целях "
            "безопасности."
        ),
        "token_accepted": "Токен принят. Ваше сообщение удалено. Всё готово!",
        "token_invalid": "Этот токен не подошёл. Проверьте его и отправьте снова.",
        "token_network_error": "Не удалось связаться с Todoist для проверки токена. Попробуйте снова.",
        "token_deletion_failed": "Не удалось удалить ваше сообщение с токеном. Удалите его вручную.",
        "token_accidental": "Похоже, вы отправили токен Todoist — он удалён для вашей безопасности.",
        "already_registered": "Вы уже подключены к Todoist.",
        "unregistered": "Вы ещё не настроены. Отправьте /start, чтобы начать.",
        "rate_limit_session": "Достигнут лимит сессии. Попробуйте снова через {retry_time}.",
        "rate_limit_week": "Достигнут недельный лимит. Попробуйте снова через {retry_time}.",
        "llm_timeout": "Ассистент отвечает слишком долго. Попробуйте снова.",
        "tool_failure": "Не удалось связаться с Todoist. Попробуйте позже.",
        "reset_prompt": "Напишите 'confirm', чтобы очистить историю, или что-нибудь другое для отмены.",
        "reset_confirmed": "История очищена. Удалено сообщений: {count}.",
        "reset_confirmed_empty": "Ваша история уже была пустой.",
        "reset_cancelled": "Сброс отменён.",
        "refresh_confirmed": "Повторное подключение к Todoist выполнено.",
        "group_chat_rejected": "Я работаю только в личных чатах.",
        "help_text": (
            "Доступные команды:\n"
            "/start - Онбординг для новых пользователей; статус подключения для существующих\n"
            "/token - Обновить API-токен Todoist\n"
            "/reset - Очистить историю переписки\n"
            "/refresh - Переподключиться к Todoist\n"
            "/help - Показать список команд"
        ),
        "please_wait": "Обрабатываю запрос, подождите...",
        "decrypt_error": "Проблема с сохранённым токеном. Зарегистрируйтесь снова с помощью /token.",
        "send_error": "Не удалось отправить сообщение.",
        "db_error": "Произошла временная ошибка. Попробуйте снова.",
    },
}


def get(key: str, lang: str, **kwargs: object) -> str:
    """Return the user-facing string for `key` in `lang`, formatted with `kwargs`.

    Unknown `lang` falls back to English silently. Unknown `key` raises
    `KeyError`.
    """
    lang_messages = _MESSAGES.get(lang, _MESSAGES[_DEFAULT_LANG])
    template = lang_messages[key]
    return template.format(**kwargs)
