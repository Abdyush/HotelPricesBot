import csv
import json
import time
import logging
import threading
from selenium import webdriver
from typing import Union, List, Dict
from datetime import datetime, timedelta
from selenium.webdriver.common.by import By
from concurrent.futures import ThreadPoolExecutor
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException


#-------------------------------------------------------------------------- ПОДГОТОВИТЕЛЬНАЯ ЧАСТЬ ---------------------------------------------------------------------------------------
# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
  )

class CsvLogger:
    def __init__(self, path, browser_count):
        self.path = path
        self.lock = threading.Lock()
        self.headers = [f'Браузер {i+1}' for i in range(browser_count)]
        self.rows = []

        # создаём файл и записываем заголовки
        with open(self.path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['Время'] + self.headers)
            writer.writeheader()

    def log(self, browser_id, message):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self.lock:
            # формируем строку лога
            row = {'Время': now, **{header: '' for header in self.headers}}
            row[f'Браузер {browser_id}'] = message

            # записываем строку
            with open(self.path, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=['Время'] + self.headers)
                writer.writerow(row)

#-------------------------------------------------------------------------- ПОДГОТОВИТЕЛЬНАЯ ЧАСТЬ ---------------------------------------------------------------------------------------
#Открываем файл с офферами и загружаем все данные в словарь
with open('data_offers.json', 'r', encoding='utf-8') as file:
    data_offers = json.load(file)

# Ставим начало отчета времени, чтобы засечь время выполнения программы
start_time = time.time()

# Определяем для поиска количество взрослых и возраста детей (возможно это будет не нужно в дальнейшем)
# TODO: Возможно убрать эти значения, так как затратно искать отдельные категории для каждого пользователя
# проще собрать все категории одиин раз и после фильтровать
number_adults = 6


# Инициализируем словарь, в который будем добавлять стоимости, ключами в котором будут названия категорий
data_collection = {}


#-------------------------------------------------------------------------- НАБОР ФУНКЦИЙ ---------------------------------------------------------------------------------------------------
# Вспомогательная функция для получения атрибутов элемента (для процееса поиска необходимых элементов страницы)
def get_attributes(driver, element) -> dict:
    return driver.execute_script(
        """
        let attr = arguments[0].attributes;
        let items = {}; 
        for (let i = 0; i < attr.length; i++) {
            items[attr[i].name] = attr[i].value;
        }
        return items;
        """,
        element
    )
   
# Функция проверяет совпадает ли категория номера с категорией в специальном предложении
# На вход приниммется категория указанная в оффере и категория которую мы достали с сайта
def check_category(offer_category: Union[str, List[str]], name: str) -> bool:
    # Вспомогательная функция, на случай если категорий в оффере окажется несколько и на входи придет список с названиями
    def check_categ(offer_name: str, site_name: str) -> bool:
        offer_name = [x.lower() for x in offer_name.split()]
        # Вернет True если все слова из названия категории оффера будут содержаться 
        # в названии категории с сайта (на случай если разный порядок слов "Премьер делюкс" и "Делюкс премьер")
        return all([x in site_name.lower() for x in offer_name])
    
    if offer_category == 'Все категории' or (offer_category == 'Все виллы' and 'вилла' in name):
        return True
    elif type(offer_category) == list and any([check_categ(x, name) for x in offer_category]):
        return True
    else:
        return False

# Функция проверяющая подходит ли дата бронирования под период специального предложения
# Принимает на вход список список со списками дат, в каждом списке две даты, начало периода спец предложения и конец,
# несколько списков получается потому, что в специальном предложении могу быть несколькно периодов
def check_date(living_dates: List[List[str]], date: datetime) -> bool:
    for dates in living_dates:
        start_date = datetime.strptime(dates[0], '%d.%m.%Y')
        end_date = datetime.strptime(dates[1], '%d.%m.%Y')
        
        if start_date <= date <= end_date:
            return True
    return False

