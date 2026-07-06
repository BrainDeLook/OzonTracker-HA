/*
 * Ozon Package Card
 * Lovelace card bundled with the Ozon Package Tracker integration.
 * https://github.com/BrainDeLook/OzonTracker-HA
 */
(() => {
  const CARD_TAG = "ozon-package-card";
  const EDITOR_TAG = "ozon-package-card-editor";
  const DOMAIN = "ozon_package_tracker";
  const CARD_VERSION = "0.4.0";

  const STRINGS = {
    ru: {
      default_title: "Посылки Ozon",
      empty: "Пока нет отслеживаемых посылок",
      track_placeholder: "Трек-номер, напр. 33310100-0168-1",
      title_placeholder: "Название посылки",
      add: "Добавить",
      adding: "Добавляю…",
      remove_confirm: "Удалить посылку «{title}»?",
      rename_prompt: "Новое название посылки:",
      rename: "Переименовать",
      remove: "Удалить",
      open: "Открыть страницу трекинга",
      no_status: "Нет данных",
      delivered: "Доставлено",
      updated: "Обновлено",
      history: "История",
      eta: "доставка",
    },
    en: {
      default_title: "Ozon packages",
      empty: "No tracked packages yet",
      track_placeholder: "Tracking number, e.g. 33310100-0168-1",
      title_placeholder: "Package name",
      add: "Add",
      adding: "Adding…",
      remove_confirm: "Remove package “{title}”?",
      rename_prompt: "New package name:",
      rename: "Rename",
      remove: "Remove",
      open: "Open tracking page",
      no_status: "No data",
      delivered: "Delivered",
      updated: "Updated",
      history: "History",
      eta: "delivery",
    },
  };

  const CSS = `
    ha-card { overflow: hidden; }
    .card-header {
      display: flex; align-items: center; justify-content: space-between;
      padding: 12px 16px 0 16px;
      font-size: 1.25rem; font-weight: 500; color: var(--primary-text-color);
    }
    .list { padding: 4px 0 8px 0; }
    .empty {
      padding: 16px; color: var(--secondary-text-color); font-style: italic;
    }
    .pkg {
      display: flex; align-items: center; gap: 12px;
      padding: 10px 16px; cursor: pointer;
    }
    .pkg:hover { background: var(--secondary-background-color); }
    .pkg ha-icon.pkg-icon { color: var(--state-icon-color, var(--paper-item-icon-color)); flex: none; }
    .pkg.delivered ha-icon.pkg-icon { color: var(--success-color, #4caf50); }
    .info { flex: 1; min-width: 0; }
    .name {
      color: var(--primary-text-color); font-weight: 500;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .secondary {
      color: var(--secondary-text-color); font-size: 0.85em;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .status-chip {
      flex: none; max-width: 40%;
      padding: 3px 10px; border-radius: 12px; font-size: 0.8em;
      background: rgba(var(--rgb-primary-color, 33, 150, 243), 0.15);
      color: var(--primary-color);
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .pkg.delivered .status-chip {
      background: rgba(76, 175, 80, 0.15); color: var(--success-color, #4caf50);
    }
    .actions { display: flex; flex: none; align-items: center; }
    .actions button, .actions a {
      background: none; border: none; padding: 4px; margin: 0;
      cursor: pointer; color: var(--secondary-text-color); display: flex;
      border-radius: 50%; text-decoration: none;
    }
    .actions button:hover, .actions a:hover { color: var(--primary-text-color); }
    .actions ha-icon { --mdc-icon-size: 18px; }
    .events {
      padding: 0 16px 4px 52px;
      /* Show about 5 events, then scroll inside the block. */
      max-height: var(--ozon-events-max-height, 8rem);
      overflow-y: auto;
      overscroll-behavior: contain;
      scrollbar-width: thin;
      scrollbar-color: var(--divider-color) transparent;
    }
    .events::-webkit-scrollbar { width: 6px; }
    .events::-webkit-scrollbar-thumb {
      background: var(--divider-color); border-radius: 3px;
    }
    .event {
      display: flex; gap: 10px; padding: 3px 0;
      color: var(--secondary-text-color); font-size: 0.85em;
    }
    .event .time { flex: none; min-width: 110px; opacity: 0.8; }
    .event:first-child { color: var(--primary-text-color); }
    .add-form {
      display: flex; gap: 8px; padding: 8px 16px 16px 16px; flex-wrap: wrap;
      border-top: 1px solid var(--divider-color);
    }
    .add-form input {
      flex: 1 1 140px; min-width: 0;
      padding: 8px 10px; border-radius: 6px;
      border: 1px solid var(--divider-color);
      background: var(--card-background-color);
      color: var(--primary-text-color); font: inherit;
    }
    .add-form input:focus { outline: 2px solid var(--primary-color); border-color: transparent; }
    .add-form button {
      flex: none; padding: 8px 16px; border: none; border-radius: 6px;
      background: var(--primary-color); color: var(--text-primary-color, #fff);
      font: inherit; cursor: pointer;
    }
    .add-form button:disabled { opacity: 0.6; cursor: default; }
    .error { padding: 0 16px 8px 16px; color: var(--error-color, #db4437); font-size: 0.85em; }
  `;

  class OzonPackageCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._expanded = new Set();
      this._signature = null;
    }

    setConfig(config) {
      this._config = {
        title: null,
        show_add_form: true,
        show_track_number: true,
        show_last_event: true,
        max_events: 0, // 0 = show all events
        entities: null,
        ...config,
      };
      this._built = false;
      this._signature = null;
    }

    set hass(hass) {
      this._hass = hass;
      if (!this._built) this._build();
      this._renderList();
    }

    _t(key, vars) {
      const lang = (this._hass && this._hass.language) || "en";
      const table = lang.startsWith("ru") ? STRINGS.ru : STRINGS.en;
      let text = table[key] || STRINGS.en[key] || key;
      if (vars) {
        for (const [name, value] of Object.entries(vars)) {
          text = text.replace(`{${name}}`, value);
        }
      }
      return text;
    }

    _states() {
      if (!this._hass) return [];
      let states;
      if (Array.isArray(this._config.entities) && this._config.entities.length) {
        states = this._config.entities
          .map((id) => this._hass.states[id])
          .filter(Boolean);
      } else {
        states = Object.values(this._hass.states).filter(
          (s) => s.attributes && s.attributes.integration === DOMAIN
        );
      }
      return states.sort((a, b) => {
        const ad = a.attributes.delivered ? 1 : 0;
        const bd = b.attributes.delivered ? 1 : 0;
        if (ad !== bd) return ad - bd;
        return (a.attributes.title || "").localeCompare(b.attributes.title || "");
      });
    }

    _build() {
      const title =
        this._config.title === null || this._config.title === undefined
          ? this._t("default_title")
          : this._config.title;
      this.shadowRoot.innerHTML = `
        <ha-card>
          <style>${CSS}</style>
          ${title ? `<div class="card-header">${escapeHtml(title)}</div>` : ""}
          <div class="list"></div>
          <div class="error" hidden></div>
          ${
            this._config.show_add_form
              ? `<div class="add-form">
                   <input class="track-input" type="text"
                     placeholder="${this._t("track_placeholder")}" spellcheck="false">
                   <input class="title-input" type="text"
                     placeholder="${this._t("title_placeholder")}">
                   <button class="add-btn">${this._t("add")}</button>
                 </div>`
              : ""
          }
        </ha-card>`;

      if (this._config.show_add_form) {
        const addBtn = this.shadowRoot.querySelector(".add-btn");
        addBtn.addEventListener("click", () => this._addPackage());
        for (const input of this.shadowRoot.querySelectorAll(".add-form input")) {
          input.addEventListener("keydown", (ev) => {
            if (ev.key === "Enter") this._addPackage();
          });
        }
      }
      this._built = true;
    }

    _renderList() {
      const states = this._states();
      const signature = JSON.stringify(
        states.map((s) => [
          s.entity_id,
          s.state,
          s.attributes.title,
          s.attributes.last_event_time,
          s.attributes.estimated_delivery,
          (s.attributes.events || []).length,
          this._expanded.has(s.entity_id),
        ])
      );
      if (signature === this._signature) return;
      this._signature = signature;

      const list = this.shadowRoot.querySelector(".list");
      if (!states.length) {
        list.innerHTML = `<div class="empty">${this._t("empty")}</div>`;
        return;
      }

      list.innerHTML = states
        .map((s) => this._renderPackage(s))
        .join("");

      for (const row of list.querySelectorAll(".pkg")) {
        const entityId = row.dataset.entity;
        row.addEventListener("click", () => {
          if (this._expanded.has(entityId)) this._expanded.delete(entityId);
          else this._expanded.add(entityId);
          this._signature = null;
          this._renderList();
        });
      }
      for (const btn of list.querySelectorAll("button.rename")) {
        btn.addEventListener("click", (ev) => {
          ev.stopPropagation();
          this._renamePackage(btn.dataset.track, btn.dataset.title);
        });
      }
      for (const btn of list.querySelectorAll("button.remove")) {
        btn.addEventListener("click", (ev) => {
          ev.stopPropagation();
          this._removePackage(btn.dataset.track, btn.dataset.title);
        });
      }
      for (const link of list.querySelectorAll("a.open")) {
        link.addEventListener("click", (ev) => ev.stopPropagation());
      }
    }

    _renderPackage(state) {
      const attrs = state.attributes;
      const track = attrs.tracking_number || "";
      const title = attrs.title || track;
      const delivered = !!attrs.delivered;
      const status = state.state && state.state !== "unknown"
        ? state.state
        : this._t("no_status");
      const icon = attrs.icon || (delivered
        ? "mdi:package-variant-closed-check"
        : "mdi:package-variant-closed");
      const url = attrs.tracking_url ||
        `https://tracking.ozon.ru/?track=${encodeURIComponent(track)}`;

      const secondaryParts = [];
      if (this._config.show_track_number && title !== track) {
        secondaryParts.push(track);
      }
      if (this._config.show_last_event && attrs.last_event_time) {
        secondaryParts.push(formatTime(attrs.last_event_time, this._hass.language));
      }
      if (attrs.estimated_delivery && !delivered) {
        secondaryParts.push(`${this._t("eta")}: ${attrs.estimated_delivery}`);
      }

      let eventsHtml = "";
      if (this._expanded.has(state.entity_id)) {
        const allEvents = attrs.events || [];
        const limit = this._config.max_events;
        // 0 (or unset) shows every event; a positive number caps the list.
        const events = limit && limit > 0 ? allEvents.slice(0, limit) : allEvents;
        if (events.length) {
          eventsHtml = `<div class="events">${events
            .map(
              (ev) => `<div class="event">
                <span class="time">${escapeHtml(
                  formatTime(ev.time, this._hass.language) || ""
                )}</span>
                <span>${escapeHtml(ev.status || "")}</span>
              </div>`
            )
            .join("")}</div>`;
        }
      }

      return `
        <div class="pkg ${delivered ? "delivered" : ""}"
             data-entity="${escapeHtml(state.entity_id)}">
          <ha-icon class="pkg-icon" icon="${escapeHtml(icon)}"></ha-icon>
          <div class="info">
            <div class="name">${escapeHtml(title)}</div>
            ${
              secondaryParts.length
                ? `<div class="secondary">${escapeHtml(secondaryParts.join(" · "))}</div>`
                : ""
            }
          </div>
          <div class="status-chip" title="${escapeHtml(status)}">${escapeHtml(status)}</div>
          <div class="actions">
            <button class="rename" title="${this._t("rename")}"
              data-track="${escapeHtml(track)}" data-title="${escapeHtml(title)}">
              <ha-icon icon="mdi:pencil-outline"></ha-icon>
            </button>
            <button class="remove" title="${this._t("remove")}"
              data-track="${escapeHtml(track)}" data-title="${escapeHtml(title)}">
              <ha-icon icon="mdi:trash-can-outline"></ha-icon>
            </button>
            <a class="open" href="${escapeHtml(url)}" target="_blank"
               rel="noreferrer noopener" title="${this._t("open")}">
              <ha-icon icon="mdi:open-in-new"></ha-icon>
            </a>
          </div>
        </div>
        ${eventsHtml}`;
    }

    async _addPackage() {
      const trackInput = this.shadowRoot.querySelector(".track-input");
      const titleInput = this.shadowRoot.querySelector(".title-input");
      const addBtn = this.shadowRoot.querySelector(".add-btn");
      const track = (trackInput.value || "").trim();
      if (!track) {
        trackInput.focus();
        return;
      }
      addBtn.disabled = true;
      addBtn.textContent = this._t("adding");
      try {
        const data = { tracking_number: track };
        const title = (titleInput.value || "").trim();
        if (title) data.title = title;
        await this._hass.callService(DOMAIN, "add_tracking", data);
        trackInput.value = "";
        titleInput.value = "";
        this._showError(null);
      } catch (err) {
        this._showError(err && err.message ? err.message : String(err));
      } finally {
        addBtn.disabled = false;
        addBtn.textContent = this._t("add");
      }
    }

    async _removePackage(track, title) {
      if (!confirm(this._t("remove_confirm", { title: title || track }))) return;
      try {
        await this._hass.callService(DOMAIN, "remove_tracking", {
          tracking_number: track,
        });
        this._showError(null);
      } catch (err) {
        this._showError(err && err.message ? err.message : String(err));
      }
    }

    async _renamePackage(track, currentTitle) {
      const title = prompt(this._t("rename_prompt"), currentTitle || "");
      if (title === null) return;
      try {
        await this._hass.callService(DOMAIN, "edit_title", {
          tracking_number: track,
          title: title.trim() || track,
        });
        this._showError(null);
      } catch (err) {
        this._showError(err && err.message ? err.message : String(err));
      }
    }

    _showError(message) {
      const el = this.shadowRoot.querySelector(".error");
      if (!el) return;
      if (message) {
        el.textContent = message;
        el.hidden = false;
      } else {
        el.hidden = true;
      }
    }

    getCardSize() {
      return 2 + this._states().length;
    }

    static getConfigElement() {
      return document.createElement(EDITOR_TAG);
    }

    static getStubConfig() {
      return { show_add_form: true };
    }
  }

  class OzonPackageCardEditor extends HTMLElement {
    setConfig(config) {
      this._config = { ...config };
      this._render();
    }

    set hass(hass) {
      this._hass = hass;
      this._render();
    }

    _render() {
      if (!this._hass || !this._config) return;
      if (!this._form) {
        this._form = document.createElement("ha-form");
        this._form.addEventListener("value-changed", (ev) => {
          const config = { ...this._config, ...ev.detail.value };
          this._config = config;
          this.dispatchEvent(
            new CustomEvent("config-changed", {
              detail: { config },
              bubbles: true,
              composed: true,
            })
          );
        });
        this.appendChild(this._form);
      }
      const ru = ((this._hass && this._hass.language) || "").startsWith("ru");
      const labels = ru
        ? {
            title: "Заголовок",
            show_add_form: "Показывать форму добавления",
            show_track_number: "Показывать трек-номер",
            show_last_event: "Показывать время последнего события",
            max_events: "Событий в истории (0 = все)",
          }
        : {
            title: "Title",
            show_add_form: "Show add form",
            show_track_number: "Show tracking number",
            show_last_event: "Show last event time",
            max_events: "Events in history (0 = all)",
          };
      this._form.hass = this._hass;
      this._form.data = {
        show_add_form: true,
        show_track_number: true,
        show_last_event: true,
        max_events: 0,
        ...this._config,
      };
      this._form.schema = [
        { name: "title", selector: { text: {} } },
        { name: "show_add_form", selector: { boolean: {} } },
        { name: "show_track_number", selector: { boolean: {} } },
        { name: "show_last_event", selector: { boolean: {} } },
        { name: "max_events", selector: { number: { min: 0, max: 60, mode: "box" } } },
      ];
      this._form.computeLabel = (schema) => labels[schema.name] || schema.name;
    }
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function formatTime(value, language) {
    if (!value) return null;
    let date = null;
    const text = String(value).trim();
    if (/^\d{13}$/.test(text)) date = new Date(Number(text));
    else if (/^\d{10}$/.test(text)) date = new Date(Number(text) * 1000);
    else {
      const parsed = new Date(text.replace(" ", "T"));
      if (!Number.isNaN(parsed.getTime())) date = parsed;
    }
    if (!date) return text;
    const now = Date.now();
    const diffMinutes = Math.round((date.getTime() - now) / 60000);
    try {
      const rtf = new Intl.RelativeTimeFormat(language || "en", {
        numeric: "auto",
      });
      const abs = Math.abs(diffMinutes);
      if (abs < 60) return rtf.format(diffMinutes, "minute");
      if (abs < 60 * 24) return rtf.format(Math.round(diffMinutes / 60), "hour");
      if (abs < 60 * 24 * 30)
        return rtf.format(Math.round(diffMinutes / (60 * 24)), "day");
    } catch (err) {
      /* fall through to absolute date */
    }
    return date.toLocaleDateString(language || "en", {
      day: "numeric",
      month: "short",
    });
  }

  if (!customElements.get(CARD_TAG)) {
    customElements.define(CARD_TAG, OzonPackageCard);
  }
  if (!customElements.get(EDITOR_TAG)) {
    customElements.define(EDITOR_TAG, OzonPackageCardEditor);
  }

  window.customCards = window.customCards || [];
  if (!window.customCards.some((card) => card.type === CARD_TAG)) {
    window.customCards.push({
      type: CARD_TAG,
      name: "Ozon Package Card",
      description:
        "Packages tracked by the Ozon Package Tracker integration, with an add/remove form.",
      preview: true,
      documentationURL: "https://github.com/BrainDeLook/OzonTracker-HA",
    });
  }

  console.info(
    `%c OZON-PACKAGE-CARD %c v${CARD_VERSION} `,
    "color: white; background: #005bff; font-weight: 700;",
    "color: #005bff; background: white; font-weight: 700;"
  );
})();
