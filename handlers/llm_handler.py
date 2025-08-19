from config.config import SUMMARY_PROMPT, TITLE_PROMPT, ASSISTANT_PROMPT, TG_PROMPT
from config.load_models import CLIENT

# Функция для суммаризации
def get_summary_response(cleaned_dialogue: str) -> str:

    chat_completion = CLIENT.chat.completions.create(
        model="openai/gpt-5-mini", 
        messages=[
            {"role": "system", "content": SUMMARY_PROMPT},
            {"role": "user", "content": cleaned_dialogue}
        ]
    )
    return chat_completion.choices[0].message.content

# Функция для названия встречи
def get_title_response(cleaned_dialogue: str) -> str:

    chat_completion = CLIENT.chat.completions.create(
        model="openai/gpt-4o-mini", 
        messages=[
            {"role": "system", "content": TITLE_PROMPT},
            {"role": "user", "content": cleaned_dialogue}
        ]
    )
    return chat_completion.choices[0].message.content

# Функция ответа от Мэри
def get_mary_response(command: str) -> str:

    chat_completion = CLIENT.chat.completions.create(
        model="openai/gpt-4o-mini", 
        messages=[
            {"role": "system", "content": ASSISTANT_PROMPT},
            {"role": "user", "content": command}
        ]
    )
    return chat_completion.choices[0].message.content

# Функция ответа для тг-бота
async def tg_bot_response(command: str) -> str:
    chat_completion = await CLIENT.chat.completions.acreate(
        model="openai/gpt-4o-mini",
        messages=[
            {"role": "system", "content": TG_PROMPT},
            {"role": "user", "content": command}
        ]
    )
    return chat_completion.choices[0].message.content