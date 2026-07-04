# OzonTracker-HA — трекинг посылок Ozon для Home Assistant

Кастомная интеграция Home Assistant для отслеживания посылок маркетплейса
**Ozon** через публичный сервис [tracking.ozon.ru](https://tracking.ozon.ru/)
плюс собственная Lovelace-карточка, в которой можно добавлять посылки
(трек-номер + название), переименовывать и удалять их.

Сделано по мотивам
[HA_aliexpress_package_tracker_sensor](https://github.com/yohaybn/HA_aliexpress_package_tracker_sensor).

## Возможности

- 📦 Отдельный сенсор `sensor.ozon_package_<трек>` на каждую посылку —
  состояние = текущий статус доставки, в атрибутах история событий,
  курьерская служба, ожидаемая дата доставки и ссылка на страницу трекинга.
- 🃏 Lovelace-карточка `custom:ozon-package-card` устанавливается вместе с
  интеграцией автоматически: список посылок, форма добавления
  (трек-номер + название), переименование, удаление, раскрываемая история
  событий по клику.
- 🔔 Событие `ozon_package_tracker_data_updated` при смене статуса — удобно
  для автоматизаций (уведомление «посылка приехала в пункт выдачи»).
- 🧹 Опциональное автоудаление доставленных посылок через N дней.
- 💾 Посылки хранятся в постоянном хранилище HA и переживают перезагрузку;
  при недоступности Ozon показываются последние известные данные.

## Установка

### HACS (рекомендуется)

1. HACS → меню (⋮) → *Пользовательские репозитории*.
2. Добавьте `https://github.com/BrainDeLook/OzonTracker-HA`, категория
   **Integration**.
3. Установите «Ozon Package Tracker» и перезапустите Home Assistant.

### Вручную

Скопируйте `custom_components/ozon_package_tracker` в папку
`custom_components` вашей конфигурации и перезапустите Home Assistant.

## Настройка

1. *Настройки → Устройства и службы → Добавить интеграцию* →
   **Ozon Package Tracker**.
2. В опциях интеграции можно изменить интервал опроса (по умолчанию
   30 минут) и автоудаление доставленных посылок (0 = выключено).

Карточка регистрируется как ресурс Lovelace автоматически. Если панель
работает в YAML-режиме, добавьте ресурс вручную:

```yaml
lovelace:
  resources:
    - url: /ozon_package_tracker/ozon-package-card.js
      type: module
```

## Карточка Lovelace

Добавьте карточку через UI (поиск «Ozon Package Card») или в YAML:

```yaml
type: custom:ozon-package-card
title: Мои посылки          # необязательно
show_add_form: true          # форма «трек + название»
show_track_number: true      # трек-номер под названием
show_last_event: true        # время последнего события
max_events: 5                # событий в раскрытой истории
# entities:                  # необязательно: явный список сенсоров
#   - sensor.ozon_package_33310100_0168_1
```

Без `entities` карточка сама находит все посылки интеграции.

## Сервисы

| Сервис | Параметры | Описание |
|---|---|---|
| `ozon_package_tracker.add_tracking` | `tracking_number` (обяз.), `title` | Добавить посылку |
| `ozon_package_tracker.remove_tracking` | `tracking_number` или `entity_id` | Удалить посылку |
| `ozon_package_tracker.edit_title` | `tracking_number`, `title` | Переименовать |
| `ozon_package_tracker.refresh` | — | Обновить все посылки сейчас |

Пример:

```yaml
service: ozon_package_tracker.add_tracking
data:
  tracking_number: "33310100-0168-1"
  title: "Наушники"
```

## Автоматизация на смену статуса

```yaml
automation:
  - alias: "Посылка Ozon — новый статус"
    trigger:
      - platform: event
        event_type: ozon_package_tracker_data_updated
    action:
      - service: notify.mobile_app_phone
        data:
          title: "📦 {{ trigger.event.data.title }}"
          message: "{{ trigger.event.data.new_status }}"
```

## Ограничения и примечания

- Интеграция использует **неофициальный** эндпоинт страницы
  tracking.ozon.ru
  (`GET /p-api/ozon-track-bff/tracking/{трек-номер}`); Ozon может изменить
  его в любой момент. Ответ содержит события в виде кодов
  (`Created`, `WayToCity`, `Delivered`, …) — интеграция переводит их в
  человекочитаемые статусы, незнакомые коды показываются «как есть».
  На случай изменений на стороне Ozon в клиенте
  (`custom_components/ozon_package_tracker/api.py`) остался запасной
  универсальный парсер, но может потребоваться правка `api.py`.
- Ozon блокирует запросы с зарубежных и датацентровых IP-адресов. С
  домашнего российского подключения проблем обычно нет; при работе HA через
  зарубежный VPN трекинг может отдавать `403`.

### Ошибка HTTP 403 (антибот)

Tracking.ozon.ru закрыт антибот-защитой с JavaScript-challenge
(в ответе видны `challengeURL` / `challenge.html` / `incidentId`), которая
вдобавок проверяет TLS-отпечаток соединения. Обычный Python-запрос она режет
с `403`. Интеграция обходит это в таком порядке:

1. **Имитация TLS-отпечатка Chrome через `curl_cffi`.** Библиотека
   ставится автоматически (прописана в зависимостях интеграции) и
   подделывает полный отпечаток браузера (TLS/JA3/HTTP2). Чаще всего этого
   достаточно, чтобы challenge пропустил запрос.
2. **Прогрев кук.** При 403 интеграция один раз заходит на саму страницу
   трекинга, собирает выданные куки в свою сессию и повторяет запрос.
3. **Ручные куки браузера** — если первые два шага не помогли:
   - Откройте [tracking.ozon.ru](https://tracking.ozon.ru/?track=ваш-трек) в
     браузере (там, где страница открывается нормально).
   - DevTools (**F12**) → вкладка **Network** → кликните запрос с вашим
     трек-номером → **Headers** → секция **Request Headers** → скопируйте
     значение заголовка `cookie` целиком.
   - В HA: *Настройки → Устройства и службы → Ozon Package Tracker →
     Настроить* → вставьте скопированное в поле **«Заголовок Cookie»**.

Примечания:

- Если `curl_cffi` не установился (редкая платформа без готового wheel),
  интеграция откатывается на обычный HTTP-клиент — тогда почти наверняка
  понадобится ручной cookie из шага 3. Установить вручную:
  `pip install curl_cffi` в окружении Home Assistant.
- Куки антибота живут долго, но не вечно: если `403` вернётся через
  несколько недель — повторите шаг 3.
- Убедитесь, что HA выходит в интернет с российского IP (не через
  зарубежный VPN/VPS): при зарубежном адресе не помогут ни куки, ни
  `curl_cffi`.
- Трек-номер — это номер отправления Ozon вида `33310100-0168-1`
  (виден на странице заказа и на [tracking.ozon.ru](https://tracking.ozon.ru/)).

---

# English

Home Assistant custom integration that tracks **Ozon** marketplace packages
via the public [tracking.ozon.ru](https://tracking.ozon.ru/) service, with a
bundled Lovelace card for adding packages (tracking number + name), renaming
and removing them. Inspired by
[HA_aliexpress_package_tracker_sensor](https://github.com/yohaybn/HA_aliexpress_package_tracker_sensor).

- One sensor per package (`sensor.ozon_package_<track>`), state = current
  delivery status, attributes include event history and a tracking URL.
- Card auto-registers as a Lovelace resource; add it as
  `type: custom:ozon-package-card`.
- Services: `add_tracking`, `remove_tracking`, `edit_title`, `refresh`.
- `ozon_package_tracker_data_updated` event fires on status changes.
- Note: the endpoint is unofficial and Ozon geo-blocks non-Russian /
  datacenter IPs, so the integration is expected to run from a Russian
  residential connection.
