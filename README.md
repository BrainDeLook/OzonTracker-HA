# 📦 Ozon Package Tracker для Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Validate](https://github.com/BrainDeLook/OzonTracker-HA/actions/workflows/validate.yml/badge.svg)](https://github.com/BrainDeLook/OzonTracker-HA/actions/workflows/validate.yml)

Кастомная интеграция Home Assistant для отслеживания посылок маркетплейса
**Ozon** + собственная Lovelace‑карточка, в которой можно добавлять посылки
(трек‑номер + название), переименовывать и удалять их.

Данные по умолчанию берутся с агрегатора
[track365.ru](https://track365.ru/) — **работает сразу, без каких‑либо
дополнительных настроек**. Статусы приходят в человекочитаемом виде и часто
подробнее оригинального трекинга Ozon.

> Сделано по мотивам
> [HA_aliexpress_package_tracker_sensor](https://github.com/yohaybn/HA_aliexpress_package_tracker_sensor).

## ✨ Возможности

- 📦 Отдельный сенсор `sensor.ozon_package_<трек>` на каждую посылку —
  состояние = текущий статус доставки; в атрибутах вся история событий,
  курьер, флаг «доставлено» и ссылка на страницу трекинга.
- 🃏 Lovelace‑карточка `custom:ozon-package-card` ставится вместе с
  интеграцией: список посылок, форма добавления (трек + название),
  переименование, удаление и раскрываемая история со скроллом.
- 🔔 Событие `ozon_package_tracker_data_updated` при смене статуса — удобно
  для автоматизаций и уведомлений.
- 🟢 Зелёный значок только когда посылка действительно доставлена/выдана.
- 🧹 Опциональное автоудаление доставленных посылок через N дней.
- 💾 Посылки хранятся в HA и переживают перезагрузку; при недоступности
  сервиса показываются последние известные данные.

## 🚀 Установка

### Через HACS (рекомендуется)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=BrainDeLook&repository=OzonTracker-HA&category=integration)

1. Нажмите кнопку выше (или в HACS: меню ⋮ → *Пользовательские репозитории* →
   добавьте `https://github.com/BrainDeLook/OzonTracker-HA`, категория
   **Integration**).
2. Установите **Ozon Package Tracker** и перезапустите Home Assistant.

### Вручную

Скопируйте папку `custom_components/ozon_package_tracker` в `custom_components`
вашей конфигурации Home Assistant и перезапустите его.

## ⚙️ Настройка

1. *Настройки → Устройства и службы → Добавить интеграцию* →
   **Ozon Package Tracker**.
2. Добавьте карточку `custom:ozon-package-card` на дашборд (ресурс
   регистрируется автоматически) и вводите трек‑номера прямо в ней.

Параметры интеграции (*Настроить*):

| Параметр | По умолчанию | Описание |
|---|---|---|
| **Источник данных** | `track365` | Откуда берутся данные (см. ниже). |
| **Проверять SSL‑сертификат** | вкл. | Обычно не трогать — проверка автоматическая. |
| **Ссылка «Открыть страницу трекинга»** | как источник данных | Куда ведёт кнопка в карточке (см. ниже). |
| **Интервал обновления** | 60 мин | Как часто опрашивать сервис. |
| **Автоудаление доставленных** | 0 (выкл.) | Удалять доставленные посылки через N дней. |
| **Устройства для уведомлений** | — | Куда слать push‑уведомления о смене статуса (см. ниже). |
| **Уровень уведомлений** | Каждая смена статуса | Какие статусы уведомлять (см. ниже). |
| **Заголовок Cookie** | — | Только для источника Ozon (см. ниже). |

### Источник данных

- **track365 (по умолчанию, рекомендуется)** — данные с агрегатора
  [track365.ru](https://track365.ru/), который отслеживает посылки Ozon.
  **Ничего дополнительно настраивать не нужно.**
- **Ozon напрямую** — данные берутся прямо с `tracking.ozon.ru`. Сайт
  закрыт антибот‑защитой, поэтому для этого режима нужно вставить **cookie**
  из браузера, где открыт tracking.ozon.ru (поле «Заголовок Cookie»). Кука
  периодически протухает и её приходится обновлять — режим для продвинутых
  пользователей.

### Ссылка «Открыть страницу трекинга»

Настройка **независима** от источника данных выше — можно, например, получать
данные через track365, но по клику открывать страницу на tracking.ozon.ru,
или наоборот:

- **Как источник данных (по умолчанию)** — ссылка ведёт туда, откуда реально
  пришли данные по конкретной посылке.
- **Всегда track365.ru** — кнопка всегда открывает track365.ru, даже если
  данные берутся напрямую с Ozon.
- **Всегда tracking.ozon.ru** — кнопка всегда открывает Ozon, даже если
  данные берутся через track365.

### Push‑уведомления о смене статуса

- **Устройства для уведомлений** — выберите одну или несколько сущностей
  `notify.*` (например, мобильное приложение Home Assistant на телефоне).
  Поле пустое по умолчанию — уведомления выключены, пока не выбрано хотя бы
  одно устройство.
- **Уровень уведомлений**:
  - **Каждая смена статуса** (по умолчанию) — уведомление на любое изменение
    статуса посылки.
  - **Только прибытие в пункт выдачи** — уведомление придёт только тогда,
    когда посылка добралась до пункта выдачи/постамата, остальные смены
    статуса молча пропускаются.
- В заголовке уведомления — название посылки (как задано при добавлении),
  в тексте — новый статус.

## 🃏 Карточка Lovelace

Добавьте через UI (поиск «Ozon Package Card») или в YAML:

```yaml
type: custom:ozon-package-card
title: Мои посылки          # необязательно
show_add_form: true          # форма «трек + название»
show_track_number: true      # трек‑номер под названием
show_last_event: true        # время последнего события
max_events: 0                # событий в раскрытой истории (0 = все, со скроллом)
# entities:                  # необязательно: явный список сенсоров
#   - sensor.ozon_package_33310100_0168_1
```

Без `entities` карточка сама находит все посылки интеграции. По клику на
посылку раскрывается история событий (со скроллом).

## 🛠 Сервисы

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

## 🤖 Автоматизация на смену статуса

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

## 📝 Примечания

- Источник **track365** — сторонний сервис: данные о посылке идут через него.
  Параметр `fp` его API воспроизведён в интеграции; если track365 изменит
  алгоритм, потребуется обновление.
- Оба сервиса (track365 и Ozon) отдают данные только для российских
  IP‑адресов. Нужен российский домашний канал (не зарубежный VPN/VPS).
- Трек‑номер — это номер отправления Ozon вида `33310100-0168-1` (виден на
  странице заказа и на [tracking.ozon.ru](https://tracking.ozon.ru/)).
- Как только посылка помечена доставленной, интеграция перестаёт опрашивать
  по ней сервис (и не делает этого даже при ручном вызове сервиса
  `ozon_package_tracker.refresh`) — сенсор просто продолжает показывать
  последний известный статус. Это экономит запросы к track365/Ozon; при
  «Автоудалении доставленных» = 0 доставленные посылки так и останутся в
  списке, просто без опроса.

---

# English

Home Assistant custom integration that tracks **Ozon** marketplace parcels,
with a bundled Lovelace card to add packages (tracking number + name), rename
and remove them. By default it reads data from the
[track365.ru](https://track365.ru/) aggregator — **works out of the box**, with
rich human‑readable statuses.

Install via HACS (use the button above or add
`https://github.com/BrainDeLook/OzonTracker-HA` as a custom **Integration**
repository), then add the integration and drop the `custom:ozon-package-card`
card on a dashboard.

- One sensor per package (`sensor.ozon_package_<track>`); state = current
  status, attributes hold the full event history and a tracking URL.
- Services: `add_tracking`, `remove_tracking`, `edit_title`, `refresh`.
- `ozon_package_tracker_data_updated` event fires on status changes.
- **Data source** option: `track365` (default, recommended) or `ozon` (direct
  `tracking.ozon.ru`, which needs a browser cookie because of the anti-bot).
- **"Open tracking page" link** option: independent of the data source — pin
  the card's button (and `tracking_url` attribute) to always open track365.ru
  or tracking.ozon.ru, or leave it on `auto` to follow whichever source
  actually produced the data.
- **Push notifications**: pick one or more `notify.*` entities (e.g. your
  phone's mobile app) and a notification level — every status change, or only
  when the parcel reaches a pickup point/locker. Empty by default (disabled).
  The notification title is the package's friendly name.
- Once a package is marked delivered, it stops being polled entirely (even a
  manual `ozon_package_tracker.refresh`) — the sensor just keeps showing the
  last known status. Saves requests; with auto-delete off, delivered packages
  simply sit there unpolled instead of being removed.
- Note: both services only serve Russian IPs; run Home Assistant on a Russian
  residential connection.