# Функция проверяющая попадает ли сегодняшний день, под период бронирования номеров по специальному предложению
def check_booking_date(booking_dates: list[str]) -> bool:
    date = datetime.now()
    for dates in booking_dates:
        start_date = datetime.strptime(dates[0], '%d.%m.%Y')
        end_date = datetime.strptime(dates[1], '%d.%m.%Y')
        
        if start_date <= date <= end_date:
            return True
    return False

# Функция применяющая скидку к стоимости за сутки полученные с сайта, и возвращающая словарь с новыми ценами по тарифам 
# "полный пансион" и "только завтраки"
def recalculate_cost(name: str, formula: str, only_breakfast: int, full_pansion: int) -> Dict[str, int]:
    # Убираем N из формулы (возможно стоит ее сразу формировать без N)
    formula = formula.replace('N = ', '')
    # Заменяем C на его значение и выполняем формулу
    discount_only_breakfast = eval(formula.replace("C", str(only_breakfast)))
    discount_full_pansion = eval(formula.replace("C", str(full_pansion)))
    
    discounted_prices = {f'{name}: только завтраки': int(discount_only_breakfast),
                         f'{name}: полный пансион': int(discount_full_pansion),}
    
    return discounted_prices

# Функция считающая скидку по программе лояльности, принимает на вход текущиие стоимости с сайта и названия оффера на случай,
# если спецпредложение суммируется с программой лояльности и возвращающая словарь ключами которого являются статусы программы лояльности,
# а их значения словари с новыми ценами по тарифам "полный пансион" и "только завтраки" и указанием названия спецпредложения, если онно суммировалось
def count_loyalty_discount(only_breakfast: int, full_pansion: int, offer: str=None) -> Dict[str, int]:
    # словарь с названиями статусов и соответсвующими скидками к стоимости
    discount_dict = {'white': 5,
                     'bronze': 7,
                     'silver': 8,
                     'gold': 10,
                     'platinum': 12,
                     'diamond': 15}
    
    loyalty_dict = {}
    # просчитываем скидку к обоим тарифам по всем статусам
    for stat, percent in discount_dict.items():
        breakfast_discounted = only_breakfast * (1 - percent / 100)
        pansion_discounted = full_pansion * (1 - percent / 100)
        # и добавляем в словарь
        if offer:
            loyalty_dict[stat] = {f'{offer}: только завтраки': int(breakfast_discounted),
                                  f'{offer}: полный пансион': int(pansion_discounted)}
        else:
            loyalty_dict[stat] = {f'только завтраки': int(breakfast_discounted),
                                  f'полный пансион': int(pansion_discounted)}
        
    return loyalty_dict

# Функция принимающая на вход дату, словарь с найденными датами, и тип выезд/заезд и находит соответсвующую дате кнопку    
def find_date_btn(dt: datetime, dates_dict: Dict, procedure: str) -> WebElement:
    dt = dt.strftime('%d.%m.%y')
    # Преобразование строки в объект datetime
    if procedure == 'arrival':
        date_object = datetime.strptime(dt, "%d.%m.%y")
    elif procedure == 'checkout':
        date_object = datetime.strptime(dt, "%d.%m.%y") + timedelta(days=1)
    # Извлечение номера года и месяца а также дня
    y_m = date_object.strftime("%Y-%m")  
    d = date_object.day        
    # Находим кнопку с соответсвующим числом в словаре     
    date_btn = [num for num in dates_dict[y_m] if num.text == str(d)][0]
    
    return date_btn

# Функция принимающая на вход элемент - рамку в которой содержаться кнопки с датами, и формирует словарь где ключи - месяц, а значения - список номеров дней

