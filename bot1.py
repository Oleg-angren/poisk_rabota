import asyncio
import logging
import urllib.parse
import os
import time
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import aiohttp
from aiocron import crontab

# 🔒 Загружаем токен из .env
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TELEGRAM_TOKEN:
    raise ValueError(
        "❌ ОШИБКА: Токен не найден!\n"
        "1. Создайте файл .env в папке проекта\n"
        "2. Добавьте: TELEGRAM_TOKEN=ваш_токен\n"
        "3. Перезапустите бота"
    )

# 📦 Хранилище данных
SUBSCRIPTIONS = {}      # {chat_id: query}
SENT_VACANCIES = set()  # Уникальные ID отправленных вакансий

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== РАБОТА С HH.RU ====================
async def fetch_vacancies(query, session):
    """Получаем вакансии из Воронежа (area=26) с поиском ТОЛЬКО по названию"""
    try:
        encoded_query = urllib.parse.quote(query)
        url = f"https://api.hh.ru/vacancies?text={encoded_query}&area=26&per_page=5&search_field=name"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        logger.info(f"🔍 Запрос к hh.ru (Воронеж): {url}")
        async with session.get(url, headers=headers, timeout=15) as resp:
            logger.info(f"✅ Статус ответа hh.ru: {resp.status}")
            
            if resp.status != 200:
                logger.error(f"❌ Ошибка hh.ru ({resp.status})")
                return []
            
            data = await resp.json()
            items = data.get('items', [])
            logger.info(f"📄 Получено вакансий: {len(items)}")
            return items
            
    except Exception as e:
        logger.error(f"💥 Исключение при запросе к hh.ru: {e}", exc_info=True)
        return []

def format_salary(salary_info):
    """Форматируем зарплату с эмодзи"""
    if not salary_info:
        return "💰 Зарплата не указана"
    
    currency = salary_info.get('currency', 'RUR')
    salary_from = salary_info.get('from')
    salary_to = salary_info.get('to')
    
    if salary_from and salary_to:
        return f"💰 {salary_from} – {salary_to} {currency}"
    elif salary_from:
        return f"💰 от {salary_from} {currency}"
    elif salary_to:
        return f"💰 до {salary_to} {currency}"
    else:
        return "💰 Зарплата не указана"

# ==================== ОТПРАВКА СООБЩЕНИЙ ====================
async def send_vacancies_list(bot, chat_id, query, vacancies, is_new=True):
    """Отправляет список вакансий с эмодзи"""
    prefix = "✨ НОВЫЕ вакансии" if is_new else "📄 Вакансии"
    messages = [f"{prefix} по запросу «<b>{query}</b>» в <b>Воронеже</b>:\n"]
    
    for i, v in enumerate(vacancies[:10], 1):
        name = v['name']
        salary = format_salary(v.get('salary'))
        link = v['alternate_url']
        messages.append(f"\n<b>{i}. {name}</b>\n{salary}\n🔗 {link}")
    
    await bot.send_message(
        chat_id=chat_id,
        text=''.join(messages),
        parse_mode="HTML",
        disable_web_page_preview=True
    )

# ==================== КОМАНДЫ БОТА ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для поиска вакансий в Воронеже на hh.ru.\n\n"
        "📬 Команды:\n"
        "• /search [запрос] — найти вакансии сейчас\n"
        "• /subscribe [запрос] — получать уведомления каждый час\n"
        "• /unsubscribe — отменить подписку\n\n"
        "💡 Примеры:\n"
        "<code>/search водитель</code>\n"
        "<code>/subscribe повар</code>\n"
        "<code>/search продавец</code>",
        parse_mode="HTML"
    )

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text(
            "❗ Укажите запрос!\nПример: <code>/subscribe водитель</code>",
            parse_mode="HTML"
        )
        return
    
    query = ' '.join(context.args)
    SUBSCRIPTIONS[chat_id] = query
    await update.message.reply_text(
        f"✅ Подписка активирована!\n\n"
        f"📬 Запрос: <b>{query}</b>\n"
        f"📍 Город: <b>Воронеж</b>\n"
        f"⏰ Проверка: каждый час\n\n"
        f"Чтобы отписаться: /unsubscribe",
        parse_mode="HTML"
    )
    logger.info(f"✅ Пользователь {chat_id} подписался на '{query}'")

