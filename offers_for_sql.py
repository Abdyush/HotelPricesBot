import os
from dotenv import load_dotenv
import json
import psycopg2
from datetime import datetime

# Загрузка переменных из .env
load_dotenv()

DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")

# Настройки подключения к PostgreSQL
DB_PARAMS = {
    'host': DB_HOST,
    'dbname': DB_NAME,
    'user': DB_USER,
    'password': DB_PASSWORD
}

JSON_FILE = 'data_offers.json'

def parse_date(date_str):
    """Преобразует дату из строки 'дд.мм.гггг' в объект date."""
    return datetime.strptime(date_str, "%d.%m.%Y").date()

def insert_offer(cur, offer, stay_range):
    try:
        start_living = parse_date(stay_range[0])
        end_living = parse_date(stay_range[1])
        if start_living > end_living:
            print(f"⚠ Пропущен диапазон проживания: {start_living} > {end_living}")
            return
    except Exception as e:
        print(f"❌ Ошибка в датах проживания: {stay_range} — {e}")
        return

    try:
        booking_range = offer.get("Даты бронирования", [[]])[0]
        start_booking = parse_date(booking_range[0])
        end_booking = parse_date(booking_range[1])
    except Exception as e:
        print(f"❌ Ошибка в датах бронирования — {e}")
        return

    cur.execute("""
        INSERT INTO special_offers (
            name, categories, start_living_date, end_living_date,
            start_booking_date, end_booking_date,
            formula, min_days, loyalty_compatible
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        offer["Название"],
        offer.get("Категория", []),
        start_living,
        end_living,
        start_booking,
        end_booking,
        offer.get("Формула расчета"),
        int(offer.get("Минимальное количество дней", 1)),
        offer.get("Суммируется с программой лояльности", False)
    ))

def main():
    with open(JSON_FILE, 'r', encoding='utf-8') as f:
        offers = json.load(f)

    with psycopg2.connect(**DB_PARAMS) as conn:
        with conn.cursor() as cur:
            for offer in offers:
                for stay_range in offer.get("Даты проживания", []):
                    insert_offer(cur, offer, stay_range)
        conn.commit()
        print("✅ Все спецпредложения успешно записаны в базу данных.")

if __name__ == "__main__":
    main()