def find_dates(frame: WebElement) -> Dict:
    wait = WebDriverWait(frame, 10)

    try:
        # Ждём появления нужного блока
        frame2 = wait.until(EC.presence_of_element_located((By.XPATH, ".//div[@data-mode]")))
    except TimeoutException:
        logging.error("Не удалось найти блок с data-mode внутри переданного frame")
        raise

    months = frame2.find_elements(By.XPATH, './/div[@data-month]')
    if len(months) < 2:
        logging.warning("Найдено меньше двух месяцев в календаре — возможно, DOM изменился")
        raise Exception("Недостаточно блоков с месяцами")

    data_months = [el.get_attribute("data-month") for el in months]
    dates = {
        data_months[0][:7]: [d for d in months[0].find_elements(By.XPATH, './/span') if d.text.isdigit()],
        data_months[1][:7]: [d for d in months[1].find_elements(By.XPATH, './/span') if d.text.isdigit()],
    }

    return dates

def find_categories(browser):
    selected_buttons = []
    while True:
        # Определяем переменную start со значением - длиной списка selected_buttons (в начале он пустой)
        start = len(selected_buttons)
        # находим на всех карточках с категориями номеров, кнопки "выбрать" и формируем во временный список
        temp_list = [x for x in browser.find_elements(By.CLASS_NAME, 'tl-btn') if x.text != '']
        # добавляем временный список в список selected_buttons и формируем множество уникальных элементов
        selected_buttons = set(selected_buttons).union(set(temp_list))
        # Определяем переменную end со значением - длиной списка selected_buttons
        end = len(selected_buttons)
        # спим 3 секунды для загрузки
        # TODO: Оптимизировать ожидание
        #time.sleep(3)
        # если перемнная длина списка в начале совпала с длиной списка в конце, значит новые кнопки "выбрать" на странице закончились
        if start == end:
            # временный список становиться переменной selected_buttons (не помню почему), и цикл завершается
            selected_buttons = temp_list
            break
        # если длины разные, значит скроллим страницу до последнего элемента во временном списке и продолжаем поиск в цикле
        browser.execute_script("return arguments[0].scrollIntoView(true);", temp_list[-1])
        # спим 3 секунды для загрузки
        # TODO: Оптимизировать ожидание
        time.sleep(1)
        # Этот процесс поиска доступных кнопок, приходиться каждый раз потворять в цикле, потому что с каждым возвратом на траницу с карточками,
        # кнопки и их идентификаторы обновляются, это связано с тем, что в любой момент, определенная категория может стать недоступной для бронирования
   
    return selected_buttons 

def find_categories1(browser, date, timeout=10):
    try:
        seen_locations = set()
        all_buttons = []

        while True:
            # Ожидаем появления хотя бы одной кнопки
            WebDriverWait(browser, timeout).until(
                EC.presence_of_element_located((By.CLASS_NAME, 'tl-btn'))
            )

            # Ищем все доступные кнопки
            current_buttons = [
                btn for btn in browser.find_elements(By.CLASS_NAME, 'tl-btn') if btn.text.strip()
            ]

            # Добавляем только новые кнопки, отслеживаем их по позиции (location)
            new_buttons = []
            for btn in current_buttons:
                try:
                    loc = btn.location_once_scrolled_into_view  # более надёжно в headless
                    loc_tuple = (loc['x'], loc['y'])
                    if loc_tuple not in seen_locations:
                        seen_locations.add(loc_tuple)
                        new_buttons.append(btn)
                except Exception as e:
                    logging.warning(f"Не удалось получить location кнопки: {e}")
                    continue

            if not new_buttons:
                break

            all_buttons.extend(new_buttons)

            # Скроллим к последней новой кнопке
            try:
                browser.execute_script("arguments[0].scrollIntoView({block: 'center'});", new_buttons[-1])
            except Exception as e:
                logging.warning(f"Не удалось проскроллить: {e}")
                break

            # Явное ожидание появления новых элементов
            time.sleep(0.7)

        logging.info(f"Дата {date.strftime('%d.%m.%Y')}: найдено {len(all_buttons)} уникальных кнопок 'Выбрать'")
        return all_buttons

    except Exception as e:
        logging.exception(f"Ошибка при поиске категорий на {date.strftime('%d.%m.%Y')}: {e}")
        return []


