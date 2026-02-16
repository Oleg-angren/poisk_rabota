import asyncio
import logging
import urllib.parse
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import aiohttp
from aiocron import crontab

# 🔑 ОБЯЗАТЕЛЬНО ЗАМЕНИТЕ НА СВОЙ ТОКЕН ОТ @BotFather!
TOKEN = '8082307822:AAFWJBO01AZhgLXyKC2s-bO9NK08PvNT7h0'

# Хранилище подписок: {chat_id: query}
SUBSCRIPTIONS = {}
# Множество отправленных вакансий (для избежания дубликатов)
SENT_VACANCIES = set()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def fetch_vacancies(query, session):
    """Получаем вакансии с hh.ru только для Воронежа (area=26) с поиском ТОЛЬКО по названию"""
    try:
        encoded_query = urllib.parse.quote(query)
        # ⚠️ ВАЖНО: используем 'text' вместо 'q' + 'search_field=name'
        url = f"https://api.hh.ru/vacancies?text={encoded_query}&area=26&per_page=5&search_field=name"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        logger.info(f"Запрос к hh.ru (Воронеж, строгий поиск): {url}")
        async with session.get(url, headers=headers, timeout=15) as resp:
            logger.info(f"Статус ответа hh.ru: {resp.status}")
            
            if resp.status != 200:
                text = await resp.text()
                logger.error(f"Ошибка hh.ru ({resp.status}): {text[:200]}")
                return []
            
            data = await resp.json()
            items = data.get('items', [])
            logger.info(f"Получено вакансий в Воронеже: {len(items)}")
            return items
            
    except Exception as e:
        logger.error(f"Исключение при запросе к hh.ru: {e}", exc_info=True)
        return []

async def send_new_vacancies(bot, chat_id, query):
    """Отправляем новые вакансии пользователю (только Воронеж)"""
    async with aiohttp.ClientSession() as session:
        vacancies = await fetch_vacancies(query, session)
        
        if not vacancies:
            logger.info(f"Нет вакансий по запросу '{query}' в Воронеже для чата {chat_id}")
            return
        
        new_vacancies = []
        for v in vacancies:
            vid = str(v['id'])
            if vid not in SENT_VACANCIES:
                SENT_VACANCIES.add(vid)
                name = v['name']
                salary_info = v.get('salary')
                if salary_info:
                    currency = salary_info.get('currency', 'RUR')
                    salary_from = salary_info.get('from')
                    salary_to = salary_info.get('to')
                    if salary_from and salary_to:
                        salary = f"{salary_from} - {salary_to} {currency}"
                    elif salary_from:
                        salary = f"от {salary_from} {currency}"
                    elif salary_to:
                        salary = f"до {salary_to} {currency}"
                    else:
                        salary = 'Зарплата не указана'
                else:
                    salary = 'Зарплата не указана'
                link = v['alternate_url']
                new_vacancies.append((name, salary, link))
        
        if new_vacancies:
            messages = [f"Новые вакансии по запросу «{query}» в <b>Воронеже</b>:\n"]
            for i, (name, salary, link) in enumerate(new_vacancies, 1):
                messages.append(f"\n<b>{i}. {name}</b>\n💰 {salary}\n🔗 {link}")
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=''.join(messages),
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
                logger.info(f"Отправлено {len(new_vacancies)} вакансий в чат {chat_id}")
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения в чат {chat_id}: {e}")
        else:
            logger.info(f"Все вакансии по '{query}' в Воронеже уже отправлены ранее")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"👋 Привет! Я бот для поиска вакансий на hh.ru.\n\n"
        f"Удачи в поиске работы\n"
        f"Команды:\n"
        f"/search [запрос] — найти вакансии в Воронеже сейчас\n"
        f"/subscribe [запрос] — получать уведомления о новых вакансиях в Воронеже каждый час\n"
        f"/test — проверить работу бота\n\n"
        f"Примеры:\n"
        f"<code>/search python</code>\n"
        f"<code>/search java backend</code>\n"
        f"<code>/subscribe аналитик</code>",
        parse_mode="HTML"
    )

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text(
            "Укажите запрос! Пример:\n<code>/subscribe python</code>",
            parse_mode="HTML"
        )
        return
    
    query = ' '.join(context.args)
    SUBSCRIPTIONS[chat_id] = query
    await update.message.reply_text(
        f"✅ Подписка активирована!\n"
        f"Буду присылать новые вакансии по запросу:\n<b>{query}</b>\n"
        f"Только вакансии из <b>Воронежа</b>.\n\n"
        f"Проверка каждый час",
        parse_mode="HTML"
    )
    logger.info(f"Пользователь {chat_id} подписался на '{query}' (Воронеж)")

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Поиск вакансий по запросу (только Воронеж)"""
    if not context.args:
        await update.message.reply_text(
            "Укажите запрос! Пример:\n<code>/search python</code>",
            parse_mode="HTML"
        )
        return
    
    query = ' '.join(context.args)
    chat_id = update.effective_chat.id
    
    await update.message.reply_text(
        f"🔍 Ищу вакансии по запросу «<b>{query}</b>» в <b>Воронеже</b>...",
        parse_mode="HTML"
    )
    
    async with aiohttp.ClientSession() as session:
        vacancies = await fetch_vacancies(query, session)
        
        if not vacancies:
            await update.message.reply_text(
                "❌ Вакансии не найдены в Воронеже. Попробуйте другой запрос:\n"
                "• Упростите запрос (например, «программист» вместо «senior python backend developer»)\n"
                "• Попробуйте синонимы («разработчик», «программист», «инженер»)"
            )
            return
        
        messages = ["Результаты поиска в Воронеже:\n"]
        for i, v in enumerate(vacancies[:5], 1):
            name = v['name']
            salary_info = v.get('salary')
            if salary_info:
                currency = salary_info.get('currency', 'RUR')
                salary_from = salary_info.get('from')
                salary_to = salary_info.get('to')
                if salary_from and salary_to:
                    salary = f"{salary_from} - {salary_to} {currency}"
                elif salary_from:
                    salary = f"от {salary_from} {currency}"
                elif salary_to:
                    salary = f"до {salary_to} {currency}"
                else:
                    salary = 'Зарплата не указана'
            else:
                salary = 'Зарплата не указана'
            link = v['alternate_url']
            messages.append(f"\n<b>{i}. {name}</b>\n💰 {salary}\n🔗 {link}")
        
        await update.message.reply_text(''.join(messages), parse_mode="HTML", disable_web_page_preview=True)

async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Тестовая команда для проверки работы бота"""
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"✅ Бот работает!\n"
        f"Удачи в поиске работы\n"
        f"Активных подписок: {len(SUBSCRIPTIONS)}\n"
        f"Отправлено уникальных вакансий: {len(SENT_VACANCIES)}\n"
        f"Фильтр: только Воронеж (area=26)",
        parse_mode="HTML"
    )
    logger.info(f"Тест от пользователя {chat_id}")

