import os
from dotenv import load_dotenv
import psycopg2
import pandas as pd
from datetime import date, timedelta
from sqlalchemy import create_engine
import logging

# Загрузка переменных из .env
load_dotenv()

DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Подключение
conn = psycopg2.connect(
    dbname=DB_NAME,
    user=DB_USER,
    password=DB_PASSWORD,
    host=DB_HOST,
    port=DB_PORT
)

# Загрузка таблиц
guest_details = pd.read_sql("SELECT * FROM guest_details", conn)
room_characteristics = pd.read_sql("SELECT * FROM room_characteristics", conn)
regular_prices = pd.read_sql("SELECT * FROM regular_prices", conn)
special_offers = pd.read_sql("SELECT * FROM special_offers", conn)
loyalty_discounts = pd.read_sql("SELECT * FROM loyalty_discounts", conn)

# Приведение дат
regular_prices['date'] = pd.to_datetime(regular_prices['date'])
special_offers['start_living_date'] = pd.to_datetime(special_offers['start_living_date'])
special_offers['end_living_date'] = pd.to_datetime(special_offers['end_living_date'])
special_offers['start_booking_date'] = pd.to_datetime(special_offers['start_booking_date'])
special_offers['end_booking_date'] = pd.to_datetime(special_offers['end_booking_date'])

today = pd.Timestamp.today().normalize()

guest_prices = []

def normalize(text):
    return set(text.lower().replace('(', '').replace(')', '').replace('|', '').replace(',', '').split())

def is_category_match(offer_category, room_category):
    return normalize(offer_category).issubset(normalize(room_category))

# Главный цикл
for _, guest in guest_details.iterrows():
    logging.info(f"Обработка гостя: {guest['name']} {guest['surname']}, loyalty: {guest['loyalty']}")
    
    suitable_rooms = room_characteristics[room_characteristics['number_of_main_beds'] >= guest['number_of_adults']]
    threshold_id = room_characteristics[room_characteristics['room_category'] == guest['threshold_category']]['id'].min()
    suitable_rooms = suitable_rooms[suitable_rooms['id'] >= threshold_id]

    for _, room in suitable_rooms.iterrows():
        room_prices = regular_prices[regular_prices['room_category'] == room['room_category']]

        for _, price_row in room_prices.iterrows():
            original_price = price_row['price']
            final_price = original_price
            applied_offer = None

            for _, offer in special_offers.iterrows():
                try:
                    in_range = offer['start_living_date'] <= price_row['date'] <= offer['end_living_date']
                    bookable = offer['start_booking_date'] <= today <= offer['end_booking_date']
                    applicable = False
                    offer_categories = offer['categories']

                    if "Все категории" in offer_categories:
                        applicable = True
                    elif "Все виллы" in offer_categories and "вилла" in room['room_category'].lower():
                        applicable = True
                    else:
                        for cat in offer_categories:
                            if is_category_match(cat, room['room_category']):
                                applicable = True
                                break

                    if in_range and bookable and applicable:
                        C = original_price
                        local_vars = {'C': C}
                        exec(offer['formula'], {}, local_vars)
                        final_price = local_vars['N']
                        applied_offer = offer['name']
                        logging.info(f"Применено спецпредложение '{applied_offer}' для комнаты '{room['room_category']}'")

                        if offer['loyalty_compatible']:
                            pct = loyalty_discounts.query("level == @guest.loyalty")["discount_percent"].values[0]
                            final_price *= (1 - pct / 100)
                            logging.info(f"Доп. скидка по лояльности {guest['loyalty']}: -{pct}%")
                        break
                except Exception as e:
                    logging.error(f"Ошибка при применении спецпредложения '{offer['name']}': {e}")
                    continue
            else:
                try:
                    pct = loyalty_discounts.query("level == @guest.loyalty")["discount_percent"].values[0]
                    final_price = original_price * (1 - pct / 100)
                    logging.info(f"Применена только скидка по лояльности {guest['loyalty']}: -{pct}%")
                except Exception as e:
                    logging.error(f"Ошибка при расчёте лояльности: {e}")
                    final_price = original_price

            if final_price <= guest['desired_cost'] * 1.2:
                guest_prices.append({
                    'id': guest['id'],
                    'name': guest['name'],
                    'surname': guest['surname'],
                    'room_category': room['room_category'],
                    'date': price_row['date'],
                    'tariff': price_row['tariff'],
                    'old_price': original_price,
                    'price': round(final_price),
                    'special_offer': applied_offer if applied_offer else '',
                    'loyalty': guest['loyalty']
                })

# Преобразуем в DataFrame
df = pd.DataFrame(guest_prices)

# Сортировка и добавление временных групп
df = df.sort_values(by=['id', 'room_category', 'tariff', 'price', 'special_offer', 'loyalty', 'date'])

# Группировка по непрерывным датам
def group_periods(df):
    df['date'] = pd.to_datetime(df['date'])
    df['grp'] = (df['date'] != df['date'].shift() + pd.Timedelta(days=1)).cumsum()
    result = df.groupby(['id', 'name', 'surname', 'room_category', 'tariff', 'old_price', 'price', 'special_offer', 'loyalty', 'grp']).agg(
        period_start=('date', 'min'),
        period_end=('date', 'max')
    ).reset_index()

    result['period'] = result['period_start'].dt.strftime('%Y-%m-%d') + ' - ' + result['period_end'].dt.strftime('%Y-%m-%d')
    return result[['id', 'name', 'surname', 'room_category', 'period', 'tariff', 'old_price', 'price', 'special_offer', 'loyalty']]

df_grouped = group_periods(df)

# Сохранение в БД
engine = create_engine(f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}")
df_grouped.to_sql('guest_prices', engine, index=False, if_exists='replace')

logging.info(f"Готово! В таблицу guest_prices записано {len(df_grouped)} строк.")