#------------------------------------------------------------------- ПАРСЕР НАЗВАНИЙ КАТЕГОРИЙ И ЦЕН С САЙТА ОТЕЛЯ -------------------------------------------------------------------------

#--------------------------------------------------------------------- Процесс нахождения нужного контенейра -------------------------------------------------------------------------------
# Определяем весь код ниже, в функцию
def pars(data_collection, start_period, logger, browser_id):
    # Задаем настройки для вебдрайвера
    options = webdriver.ChromeOptions()
    #options.add_argument("--headless")  # можно убрать, если нужен визуальный режим
    #options.add_argument('--window-size=1920x1080')
    #options.add_argument("--disable-gpu")
    #options.add_argument("--no-sandbox")
    # Запускаем webdraiwer selenium под именем "browser"
    with webdriver.Chrome(options=options) as browser:
        try:
            logger.log(browser_id, f"Начало парсинга с {start_period.strftime('%d.%m.%Y')}")
            browser.get('https://mriyaresort.com/booking/')
            time.sleep(5)
            # Явное ожидание элемента .block--content
            wait = WebDriverWait(browser, 15)
            frame = wait.until(EC.presence_of_element_located((By.CLASS_NAME, 'block--content')))
            logging.info("Найден блок .block--content")
            logger.log(browser_id, "Найден блок .block--content")
            browser.execute_script("arguments[0].scrollIntoView(true);", frame)

            try:
                el = frame.find_element(By.ID, 'tl-booking-form')
                logging.info("Найдена форма бронирования")
                logger.log(browser_id, "Найдена форма бронирования")
            except NoSuchElementException:
                logging.error("Форма бронирования не найдена в блоке .block--content")
                logger.log(browser_id, "Форма бронирования не найдена в блоке .block--content")
                return

            # Ждём iframe внутри формы
            wait = WebDriverWait(el, 10)
            iframes = wait.until(EC.presence_of_all_elements_located((By.TAG_NAME, 'iframe')))

            if len(iframes) < 2:
                logging.error("Ожидалось минимум два iframe, найдено: %d", len(iframes))
                logger.log(browser_id, "Ожидалось минимум два iframe, найдено: %d", len(iframes))
                return

            iframe = iframes[1]
            browser.switch_to.frame(iframe)
            logging.info("Переключение на iframe выполнено")
            logger.log(browser_id, "Переключение на iframe выполнено")
            time.sleep(3)
            # Ожидание контейнера внутри iframe
            container = WebDriverWait(browser, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, 'page-container'))
            )
            logging.info("Контейнер .page-container успешно загружен")
            logger.log(browser_id, "Контейнер .page-container успешно загружен")
            
            logging.info("Поиск кнопок выбора количества гостей")
            logger.log(browser_id, "Поиск кнопок выбора количества гостей")
            time.sleep(3)
            select_amount = container.find_elements(By.CLASS_NAME, 'x-select__match-icon')
            if not select_amount:
                logging.warning("Кнопки выбора количества гостей не найдены")
                logger.log(browser_id, "Кнопки выбора количества гостей не найдены")
                return

            adults = select_amount[0]  # первая — взрослые

            if number_adults != 2:
                logging.info(f"Выбор {number_adults} взрослых")
                logger.log(browser_id, f"Выбор {number_adults} взрослых")
                adults.click()
                try:
                    list(filter(lambda x: int(x.text.split(' ')[0]) == number_adults, container.find_elements(By.XPATH, '//div[@class="x-sd__scroll"]//div[@class="x-sd__choice"]')))[0].click()
                    logging.info(f"Выбрано взрослых: {number_adults}")
                    logger.log(browser_id, f"Выбрано взрослых: {number_adults}")
                except:
                    logging.warning(f"Ошибка при выборе взрослых")
                    logger.log(browser_id, f"Ошибка при выборе взрослых")

            # Поиск и нажатие на кнопку "Найти"
            logging.info("Поиск кнопки 'Найти'")
            logger.log(browser_id, "Поиск кнопки 'Найти'")
            time.sleep(3)
            buttons = container.find_elements(By.TAG_NAME, 'span')
            if buttons:
                buttons[-1].click()
                logging.info("Кнопка 'Найти' нажата")
                logger.log(browser_id, "Кнопка 'Найти' нажата")
            else:
                logging.warning("Кнопка 'Найти' не найдена")
                logger.log(browser_id, "Кнопка 'Найти' не найдена")

           
        except TimeoutException as e:
            logging.error("Ошибка ожидания элемента: %s", e)
            logger.log(browser_id, "Ошибка ожидания элемента: %s", e)
        except Exception as e:
            logging.exception("Необработанное исключение: %s", e)
            logger.log(browser_id, "Необработанное исключение: %s", e)
        time.sleep(3)
       
        #----------------------------------------------------------------- ПОИСК НА СТРАНИЦЕ КАРТОЧЕК С КАТЕГОРИЯМИ НОМЕРОВ --------------------------------------------------------------------
        
        # Инициализируем пустой список в который будут добавляться кнопки "выбрать" из карточек с категориями
        selected_buttons = set()
        # в переменную date определяем начало периода
        date = start_period
        # определяем количество дней на которые будет выполняться поиск номеров и запускаем по нему цикл
        count_days = 10
        
        for _ in range(count_days):
            try:
                logging.info(f"Выбор даты: {date.strftime('%d.%m.%Y')}")
                logger.log(browser_id, f"Выбор даты: {date.strftime('%d.%m.%Y')}")
                input_date = browser.find_element(By.CLASS_NAME, 'x-hcp__text-field')
                input_btn = input_date.find_element(By.TAG_NAME, 'input')
                browser.execute_script("arguments[0].scrollIntoView(true);", input_btn)
                wait.until(EC.element_to_be_clickable(input_btn))
                browser.execute_script("arguments[0].click();", input_btn)
                logging.info(f"Кнопка с выбором дат найдена, и кликнута")
                logger.log(browser_id, f"Кнопка с выбором дат найдена, и кликнута")
                frame1 = browser.find_element(By.CLASS_NAME, 'x-modal__container')
                logging.info(f"Рамка выбора дат найдена")
                logger.log(browser_id, f"Рамка выбора дат найдена")
                try:
                    dates = find_dates(frame1)
                except:
                    logging.warning(f"dates не найдены, пробуем снова")
                    logger.log(browser_id, f"dates не найдены, пробуем снова")
                    time.sleep(3)
                    dates = find_dates(frame1)
                logging.info(f"Кнопки с числами месяца найдены, и сформированы в словарь")
                logger.log(browser_id, f"Кнопки с числами месяца найдены, и сформированы в словарь")
                try:
                    arrival_btn = find_date_btn(date, dates, 'arrival')
                    wait.until(EC.element_to_be_clickable(arrival_btn)).click()
                    logging.info(f"Кнопка заезда кликнута")
                    logger.log(browser_id, f"Кнопка заезда кликнута")
                except Exception:
                    logging.warning("Кнопка заезда не найдена с первого раза, повторяем попытку...")
                    logger.log(browser_id, "Кнопка заезда не найдена с первого раза, повторяем попытку...")
                    dates = find_dates(frame1)
                    logging.info(f"Кнопки с числами месяца повторно найдены, и сформированы в словарь")
                    logger.log(browser_id, f"Кнопки с числами месяца повторно найдены, и сформированы в словарь")
                    arrival_btn = find_date_btn(date, dates, 'arrival')
                    wait.until(EC.element_to_be_clickable(arrival_btn)).click()
                    logging.info(f"Кнопка заезда кликнута")
                    logger.log(browser_id, f"Кнопка заезда кликнута")

                try:
                    checkout_btn = find_date_btn(date, dates, 'checkout')
                    wait.until(EC.element_to_be_clickable(checkout_btn)).click()
                    logging.info(f"Кнопка выезда кликнута")
                    logger.log(browser_id, f"Кнопка выезда кликнута")
                except Exception:
                    logging.warning("Кнопка выезда не найдена с первого раза, повторяем попытку...")
                    logger.log(browser_id, "Кнопка выезда не найдена с первого раза, повторяем попытку...")
                    dates = find_dates(frame1)
                    logging.info(f"Кнопки с числами месяца повторно найдены, и сформированы в словарь")
                    logger.log(browser_id, f"Кнопки с числами месяца повторно найдены, и сформированы в словарь")
                    checkout_btn = find_date_btn(date, dates, 'checkout')
                    wait.until(EC.element_to_be_clickable(checkout_btn)).click()
                    logging.info(f"Кнопка выезда кликнута")
                    logger.log(browser_id, f"Кнопка выезда кликнута")

                time.sleep(4)

            except Exception as e:
                logging.exception(f"Ошибка при обработке даты {date.strftime('%d.%m.%Y')}: {e}")
                logger.log(browser_id, f"Ошибка при обработке даты {date.strftime('%d.%m.%Y')}: {e}")
                
            logging.info(f"Поиск доступных карточек на дату {date.strftime('%d.%m.%y')}")
            logger.log(browser_id, f"Поиск доступных карточек на дату {date.strftime('%d.%m.%y')}")
    
            
            selected_buttons = find_categories(browser)
            ran = len(selected_buttons)
            logging.info(f"на дату: {date.strftime('%d.%m.%Y')}, найдено доступных категорий: {ran}")
            logger.log(browser_id, f"на дату: {date.strftime('%d.%m.%Y')}, найдено доступных категорий: {ran}")
            for i in range(ran):
                time.sleep(3)  # поменял на секунду меньше
                try:
                    btn = selected_buttons[i]
                    logging.info(f"Обработка карточки №{i+1}")
                    logger.log(browser_id, f"Обработка карточки №{i+1}")
                    browser.execute_script("arguments[0].scrollIntoView(true);", btn)
                    WebDriverWait(browser, 10).until(EC.element_to_be_clickable(btn))
                    logging.info(f"кнопка №{i+1} стала кликабельной")
                    logger.log(browser_id, f"кнопка №{i+1} стала кликабельной")

                    try:
                        parent_div = btn.find_element(By.XPATH, './/ancestor::div[@data-shift-animate="true"]')
                        child_div = parent_div.find_element(By.XPATH, './/div[@title="Остался 1 номер"]')
                        remainder = child_div.get_attribute("title")
                        logging.info(f"Карточка №{i+1}: {remainder}")
                        logger.log(browser_id, f"Карточка №{i+1}: {remainder}")
                    except NoSuchElementException:
                        logging.info(f"Карточка №{i+1}: без пометки 'Остался 1 номер'")
                        logger.log(browser_id, f"Карточка №{i+1}: без пометки 'Остался 1 номер'")

                    browser.execute_script("arguments[0].click();", btn)
                    logging.info(f"Карточка №{i+1}: успешно выбрана, спим 4 секунды")
                    logger.log(browser_id, f"Карточка №{i+1}: успешно выбрана, спим 4 секунды")
                    time.sleep(4)
                    
                    
                
                except (StaleElementReferenceException, NoSuchElementException, TimeoutException) as e:
                    logging.warning(f"Карточка №{i+1} недоступна: {e}")
                    logger.log(browser_id, f"Карточка №{i+1} недоступна: {e}")
                    continue
                except Exception as e:
                    logging.error(f"Непредвиденная ошибка при обработке карточки №{i+1}: {e}")
                    logger.log(browser_id, f"Непредвиденная ошибка при обработке карточки №{i+1}: {e}")
                    continue    
                
                
                #------------------------------------------------------------ СБОР ДАННЫХ С КАРТОЧЕК -----------------------------------------------------------------------------------
                try:
                    # достаем название категории посредством поиска всех элемнтов с тегом div и атрибутом tl-id="plate-title" и выбираем первый из них,
                    # это будет являеться названием категории (способ не надежный)
                    # TODO: Оптимизировать поиск
                    name = [x.text for x in browser.find_elements(By.CSS_SELECTOR, 'div[tl-id="plate-title"]') if x.text  != ''][0]
                    if not name:
                        logging.warning("Название категории не найдено")
                        logger.log(browser_id, "Название категории не найдено")
                    # достаем цены, находя с помощью css селектора все элементы span с классом numeric, удаляем лишнее форматирование и преобзауем в числа
                    prices = [int(x.text.replace('\u2009', '')) for x in browser.find_elements(By.CSS_SELECTOR, 'span[class="numeric"]') if x.text  != '']
                    if len(prices) < 2:
                        logging.warning(f"Недостаточно данных о тарифах для категории {name}")
                        logger.log(browser_id, f"Недостаточно данных о тарифах для категории {name}")
                    # первые два элемента этого списка будут стоимости без скидок по специальным предложениям, по тарифам "только завтраки" и "полный пансион"
                    # способ поиска можно сделать вернее, если найти также названия тарифов
                    # TODO: Оптимизировать поиск
                    only_breakfast = prices[0]
                    full_pansion = prices[1]
                    
                    logging.debug(f"Цены: Завтрак — {only_breakfast}, Полный пансион — {full_pansion}")
                    logger.log(browser_id, f"Цены: Завтрак — {only_breakfast}, Полный пансион — {full_pansion}")
                    
                    # Просчитываем скидку по всем статусам программы лояльности и формируем стоимости в словарь
                    loyalty_discont_dict = count_loyalty_discount(only_breakfast, full_pansion)
                    logging.info(f"Рассчитаны цены по программе лояльности")
                    logger.log(browser_id, f"Рассчитаны цены по программе лояльности")
                    
                    # Формируем словарь с ценами без скидок 
                    date_dict = {'только завтраки': only_breakfast,
                                'полный пансион': full_pansion}
                    
                    # По полученным в модуле special_offers данным по специальным предложениям, запускаем цикл по каждой акции и проверяем по всем параметрам
                    # можно ли применить скидку к текущим стоимостям
                    for offer in data_offers:
                        try:
                            # проверяем соответствие категорий
                            if check_category(offer["Категория"], name):
                                # проверяем соответсвие дат проживания
                                if check_date(offer['Даты проживания'], date):
                                    # проверяем соответсвие дат бронирования
                                    if check_booking_date(offer['Даты бронирования']):
                                        # Расчитываем стоимости со скидкой по спец предложению
                                        discounted_prices = recalculate_cost(offer['Название'], offer['Формула расчета'], only_breakfast, full_pansion)
                                        # добавляем цены в словарь
                                        date_dict.update(discounted_prices)
                                        # если предложение суммируется с программой лояльности, рассчитываем соответсвующие скидки
                                        if offer['Суммируется с программой лояльности']:
                                            loyalty_offer_dict = count_loyalty_discount(list(discounted_prices.values())[0], list(discounted_prices.values())[1], offer['Название'])
                                            # добавляем их в словарь с пометками о спец предложениях в названиях тарифов
                                            for key in loyalty_discont_dict:
                                                loyalty_discont_dict[key].update(loyalty_offer_dict[key])
                                            logging.info(f"Применено спецпредложение + лояльность: {offer['Название']}")
                                            logger.log(browser_id, f"Применено спецпредложение + лояльность: {offer['Название']}")
                                        else:
                                            logging.info(f"Применено спецпредложение: {offer['Название']}")
                                            logger.log(browser_id, f"Применено спецпредложение: {offer['Название']}")
                                            
                        except Exception as e:
                            logging.warning(f"Ошибка при обработке предложения {offer.get('Название', '')}: {e}")
                            logger.log(browser_id, f"Ошибка при обработке предложения {offer.get('Название', '')}: {e}")
                            
                    # Добавляем в словарь с ценами, по ключу "программа лояльности" словрь с ценами по программе лояльности и если есть, спец предложениями вместе с п.л.                     
                    date_dict['программа лояльности'] = loyalty_discont_dict
                    
                    # Если названия категории нет в большом словаре с данными, добавляем пустой словарь по ключу "каатегория"
                    if name not in data_collection:
                        data_collection[name] = {}
                    
                    # Добавляем в большой словарь с данным, по ключу - категории данные, где в каждой категории будут храниться даты, и соответсвуеющие цены по ним
                    data_collection[name][date.strftime('%d.%m.%Y')] = date_dict
                    logging.info(f"Успешно добавлены данные по категории '{name}' на дату {date.strftime('%d.%m.%Y')}")
                    logger.log(browser_id, f"Успешно добавлены данные по категории '{name}' на дату {date.strftime('%d.%m.%Y')}")
                
                except NoSuchElementException as e:
                    logging.error(f"Ошибка поиска элемента: {e}")
                    logger.log(browser_id, f"Ошибка поиска элемента: {e}")
                except IndexError as e:
                    logging.error(f"Ошибка доступа по индексу: {e}")
                    logger.log(browser_id, f"Ошибка доступа по индексу: {e}")
                except ValueError as e:
                    logging.error(f"Ошибка преобразования данных: {e}")
                    logger.log(browser_id, f"Ошибка преобразования данных: {e}")
                except Exception as e:
                    logging.exception(f"Неизвестная ошибка при обработке карточки: {e}")    
                    logger.log(browser_id, f"Неизвестная ошибка при обработке карточки: {e}")
                
                # Находим кнопку возврата к выбору карточек кликаем по ней и ожидаем 3 секунды для загрузки
                back = browser.find_element(By.CLASS_NAME, 'x-hnp__link')
                logging.info("Кнопка возврата с выбору категорий найдена")
                logger.log(browser_id, "Кнопка возврата с выбору категорий найдена")
                WebDriverWait(browser, 10).until(EC.element_to_be_clickable(back))
                browser.execute_script("arguments[0].click();", back)
                # TODO: Оптимизировать ожидание
                time.sleep(3)
                selected_buttons = find_categories(browser)
                    
            # Прибавляем к дате один день
            date += timedelta(days=1)
         
        logger.log(browser_id, f"Завершён парсинг с {start_period.strftime('%d.%m.%Y')}")   

base_date = datetime.now() # Текущая дата
logger = CsvLogger('log.csv', browser_count=9)
futures = []
    
with ThreadPoolExecutor(max_workers=9) as executor:
    for i in range(9):
        start_date = base_date + timedelta(days=i * 10)
        futures.append(executor.submit(pars, data_collection, start_date, logger, i + 1))

    for future in futures:
        try:
            result = future.result()  # Получаем результат выполнения функции
        except Exception as e:
            print(f"Ошибка: {e}")


#-------------------------------------------------------------- ЗАПИСЬ ДАННЫХ В ФАЙЛ И ЗАВЕРШЕНИЕ РАБОТЫ -----------------------------------------------------------------------------------        
# Запись большой словрь с данными в JSON файл
with open('data.json', 'w', encoding='utf-8') as json_file:
    json.dump(data_collection, json_file, ensure_ascii=False, indent=4)  

# Конец отсчета времени
end_time = time.time()
execution_time = end_time - start_time

# Преобразование времени выполнения в минуты и секунды
minutes = int(execution_time // 60)
seconds = int(execution_time % 60)

# Выводим в консль отчет о выполнении программы и итоговое время
print("Данные успешно сохранены в data.json")
print(f"Время выполнения программы: {minutes} минут {seconds} секунд")