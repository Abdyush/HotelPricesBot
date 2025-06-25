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

# Путь к JSON-файлу
JSON_FILE_PATH = 'data.json'

def load_json_data(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)

def insert_data(conn, room_category, date, tariff, price):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO regular_prices (room_category, date, tariff, price)
            VALUES (%s, %s, %s, %s)
        """, (room_category, date, tariff, price))

def main():
    data = load_json_data(JSON_FILE_PATH)

    with psycopg2.connect(**DB_PARAMS) as conn:
        for room_category, dates in data.items():
            for date_str, tariffs in dates.items():
                for tariff, price in tariffs.items():
                    # Пропускаем вложенные словари (например, программа лояльности)
                    if isinstance(price, dict):
                        continue
                    # Конвертация даты
                    date = datetime.strptime(date_str, "%d.%m.%Y").date()
                    insert_data(conn, room_category, date, tariff, price)

        conn.commit()
        print("Данные успешно загружены в базу данных.")

if __name__ == '__main__':
    main()