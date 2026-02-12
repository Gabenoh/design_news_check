import asyncio
import aiohttp
from bs4 import BeautifulSoup
import json
import os
from datetime import datetime
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.utils.markdown import hbold, hlink, quote_html
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import ParseMode, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from config import *

# Налаштування логування
logging.basicConfig(level=logging.INFO)

# Ініціалізація бота і диспетчера
bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)


# Стан для додавання нового сайту
class SiteForm(StatesGroup):
    name = State()
    url = State()
    selector = State()
    title_attr = State()
    link_attr = State()
    base_url = State()


# Клас для роботи з підписниками
class SubscribersManager:
    def __init__(self, file_path='subscribers.json'):
        self.file_path = file_path
        self.subscribers = self.load_subscribers()

    def load_subscribers(self):
        if os.path.exists(self.file_path):
            with open(self.file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []

    def save_subscribers(self):
        with open(self.file_path, 'w', encoding='utf-8') as f:
            json.dump(self.subscribers, f, ensure_ascii=False, indent=2)

    def add_subscriber(self, user_id):
        if user_id not in self.subscribers:
            self.subscribers.append(user_id)
            self.save_subscribers()
            return True
        return False

    def remove_subscriber(self, user_id):
        if user_id in self.subscribers:
            self.subscribers.remove(user_id)
            self.save_subscribers()
            return True
        return False

    def get_subscribers(self):
        return self.subscribers


# Клас для парсингу новин
class NewsParser:
    def __init__(self, sites_config_file='sites_config.json', seen_news_file='seen_news.json'):
        self.sites_config_file = sites_config_file
        self.seen_news_file = seen_news_file
        self.sites_config = self.load_sites_config()
        self.seen_news = self.load_seen_news()

    def load_sites_config(self):
        if os.path.exists(self.sites_config_file):
            with open(self.sites_config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            # Стандартна конфігурація, якщо файл не існує
            # https://ain.ua/  |  https://vctr.media/ua/   |  https://blog.hubspot.com/  |  https://cases.media/en
            # |  https://www.komarov.design/

            default_config = [
                {
                    'name': 'AIN.UA',
                    'url': 'https://ain.ua/',
                    'selector': '.post-link',
                    'title_attr': '',
                    'link_attr': 'href',
                    'base_url': 'https://ain.ua'
                },
                {
                    'name': 'Vector',
                    'url': 'https://vctr.media/ua/',
                    'selector': '.jeg_post_title a',
                    'title_attr': '',
                    'link_attr': 'href',
                    'base_url': ''
                },
                {
                    'name': 'HubSpot Blog',
                    'url': 'https://blog.hubspot.com/',
                    'selector': '.blog-card__title-link',
                    'title_attr': '',
                    'link_attr': 'href',
                    'base_url': ''
                },
                {
                    'name': 'Komarov Design',
                    'url': 'https://www.komarov.design/',
                    'selector': '.loop.inset-hover .secondary-button',
                    'title_attr': '',
                    'link_attr': 'href',
                    'base_url': 'https://www.komarov.design'
                }
            ]
            self.save_sites_config(default_config)
            return default_config

    def save_sites_config(self, config=None):
        if config is None:
            config = self.sites_config
        with open(self.sites_config_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    def add_site(self, site_config):
        self.sites_config.append(site_config)
        self.save_sites_config()

    def remove_site(self, site_name):
        self.sites_config = [site for site in self.sites_config if site['name'] != site_name]
        self.save_sites_config()
        return len(self.sites_config)

    def load_seen_news(self):
        if os.path.exists(self.seen_news_file):
            with open(self.seen_news_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def save_seen_news(self):
        with open(self.seen_news_file, 'w', encoding='utf-8') as f:
            json.dump(self.seen_news, f, ensure_ascii=False, indent=2)

    async def fetch_page(self, session, url):
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'uk-UA,uk;q=0.8,en-US;q=0.5,en;q=0.3',
            }
            async with session.get(url, headers=headers, timeout=15) as response:
                if response.status == 200:
                    return await response.text()
                else:
                    logging.warning(f"Помилка при отриманні сторінки {url}: {response.status}")
                    return None
        except Exception as e:
            logging.error(f"Виникла помилка при запиті до {url}: {e}")
            return None

    async def parse_site(self, session, site_name, url, selector, title_attr, link_attr, base_url=None):
        html = await self.fetch_page(session, url)
        if not html:
            logging.error(f"Не вдалося отримати HTML для сайту {site_name}")
            return []

        try:
            soup = BeautifulSoup(html, 'html.parser')
            news_items = soup.select(selector)

            if not news_items:
                logging.warning(f"Селектор '{selector}' не знайшов елементів на сайті {site_name}")

            logging.info(f"Знайдено {len(news_items)} елементів на сайті {site_name}")

            new_articles = []

            for item in news_items:
                try:
                    # Отримання заголовку
                    if title_attr:
                        title = item.get(title_attr, '')
                    else:
                        title = item.text.strip()

                    # Логуємо знайдений заголовок
                    logging.info(f"Заголовок з {site_name}: {title}")

                    # Отримання посилання - виправлено обробку посилань
                    link = ""
                    if link_attr == 'href' and item.name == 'a':
                        link = item.get('href', '')
                    elif link_attr == 'parent':
                        parent = item.parent
                        link = parent.get('href', '') if parent and parent.name == 'a' else ''
                    elif link_attr.startswith('select:'):
                        link_selector = link_attr.split(':', 1)[1]
                        link_element = item.select_one(link_selector)
                        link = link_element.get('href', '') if link_element else ''
                    elif not link_attr or link_attr == 'href':
                        # Шукаємо посилання всередині елемента, якщо сам елемент не є посиланням
                        if item.name != 'a':
                            link_element = item.find('a')
                            link = link_element.get('href', '') if link_element else ''
                        else:
                            link = item.get('href', '')
                    else:
                        link = item.get(link_attr, '')

                    # Логуємо знайдене посилання
                    logging.info(f"Посилання з {site_name}: {link}")

                    # Перевірка, чи є заголовок і посилання
                    if not title:
                        logging.warning(f"Не знайдено заголовок для елемента на {site_name}")
                        continue

                    if not link:
                        logging.warning(f"Не знайдено посилання для елемента '{title}' на {site_name}")
                        continue

                    # Додавання базового URL, якщо посилання відносне
                    if base_url and link and not (link.startswith('http://') or link.startswith('https://')):
                        if link.startswith('/'):
                            link = f"{base_url.rstrip('/')}{link}"
                        else:
                            link = f"{base_url.rstrip('/')}/{link.lstrip('/')}"
                        logging.info(f"Оновлене посилання з базовим URL: {link}")

                    # Створення унікального ідентифікатора для новини
                    news_id = f"{site_name}:{link}"

                    # Перевірка, чи бачили ми цю новину раніше
                    if news_id not in self.seen_news:
                        self.seen_news[news_id] = {
                            'title': title,
                            'link': link,
                            'first_seen': datetime.now().isoformat()
                        }
                        new_articles.append({
                            'site': site_name,
                            'title': title,
                            'link': link
                        })
                        logging.info(f"Додано нову статтю: {title} з {site_name}")
                except Exception as e:
                    logging.error(f"Помилка при обробці новини з {site_name}: {e}")

            return new_articles
        except Exception as e:
            logging.error(f"Помилка при парсингу сайту {site_name}: {e}")
            return []

    async def check_all_sites(self):
        async with aiohttp.ClientSession() as session:
            tasks = []
            for site_config in self.sites_config:
                tasks.append(
                    self.parse_site(
                        session,
                        site_config['name'],
                        site_config['url'],
                        site_config['selector'],
                        site_config.get('title_attr', ''),
                        site_config.get('link_attr', 'href'),
                        site_config.get('base_url', None)
                    )
                )

            results = await asyncio.gather(*tasks)
            all_new_articles = []
            for articles in results:
                all_new_articles.extend(articles)

            self.save_seen_news()
            return all_new_articles


# Ініціалізація менеджерів
subscribers_manager = SubscribersManager()
news_parser = NewsParser()


# Асинхронне завдання перевірки новин
async def check_news_task():
    while True:
        try:
            logging.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Перевіряємо новини...")
            new_articles = await news_parser.check_all_sites()

            if new_articles:
                subscribers = subscribers_manager.get_subscribers()
                logging.info(f"Знайдено {len(new_articles)} нових новин. Розсилаємо {len(subscribers)} підписникам.")

                for article in new_articles:
                    # Перевірка наявності всіх необхідних полів
                    site_name = article.get('site', 'Невідомий сайт')
                    title = article.get('title', 'Без заголовку')
                    link = article.get('link', '')

                    # Логування для відлагодження
                    logging.info(f"Підготовка повідомлення: Сайт={site_name}, Заголовок={title}, Посилання={link}")

                    # Формуємо повідомлення з перевіркою посилання
                    if link:
                        message_text = (
                            f"📰 {hbold(site_name)}\n\n"
                            f"{quote_html(title)}\n\n"
                            f"🔗 {hlink('Читати повністю', link)}"
                        )
                    else:
                        message_text = (
                            f"📰 {hbold(site_name)}\n\n"
                            f"{quote_html(title)}\n\n"
                            f"⚠️ Посилання недоступне"
                        )

                    for user_id in subscribers:
                        try:
                            await bot.send_message(
                                user_id,
                                message_text,
                                parse_mode=ParseMode.HTML,
                                disable_web_page_preview=False
                            )
                            # Невелика затримка, щоб не перевищити ліміти Telegram API
                            await asyncio.sleep(0.1)
                        except Exception as e:
                            logging.error(f"Помилка при надсиланні повідомлення користувачу {user_id}: {e}")

        except Exception as e:
            logging.error(f"Помилка в завданні перевірки новин: {e}")

        # Затримка перед наступною перевіркою
        await asyncio.sleep(CHECK_INTERVAL)


# Обробники команд бота

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("📰 Підписатися на новини"))
    keyboard.add(KeyboardButton("🔕 Відписатися від новин"))

    await message.answer(
        f"Вітаю, {message.from_user.first_name}! 👋\n\n"
        f"Я бот для моніторингу новин з різних джерел. "
        f"Я повідомлю вас, як тільки з'явиться нова новина на відстежуваних сайтах.\n\n"
        f"Використовуйте меню нижче, щоб підписатися або відписатися від оновлень.",
        reply_markup=keyboard
    )


@dp.message_handler(lambda message: message.text == "📰 Підписатися на новини")
async def subscribe(message: types.Message):
    if subscribers_manager.add_subscriber(message.from_user.id):
        await message.answer("✅ Ви успішно підписалися на оновлення новин!")
    else:
        await message.answer("ℹ️ Ви вже підписані на оновлення новин.")


@dp.message_handler(lambda message: message.text == "🔕 Відписатися від новин")
async def unsubscribe(message: types.Message):
    if subscribers_manager.remove_subscriber(message.from_user.id):
        await message.answer("✅ Ви успішно відписалися від оновлень новин.")
    else:
        await message.answer("ℹ️ Ви не були підписані на оновлення новин.")


@dp.message_handler(commands=['sites'])
async def cmd_sites(message: types.Message):
    sites = news_parser.sites_config
    if not sites:
        await message.answer("🔍 Поки що немає налаштованих сайтів для моніторингу.")
        return

    text = "📋 Список сайтів для моніторингу:\n\n"
    for i, site in enumerate(sites, 1):
        text += f"{i}. {hbold(site['name'])}\n   🌐 {site['url']}\n\n"

    # Для адміністратора додати кнопки керування
    if message.from_user.id == ADMIN_ID:
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("➕ Додати сайт", callback_data="add_site"))
        keyboard.add(InlineKeyboardButton("❌ Видалити сайт", callback_data="remove_site"))
        await message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    else:
        await message.answer(text, parse_mode=ParseMode.HTML)


# Обробники для адміністрування (додавання/видалення сайтів)

@dp.callback_query_handler(lambda c: c.data == "add_site")
async def process_add_site(callback_query: types.CallbackQuery):
    if callback_query.from_user.id != ADMIN_ID:
        await bot.answer_callback_query(callback_query.id, "⚠️ Ця функція доступна тільки адміністратору.")
        return

    await bot.answer_callback_query(callback_query.id)
    await bot.send_message(callback_query.from_user.id, "Введіть назву сайту:")
    await SiteForm.name.set()


@dp.message_handler(state=SiteForm.name)
async def process_site_name(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['name'] = message.text

    await message.answer("Введіть URL сайту:")
    await SiteForm.next()


@dp.message_handler(state=SiteForm.url)
async def process_site_url(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['url'] = message.text

    await message.answer(
        "Введіть CSS-селектор для пошуку новин:\n"
        "Наприклад: '.article_header' або '.news-item h2'"
    )
    await SiteForm.next()


@dp.message_handler(state=SiteForm.selector)
async def process_site_selector(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['selector'] = message.text

    await message.answer(
        "Введіть атрибут для заголовка (або залиште порожнім, щоб використовувати текст елемента):"
    )
    await SiteForm.next()


@dp.message_handler(state=SiteForm.title_attr)
async def process_title_attr(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['title_attr'] = message.text

    await message.answer(
        "Введіть атрибут для посилання або спеціальний метод:\n"
        "- 'href' для прямих посилань (за замовчуванням)\n"
        "- 'parent' якщо посилання знаходиться в батьківському елементі\n"
        "- 'select:a' для використання CSS-селектора"
    )
    await SiteForm.next()


@dp.message_handler(state=SiteForm.link_attr)
async def process_link_attr(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['link_attr'] = message.text

    await message.answer(
        "Введіть базовий URL для відносних посилань (або залиште порожнім, якщо посилання повні):"
    )
    await SiteForm.next()


@dp.message_handler(state=SiteForm.base_url)
async def process_base_url(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['base_url'] = message.text

        # Додавання нового сайту
        site_config = {
            'name': data['name'],
            'url': data['url'],
            'selector': data['selector'],
            'title_attr': data['title_attr'],
            'link_attr': data['link_attr'] if data['link_attr'] else 'href',
            'base_url': data['base_url'] if data['base_url'] else None
        }

        news_parser.add_site(site_config)

    await state.finish()
    await message.answer(f"✅ Сайт '{data['name']}' успішно додано до моніторингу!")


@dp.callback_query_handler(lambda c: c.data == "remove_site")
async def process_remove_site(callback_query: types.CallbackQuery):
    if callback_query.from_user.id != ADMIN_ID:
        await bot.answer_callback_query(callback_query.id, "⚠️ Ця функція доступна тільки адміністратору.")
        return

    await bot.answer_callback_query(callback_query.id)

    sites = news_parser.sites_config
    if not sites:
        await bot.send_message(callback_query.from_user.id, "🔍 Немає сайтів для видалення.")
        return

    keyboard = InlineKeyboardMarkup()
    for site in sites:
        keyboard.add(InlineKeyboardButton(
            f"❌ {site['name']}",
            callback_data=f"delete_site:{site['name']}"
        ))

    await bot.send_message(
        callback_query.from_user.id,
        "Виберіть сайт для видалення:",
        reply_markup=keyboard
    )


@dp.callback_query_handler(lambda c: c.data.startswith("delete_site:"))
async def delete_site(callback_query: types.CallbackQuery):
    if callback_query.from_user.id != ADMIN_ID:
        await bot.answer_callback_query(callback_query.id, "⚠️ Ця функція доступна тільки адміністратору.")
        return

    site_name = callback_query.data.split(':', 1)[1]
    count = news_parser.remove_site(site_name)

    await bot.answer_callback_query(callback_query.id)
    await bot.send_message(
        callback_query.from_user.id,
        f"✅ Сайт '{site_name}' видалено з моніторингу.\n"
        f"Залишилося {count} сайтів."
    )


@dp.message_handler(commands=['help'])
async def cmd_help(message: types.Message):
    help_text = (
        "🤖 <b>Команди бота:</b>\n\n"
        "/start - Почати роботу з ботом\n"
        "/sites - Переглянути список сайтів, що відстежуються\n"
        "/help - Показати цю довідку\n\n"
        "📰 <b>Підписка на новини:</b>\n"
        "Натисніть кнопку «Підписатися на новини», щоб отримувати сповіщення про нові публікації.\n\n"
        "🔕 <b>Відписка від новин:</b>\n"
        "Натисніть кнопку «Відписатися від новин», щоб зупинити отримання сповіщень."
    )

    if message.from_user.id == ADMIN_ID:
        help_text += (
            "\n\n👑 <b>Команди адміністратора:</b>\n"
            "В меню /sites ви можете:\n"
            "➕ Додати новий сайт для моніторингу\n"
            "❌ Видалити існуючий сайт\n"
        )

    await message.answer(help_text, parse_mode=ParseMode.HTML)


# Функція для запуску бота
async def on_startup(dp):
    # Запуск завдання перевірки новин
    asyncio.create_task(check_news_task())
    logging.info("Бот запущено!")


if __name__ == "__main__":
    # Запуск бота
    executor.start_polling(dp, on_startup=on_startup, skip_updates=True)
