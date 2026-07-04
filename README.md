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

### Обход антибота (важно): headless-браузер прокси

Tracking.ozon.ru закрыт антибот-защитой с **JavaScript-challenge**
(`fab_ichlg`; в теле 403 видны `challengeURL` / `incidentId`). Токен доступа
вычисляется JavaScript'ом и кладётся в куку — получить его можно **только
выполнив этот JS в настоящем браузере**. Ни правильные заголовки, ни имитация
TLS (`curl_cffi`) challenge не решают, а вставленная вручную кука протухает
через часы-сутки.

Поэтому рекомендуемый способ — поднять рядом с Home Assistant дополнение
**`ozon-tracker-proxy`** (папка [`ozon-tracker-proxy/`](ozon-tracker-proxy)):
оно держит headless-Chromium (Playwright), само проходит challenge, обновляет
куки в фоне и отдаёт интеграции готовый JSON. Вставлять куки руками больше не
нужно.

**Способ A — Home Assistant Add-on (рекомендуется для HAOS/Supervised):**

1. *Настройки → Дополнения → Магазин дополнений* → меню **⋮** →
   **Репозитории** → добавьте
   `https://github.com/BrainDeLook/OzonTracker-HA`.
2. Установите дополнение **«Ozon Tracker Proxy»**, запустите, включите
   «Запускать при загрузке» и «Watchdog».
3. В опциях интеграции укажите **URL прокси** = `http://<IP-HA>:8080`.

> Поддерживаются только архитектуры **amd64** и **aarch64** — Chromium от
> Playwright не собирается под armv7/armhf (старые Raspberry Pi).

**Способ B — Docker Compose (для обычного Docker/NAS/мини-ПК):**

```bash
cd ozon-tracker-proxy
docker compose up -d --build
curl http://localhost:8080/healthz          # {"status": "ok"}
```

Затем в HA: *Настройки → Устройства и службы → Ozon Package Tracker →
Настроить* → в поле **«URL headless-браузер прокси»** укажите адрес сервиса,
напр. `http://homeassistant.local:8080` (или IP хоста с Docker).

Требования: ~350–500 МБ RAM под Chromium. На «чистой» Home Assistant OS без
поддерживаемой архитектуры используйте способ B на отдельной машине в той же
сети. Подробности и опции — в [`ozon-tracker-proxy/DOCS.md`](ozon-tracker-proxy/DOCS.md).

### Без прокси (запасные варианты)

Если прокси не используется, интеграция обращается к Ozon напрямую и пытается
обойти защиту заголовками приложения (`x-o3-app-name` / `x-o3-app-version` +
актуальный Chrome) и имитацией TLS через `curl_cffi`. Этого **может не
хватить** — тогда как крайний вариант вставьте куку браузера в поле
**«Заголовок Cookie»** (DevTools → Network → заголовок `cookie`); учтите, что
она протухает и её придётся периодически обновлять.

Примечания:

- Если через несколько месяцев `403` вернётся при прямом доступе, Ozon мог
  поднять версию приложения — обновите `x-o3-app-version` в
  `custom_components/ozon_package_tracker/api.py` (и в `ozon-tracker-proxy`
  через переменную `OZON_APP_VERSION`).
- Убедитесь, что HA/прокси выходят в интернет с российского IP (не через
  зарубежный VPN/VPS): при зарубежном адресе антибот может блокировать
  запрос независимо от способа.
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
- Ozon guards the endpoint with a JavaScript anti-bot challenge. The
  recommended way around it is the bundled [`ozon-tracker-proxy`](ozon-tracker-proxy)
  service — a headless Chromium (Playwright) that solves the challenge,
  auto-refreshes cookies and serves the tracking JSON to the integration.
  Set its URL in the integration options.
- Note: the endpoint is unofficial and Ozon geo-blocks non-Russian /
  datacenter IPs, so the integration/proxy is expected to run from a Russian
  residential connection.
