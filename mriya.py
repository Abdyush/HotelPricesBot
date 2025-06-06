import time
import json
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from datetime import datetime, timedelta
from typing import Union, List, Dict


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
number_teens = []

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
    

#------------------------------------------------------------------- ПАРСЕР НАЗВАНИЙ КАТЕГОРИЙ И ЦЕН С САЙТА ОТЕЛЯ -------------------------------------------------------------------------

#--------------------------------------------------------------------- Процесс нахождения нужного контенейра -------------------------------------------------------------------------------
# Запускаем webdraiwer selenium под именем "browser"
with webdriver.Chrome() as browser:
    # Загружаем страницу официального сайта Мрии с формой бронирования
    browser.get('https://mriyaresort.com/booking/')
    # Спим 5 секунд 
    # TODO: Оптимизировать ожидание
    time.sleep(5)
    # Находим элемент "block--content", рамка в которой находятся кнопка "Найти" 
    # и кнопки для выбора количества взрослых и детей (Как подстраховаться на случай если название класса измениться?)
    # TODO: добавить обработчик исключений
    frame = browser.find_element(By.CLASS_NAME, 'block--content')
    # скроллим к найденной рамке
    browser.execute_script("return arguments[0].scrollIntoView(true);", frame)
    # в рамке находим форму бронирования
    # TODO: добавить обработчик исключений
    el = frame.find_element(By.ID, 'tl-booking-form')
    # в форме бронирования находим iframe и переключаемся на него, (что если добавится еще один iframe и нужный нам сместится по индексу?)
    # TODO: оптимизировать поиск элемента
    iframe = el.find_elements(By.TAG_NAME, 'iframe')[1]
    browser.switch_to.frame(iframe)
    # только сейчас находим контейнер, в котором можно взаимодействовать с кнопками, и спим 3 секунды для загрузки страницы
    container = browser.find_element(By.CLASS_NAME, 'page-container')
    # TODO: Оптимизировать ожидание
    time.sleep(3)
  
    # TODO: Оптимизировать раздел 
    #------------------------------------------------------ Работа с выбором количества гостей (скорее всего придется удалить этот раздел) -----------------------------------------------
    # В контейнере находим список кнопок с классом 'x-select__match-icon'
    select_amount = container.find_elements(By.CLASS_NAME, 'x-select__match-icon')
    # первая из кнопок это выбор взрослых
    # TODO: Оптимизировать поиск
    adults = select_amount[0]
    # Если взрослых не 2, тогда кликаем на кнопку с выпадающим списком (так как по умолчанию в поле установлено 2 взрослых)
    if number_adults != 2:
        adults.click()
        # В данной строке мы находим все элементы выпадающего списка со взросылми по xpath, и фильтруем этот список по совпадению изначально заданного количества взрослых,
        # в переменной number_adults, с текстом элементов выпадающего списка, в которых указаны циры количества, и сразу кликаем на данный элемент.
        adults_amount = list(filter(lambda x: int(x.text.split(' ')[0]) == number_adults, container.find_elements(By.XPATH, '//div[@class="x-sd__scroll"]//div[@class="x-sd__choice"]')))[0].click()
    # спим 1 секунду для загрузки
    # TODO: Оптимизировать ожидание
    time.sleep(1)
    
    # Если список с возрастами детей не пуст, в цикле проходим по каждому возрасту списка
    # TODO: Оптимизировать раздел 
    if number_teens != []:
        for age in number_teens:
            # По xpath находим все возможные кнопки которую предположительно можно нажать для выброра детей по возрастам
            teens = container.find_elements(By.XPATH, '//div[@class="p-search-filter__children"]//div[@class="x-select__match-icon"]')
            # Цикле пытаемся нажать на кнопку , если получается останавливаем цикл
            for btn in teens:
                try:
                    btn.click()
                    break
                except:
                    continue
            # находим все элементы выпадающего списка с возрастами детей по xpath
            teens_amount = container.find_elements(By.XPATH, '//div[@class="x-sd__scroll"]//div[@class="x-sd__choice"]')
            # Если ребенку 0 лет, то нажимаем первую кнопку списка, так как там явно не указано "0 лет" а написано "ребенок до 1 года"
            if age == 0:
                teens_amount[0].click()
            # в остальных случаях фильтруем и нажимаем аналогично выбору взрсолых
            else:
                teen_btn = list(filter(lambda x: int(x.text.replace('\u2002', ' ').split(' ')[-2]) == age, teens_amount))[0].click()
                
            # спим 2 секунды для загрузки
            # TODO: Оптимизировать ожидание
            time.sleep(2)
    
    # после завершения циклов спим 3 секунды для загрузки
    # TODO: Оптимизировать ожидание         
    time.sleep(3) 
    
    # Далее находим все кнопки с тегом span, и нажимаем на последнюю в списке, это кнопка "Найти"
    # TODO: Оптимизировать поиск
    buttons = container.find_elements(By.TAG_NAME, 'span')
    buttons[-1].click()
    
    # спим 5 секунд для загрузки
    # TODO: Оптимизировать ожидание   
    time.sleep(5)
    
    
    #----------------------------------------------------------------- ПОИСК НА СТРАНИЦЕ КАРТОЧЕК С КАТЕГОРИЯМИ НОМЕРОВ --------------------------------------------------------------------
    
    # Инициализируем пустой список в который будут добавляться кнопки "выбрать" из карточек с категориями
    selected_buttons = []
    # В переменной date устанавливаем сегодняшнюю дату
    date = datetime.now()
    # определяем количество дней на которые будет выполняться поиск номеров и запускаем по нему цикл
    count_days = 1
    
    for i in range(count_days):
        #------------------------------------------------- Работа с переключением дат (нужен ли этот раздел в цикле?) ----------------------------------------------------------------------
        # Находим рамку с классом 'x-hcp__text-field'
        input_date = browser.find_element(By.CLASS_NAME, 'x-hcp__text-field')
        # В рамке находим кнопку для выбора даты
        # TODO: Оптимизировать поиск
        input_btn = input_date.find_element(By.TAG_NAME, 'input')
        # Скроллим к этой кнопке
        browser.execute_script("return arguments[0].scrollIntoView(true);", input_btn)
        # Ожидаем до 10 секунд пока кнопка не станет кликабельной
        WebDriverWait(browser, 10).until(EC.element_to_be_clickable(input_btn))
        # Кликаем на кнопку при помощи метода execute_script
        browser.execute_script("arguments[0].click();", input_btn)
        # после завершения циклов спим 3 секунду для загрузки
        # TODO: Оптимизировать ожидание  
        time.sleep(3)
        # Находим рамку, последнюю до которой удалось дойти в структуре
        frame1 = browser.find_element(By.CLASS_NAME, 'x-modal__container')
        # спим 3 секунды для загрузки
        # TODO: Оптимизировать ожидание   
        time.sleep(5)
        # И в этой рамке по xpath находим все кнопки 
        dates = frame1.find_elements(By.XPATH, '//div[@data-initialized]//span')
        # Даллее в список numeric_dates добавляем только те кнопки, которые имеют текст - цифру, тоесть находим ввсе кнопки с датами
        numeric_dates = [date for date in dates if date.text.isdigit()]
        
        # Получаем сегодняшний день месяца в формате строки
        today_date = str(int(datetime.now().strftime('%d')))
        # Находим индекс элемента с сегодняшним днем месяца в списке с текстовыми элементами дат
        start_index = next((i for i, date in enumerate(numeric_dates) if date.text == today_date), None)
        # Обрезаем список с датами начиная с сегодняшняшней даты
        numeric_dates = numeric_dates[start_index:]
        # Для каждого текстового элемента с числом месяца, находим родительский элемент, это и будет кнопка с которой можно взаимодействовать
        # и формируем из них список
        parent_elements = [date.find_element(By.XPATH, './..') for date in numeric_dates]
        
        # Кликаем на элемент по индексу цикла, в котором указано необходимое количество дней для просмотра
        # Для выбора даты заезда
        parent_elements[i].click()
        # спим 1 секунду для загрузки
        # TODO: Оптимизировать ожидание
        time.sleep(1)
        # Прибавляем к индексу один день и кликаем по кнопке для выбора даты выезда
        parent_elements[i + 1].click()
        # спим 3 секунды для загрузки
        # TODO: Оптимизировать ожидание
        time.sleep(3)
        
        #-------------------------------------------------- Цикл сбора всех карточек с досткпными для бронирования категориями ----------------------------------------------------------------
        # Запускаем бесконечный цикл в котором находяться кнопки выбора категории, добавляются в множество и страница скроллится вниз до тех пор,
        # пока не закончаться новые элементы
        
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
            time.sleep(3)
            # если перемнная длина списка в начале сопала с длиной списка в конце, значит новые кнопки "выбрать" на странице закончились
            if start == end:
                # временный список становиться переменной selected_buttons (не помню почему), и цикл завершается
                selected_buttons = temp_list
                break
            # если длины разные, значит скроллим страницу до последнего элемента во временном списке и продолжаем поиск в цикле
            browser.execute_script("return arguments[0].scrollIntoView(true);", temp_list[-1])
            # спим 3 секунды для загрузки
            # TODO: Оптимизировать ожидание
            time.sleep(3)
            # Этот процесс поиска доступных кнопок, приходиться каждый раз потворять в цикле, потому что с каждым возвратом на траницу с карточками,
            # кнопки и их идентификаторы обновляются, это связано с тем, что в любой момент, определенная категория может стать недоступной для бронирования
        
        # Определяем итоговую длину списка с доступными категориями
        ran = len(selected_buttons)
        # и двигаемся в цикле по тому же количеству итераций, которое мы определили при поиске элементов 
        # TODO: Оптимизировать раздел (здесь могут возникать неточности, и как следствие ошибки, потому что после того как мы определили
        # конечную длину списка с доступными элементами, они могут уменьшаться а программа, продолжая двигаться по заданным итерациям, выдаст ошибку 'index out of range')
        for i in range(ran):
            #Создаем словарь с ключем - датой, и значением - словарем с ценами
            date_dict = {}
            # спим 5 секунд для загрузки
            # TODO: Оптимизировать ожидание
            time.sleep(5)
            # кнопка определяется индексом (итерацией в цикле) списка со всеми кнопками
            btn = selected_buttons[i]
            # скроллим к ней
            browser.execute_script("return arguments[0].scrollIntoView(true);", btn)
            # дожидаемся пока она станет кликабельной
            WebDriverWait(browser, 10).until(EC.element_to_be_clickable(btn))
            # кликаем по кнопке, при помощи метода execute_script
            browser.execute_script("arguments[0].click();", btn)
            # спим 4 секунды для загрузки
            # TODO: Оптимизировать ожидание
            time.sleep(4)
            
            
            #------------------------------------------------------------ СБОР ДАННЫХ С КАРТОЧЕК -----------------------------------------------------------------------------------
            # достаем название категории посредством поиска всех элемнтов с тегом div и атрибутом tl-id="plate-title" и выбираем первый из них,
            # это будет являеться названием категории (способ не надежный)
            # TODO: Оптимизировать поиск
            name = [x.text for x in browser.find_elements(By.CSS_SELECTOR, 'div[tl-id="plate-title"]') if x.text  != ''][0]
            # достаем цены, находя с помощью css селектора все элементы span с классом numeric, удаляем лишнее форматирование и преобзауем в числа
            prices = [int(x.text.replace('\u2009', '')) for x in browser.find_elements(By.CSS_SELECTOR, 'span[class="numeric"]') if x.text  != '']
            # первые два элемента этого списка будут стоимости без скидок по специальным предложениям, по тарифам "только завтраки" и "полный пансион"
            # способ поиска можно сделать вернее, если найти также названия тарифов
            # TODO: Оптимизировать поиск
            only_breakfast = prices[0]
            full_pansion = prices[1]
            
            # Просчитываем скидку по всем статусам программы лояльности и формируем стоимости в словарь
            loyalty_discont_dict = count_loyalty_discount(only_breakfast, full_pansion)
            
            # Формируем словарь с ценами без скидок 
            date_dict = {'только завтраки': only_breakfast,
                         'полный пансион': full_pansion}
            
            # По полученным в модуле special_offers данным по специальным предложениям, запускаем цикл по каждой акции и проверяем по всем параметрам
            # можно ли применить скидку к текущим стоимостям
            for offer in data_offers:
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
            
            # Добавляем в словарь с ценами, по ключу "программа лояльности" словрь с ценами по программе лояльности и если есть, спец предложениями вместе с п.л.                     
            date_dict['программа лояльности'] = loyalty_discont_dict
            
            # Если названия категории нет в большом словаре с данными, добавляем пустой словарь по ключу "каатегория"
            if name not in data_collection:
                data_collection[name] = {}
            
            # Добавляем в большой словарь с данным, по ключу - категории данные, где в каждой категории будут храниться даты, и соответсвуеющие цены по ним
            data_collection[name][date.strftime('%d.%m.%Y')] = date_dict
            
            
            #------------------------------------------------------------ ВОЗВРАТ К ВЫБОРУ КАРТОЧЕК -----------------------------------------------------------------------------------
            # Находим кнопку возврата к выбору карточек кликаем по ней и ожидаем 3 секунды для загрузки
            back = browser.find_element(By.CLASS_NAME, 'x-hnp__link')
            back.click()
            # TODO: Оптимизировать ожидание
            time.sleep(3)
            
            # Инициализируем пустой список куда будут вновь добавляться кнопки "выбрать"
            selected_buttons = []
            
            # Запускаем цикл и ищем карточки с доступными категориями (Возможно обойтись без повтора?)
            # TODO: Оптимизировать раздел
            while True:
                start = len(selected_buttons)
                temp_list = [x for x in browser.find_elements(By.CLASS_NAME, 'tl-btn') if x.text != '']
                selected_buttons = set(selected_buttons).union(set(temp_list))
                end = len(selected_buttons)
                time.sleep(3)
                if start == end:
                    selected_buttons = temp_list
                    break
                browser.execute_script("return arguments[0].scrollIntoView(true);", temp_list[-1])
                time.sleep(3)
        
        # Прибавляем к дате один день
        date += timedelta(days=1)


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
print("Данные успешно сохранены в data.csv")
print(f"Время выполнения программы: {minutes} минут {seconds} секунд")