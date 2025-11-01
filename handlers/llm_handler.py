from datetime import datetime
import json

from config.config import SUMMARY_PROMPT, TITLE_PROMPT, CLIENT

# Функция для суммаризации
def get_summary_response(cleaned_dialogue: str) -> str:

    chat_completion = CLIENT.chat.completions.create(
        model="openai/gpt-4.1-mini", 
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

now = datetime.now()
date = now.strftime("%d.%m.%Y")

def llm_response(user_text: str) -> str:
    
    instruction = f'''Ты умный ассистент Мэри по помощи в поиске и добавлении информации в векторных базах данных.
    Определи намерение пользователя по его сообщению, а именно, хочет ли он добавить информацию в базу знаний или же найти в ней информацию.
    Если пользователь хочет добавить информацию, то тебе нужно лишь занести её в базу знаний, не меняя текст пользователя, дай ответ в формате:
    {{"key": 0, "text": <текст>}}. Если же пользователь ищет информацию, то четко структурируй его вопрос при надобности и
    дай ответ в формате: {{"key": 1, "text": <запрос пользователя>}}. Сегодня {date}. Учитывай это при занесении информации.
    Например, если пользователь просит записать созвон на завтра на 14:00 - следовательно, запись должна быть:
    Созвон 03.09.2025 в 14:00. Также, если пользователь просто разговаривает с тобой, ответь ему очень кратко.
    Ответ отправь в формате {{"key": 3, "text": <твой ответ>}}'''

    chat_completion = CLIENT.chat.completions.create(
        model="openai/gpt-4o-mini",
        messages=[
            {"role": "system", "content": instruction},
            {"role": "user", "content": user_text}
        ]
    )
    try:
        response_dict = json.loads(chat_completion.choices[0].message.content)

        key_value = response_dict.get('key')
        text_value = response_dict.get('text')

    except json.JSONDecodeError as e:
        print(f"Ошибка при парсинге JSON: {e}")
    return key_value, text_value

def llm_response_after_kb(user_text: str) -> str:
    
    instruction = f'''Ты умный ассистент Мэри. Максимально точно попробуй ответить на этот вопрос.
    Если ты не уверена, что сможешь правильно ответить, скажи об этом. Будь очень строгой по разговору.
    Если тебе задают вопрос не по делу, то есть какое-либо приветствие или бессмысленные вопросы по отношению
    к тебе, то ответь, что вопрос не по формату разговора.'''

    chat_completion = CLIENT.chat.completions.create(
        model="openai/gpt-4o-mini",
        messages=[
            {"role": "system", "content": instruction},
            {"role": "user", "content": user_text}
        ]
    )

    return chat_completion.choices[0].message.content

def mary_check(user_text: str) -> str:
    
    instruction = f'''Ты очень хорошо разбираешься в русском языке. Определи намерение пользователя:
    Если он обращается к Мэри, нужно вывести {{"key": 1}}, если же не обращается - {{"key": 0}}. Важно, чтобы ты 
    наиболее точно определял, точно ли пользователь хотел обратиться к Мэри или нет. Например,
    'Привет, Мэри, слушай...' или 'Мэри, я ....' - точно обращения, тогда как 'Слушайте, парни, когда мы говорим о Мэри, мы говорим об умном ассистенте,
    а не об боте' - не обращение.'''

    chat_completion = CLIENT.chat.completions.create(
        model="openai/gpt-4o-mini",
        messages=[
            {"role": "system", "content": instruction},
            {"role": "user", "content": user_text}
        ]
    )
    response_dict = json.loads(chat_completion.choices[0].message.content)
    key_value = response_dict.get('key')
    return key_value