async def check_and_send(bot):
    """Проверяем вакансии для всех подписанных пользователей (только Воронеж)"""
    if not SUBSCRIPTIONS:
        logger.info("Нет подписок — пропускаем проверку")
        return
    
    logger.info(f"🕗 Запуск проверки вакансий для {len(SUBSCRIPTIONS)} пользователей (Воронеж)...")
    async with aiohttp.ClientSession() as session:
        for chat_id, query in list(SUBSCRIPTIONS.items()):
            try:
                logger.info(f"Проверка для чата {chat_id}, запрос: '{query}' (Воронеж)")
                vacancies = await fetch_vacancies(query, session)
                
                if not vacancies:
                    logger.info(f"Нет новых вакансий по '{query}' в Воронеже для чата {chat_id}")
                    continue
                
                new_count = 0
                for v in vacancies:
                    vid = str(v['id'])
                    if vid not in SENT_VACANCIES:
                        SENT_VACANCIES.add(vid)
                        new_count += 1
                
                if new_count > 0:
                    await send_new_vacancies(bot, chat_id, query)
                else:
                    logger.info(f"Новых вакансий нет для чата {chat_id}")
                    
            except Exception as e:
                logger.error(f"Ошибка при обработке чата {chat_id}: {e}", exc_info=True)

def main():
    application = ApplicationBuilder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("subscribe", subscribe))
    application.add_handler(CommandHandler("search", search))
    application.add_handler(CommandHandler("test", test))
    
    # Проверка каждый час
    crontab('0 * * * *', func=lambda: asyncio.create_task(check_and_send(application.bot)))
    
    logger.info("🚀 Бот запущен! Ищет вакансии только в Воронеже (area=26). Нажмите Ctrl+C для остановки.")
    application.run_polling()

if __name__ == '__main__':
    main()