async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in SUBSCRIPTIONS:
        query = SUBSCRIPTIONS.pop(chat_id)
        await update.message.reply_text(
            f"❌ Подписка отменена!\n\n"
            f"Вы больше не получаете уведомления по запросу:\n<b>{query}</b>",
            parse_mode="HTML"
        )
        logger.info(f"❌ Пользователь {chat_id} отписался от '{query}'")
    else:
        await update.message.reply_text(
            "ℹ️ У вас нет активных подписок.\n"
            "Чтобы подписаться: /subscribe [запрос]",
            parse_mode="HTML"
        )

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "❗ Укажите запрос!\nПример: <code>/search водитель</code>",
            parse_mode="HTML"
        )
        return
    
    query = ' '.join(context.args)
    chat_id = update.effective_chat.id
    
    await update.message.reply_text(
        f"🔍 Ищу вакансии «<b>{query}</b>» в <b>Воронеже</b>...",
        parse_mode="HTML"
    )
    
    async with aiohttp.ClientSession() as session:
        vacancies = await fetch_vacancies(query, session)
        
        if not vacancies:
            await update.message.reply_text(
                f"📭 По запросу «<b>{query}</b>» в Воронеже вакансий не найдено 😕\n\n"
                "💡 Советы:\n"
                "• Упростите запрос («водитель» вместо «водитель погрузчика»)\n"
                "• Попробуйте синонимы («курьер», «доставщик»)\n"
                "• Проверьте правописание",
                parse_mode="HTML"
            )
            return
        
        new_vacancies = [v for v in vacancies if str(v['id']) not in SENT_VACANCIES]
        
        if not new_vacancies:
            await update.message.reply_text(
                f"📭 По запросу «<b>{query}</b>» найдены вакансии, но все уже показывались ранее.",
                parse_mode="HTML"
            )
            return
        
        # Отправляем новые вакансии и сохраняем их
        await send_vacancies_list(update.get_bot(), chat_id, query, new_vacancies, is_new=True)
        for v in new_vacancies:
            SENT_VACANCIES.add(str(v['id']))

async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_query = SUBSCRIPTIONS.get(update.effective_chat.id)
    status = "✅ Активна" if current_query else "❌ Нет"
    await update.message.reply_text(
        f"⚙️ Статус бота:\n\n"
        f"📬 Подписка: {status}\n"
        f"🔍 Текущий запрос: {f'<b>{current_query}</b>' if current_query else '—'}\n"
        f"📨 Отправлено вакансий: {len(SENT_VACANCIES)}\n"
        f"📍 Город: Воронеж (area=26)\n"
        f"⏰ Проверка: каждый час",
        parse_mode="HTML"
    )

# ==================== АВТОМАТИЧЕСКАЯ ПРОВЕРКА ПОДПИСОК ====================
async def check_and_send(bot):
    """Обязательная проверка подписок каждый час с уведомлениями"""
    if not SUBSCRIPTIONS:
        logger.info("📭 Нет подписок — пропускаем проверку")
        return
    
    logger.info(f"⏰ Запуск часовой проверки для {len(SUBSCRIPTIONS)} пользователей...")
    async with aiohttp.ClientSession() as session:
        for chat_id, query in list(SUBSCRIPTIONS.items()):
            try:
                logger.info(f"🔍 Проверка для {chat_id}: '{query}'")
                vacancies = await fetch_vacancies(query, session)
                
                # Случай 1: Вакансий по запросу нет совсем
                if not vacancies:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"📭 <b>Часовая проверка</b>\n\n"
                             f"По запросу «<b>{query}</b>» в Воронеже вакансий не найдено 😕",
                        parse_mode="HTML"
                    )
                    logger.info(f"📭 Отправлено 'вакансий не найдено' для {chat_id}")
                    continue
                
                # Фильтруем новые вакансии
                new_vacancies = [v for v in vacancies if str(v['id']) not in SENT_VACANCIES]
                
                # Случай 2: Есть вакансии, но все уже показывались
                if not new_vacancies:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"📭 <b>Часовая проверка</b>\n\n"
                             f"🕗 Проверка по подписке «<b>{query}</b>»:\n"
                             f"Новых вакансий нет (все уже показывались ранее) 😕",
                        parse_mode="HTML"
                    )
                    logger.info(f"📭 Отправлено 'нет новых вакансий' для {chat_id}")
                    continue
                
                # Случай 3: Есть новые вакансии → отправляем их
                await send_vacancies_list(bot, chat_id, query, new_vacancies, is_new=True)
                
                # Сохраняем отправленные вакансии
                for v in new_vacancies:
                    SENT_VACANCIES.add(str(v['id']))
                
                logger.info(f"✨ Отправлено {len(new_vacancies)} новых вакансий для {chat_id}")
                
            except Exception as e:
                logger.error(f"💥 Ошибка обработки {chat_id}: {e}", exc_info=True)

# ==================== ЗАПУСК БОТА ====================
def main():
    global application
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("subscribe", subscribe))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe))
    application.add_handler(CommandHandler("search", search))
    application.add_handler(CommandHandler("test", test))
    
    # ⏰ Проверка КАЖДЫЙ ЧАС (в 0 минут каждого часа)
    crontab('0 * * * *', func=lambda: asyncio.create_task(check_and_send(application.bot)))
    
    logger.info("🚀 Бот запущен! Ищет вакансии в Воронеже (hh.ru, area=26). Проверка каждый час.")
    application.run_polling()

if __name__ == '__main__':
    main()