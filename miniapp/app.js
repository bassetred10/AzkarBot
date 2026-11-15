(function () {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) {
    tg.ready();
    tg.expand();
    try { tg.setHeaderColor("#081510"); tg.setBackgroundColor("#081510"); } catch (_) {}
  }

  const $ = (selector, root) => (root || document).querySelector(selector);
  const $$ = (selector, root) => Array.from((root || document).querySelectorAll(selector));
  const storage = {
    get(key, fallback) {
      try {
        const value = localStorage.getItem("athar:" + key);
        return value === null ? fallback : JSON.parse(value);
      } catch (_) { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem("athar:" + key, JSON.stringify(value)); } catch (_) {}
    }
  };

  const els = {
    status: $("#connectionStatus"), summary: $("#catalogueSummary"), date: $("#todayLabel"),
    dailyTitle: $("#dailyTitle"), dailyText: $("#dailyText"), dailySource: $("#dailySource"),
    dailySave: $("#dailySave"), copy: $("#copyButton"), search: $("#searchInput"),
    clear: $("#clearSearch"), chips: $("#categoryChips"), list: $("#contentList"),
    listHeading: $("#listHeading"), count: $("#itemCount"), loadMore: $("#loadMore"),
    savedList: $("#savedList"), badge: $("#savedBadge"), toast: $("#toast"),
    modal: $("#itemModal"), modalTitle: $("#modalTitle"), modalText: $("#modalText"),
    modalCategory: $("#modalCategory"), modalMeta: $("#modalMeta"),
    modalVirtue: $("#modalVirtue"), modalSave: $("#modalSave"),
    sourceLink: $("#sourceLink"), counterDhikr: $("#counterDhikr"),
    counterValue: $("#counterValue"), counterTarget: $("#counterTarget"),
    targetLabel: $("#targetLabel"), counterProgress: $("#counterProgress"),
    counterHint: $("#counterHint"), counterButton: $("#counterButton")
  };

  const state = {
    catalogue: { categories: [], items: [] },
    view: "home",
    category: "all",
    query: "",
    visibleLimit: 8,
    selected: null,
    daily: null,
    counterItem: null,
    counterValue: 0,
    counterTarget: 100,
    favorites: new Set(storage.get("favorites", [])),
    theme: storage.get("theme", "dark"),
    counterData: storage.get("counter", {})
  };

  function node(tag, className, value) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (value !== undefined && value !== null) el.textContent = String(value);
    return el;
  }

  function normalize(value) {
    return String(value || "")
      .toLowerCase()
      .normalize("NFKD")
      .replace(/[\u064B-\u065F\u0670\u0640]/g, "")
      .replace(/[أإآ]/g, "ا")
      .replace(/ى/g, "ي")
      .replace(/ة/g, "ه")
      .trim();
  }

  function categoryFor(id) {
    return state.catalogue.categories.find(item => item.id === id);
  }

  function categoryTitle(id) {
    const category = categoryFor(id);
    return category ? category.title : "محتوى أَثَر";
  }

  function toast(message) {
    els.toast.textContent = message;
    els.toast.classList.add("is-visible");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => els.toast.classList.remove("is-visible"), 2200);
  }

  function haptic(kind) {
    try {
      if (tg && tg.HapticFeedback) {
        if (kind === "success") tg.HapticFeedback.notificationOccurred("success");
        else tg.HapticFeedback.impactOccurred(kind || "light");
      }
    } catch (_) {}
  }

  function updateDate() {
    try {
      els.date.textContent = new Intl.DateTimeFormat("ar-DZ", {
        weekday: "long", day: "numeric", month: "long"
      }).format(new Date());
    } catch (_) { els.date.textContent = "أَثَر · كل يوم"; }
  }

  function setTheme(theme) {
    state.theme = theme === "light" ? "light" : "dark";
    document.body.dataset.theme = state.theme;
    $("#themeButton").setAttribute("aria-label", state.theme === "dark" ? "تفعيل المظهر الفاتح" : "تفعيل المظهر الداكن");
    storage.set("theme", state.theme);
    try { if (tg) tg.setBackgroundColor(state.theme === "dark" ? "#081510" : "#f3f4ed"); } catch (_) {}
  }

  function setConnection(connected, message) {
    els.status.classList.toggle("is-connected", !!connected);
    $("span", els.status).textContent = message;
  }

  function saved(item) {
    return !!item && state.favorites.has(item.id);
  }

  function persistFavorites() {
    storage.set("favorites", Array.from(state.favorites));
    updateSavedBadge();
  }

  function updateSavedBadge() {
    const count = state.favorites.size;
    els.badge.hidden = count === 0;
    els.badge.textContent = count > 99 ? "99+" : String(count);
  }

  function toggleFavorite(item) {
    if (!item) return;
    if (state.favorites.has(item.id)) {
      state.favorites.delete(item.id);
      toast("أُزيل من محفوظاتك");
    } else {
      state.favorites.add(item.id);
      toast("أُضيف إلى محفوظاتك");
    }
    haptic("light");
    persistFavorites();
    updateFavoriteButtons();
    if (state.view === "saved") renderSaved();
  }

  function updateFavoriteButtons() {
    if (state.daily) {
      els.dailySave.textContent = saved(state.daily) ? "★ محفوظ" : "☆ حفظ";
      els.dailySave.classList.toggle("is-saved", saved(state.daily));
    }
    if (state.selected) {
      els.modalSave.textContent = saved(state.selected) ? "★ محفوظ" : "☆ حفظ";
    }
  }

  function chooseDaily() {
    const preferred = state.catalogue.items.filter(item => item.type === "dhikr");
    if (!preferred.length) return null;
    const now = new Date();
    const daySeed = Number(now.getFullYear() + String(now.getMonth() + 1).padStart(2, "0") + String(now.getDate()).padStart(2, "0"));
    return preferred[daySeed % preferred.length];
  }

  function renderDaily() {
    const item = state.daily;
    if (!item) return;
    els.dailyTitle.textContent = item.title || "ذكر اليوم";
    els.dailyText.textContent = item.text;
    els.dailySource.textContent = [item.source, item.reference].filter(Boolean).join(" · ");
    updateFavoriteButtons();
    $("#featuredCard").onclick = function (event) {
      if (event.target.closest("button")) return;
      openItem(item);
    };
  }

  function renderChips() {
    els.chips.replaceChildren();
    const all = [{ id: "all", title: "الكل", icon: "✧" }].concat(state.catalogue.categories);
    all.forEach(category => {
      const button = node("button", "category-chip", (category.icon ? category.icon + " " : "") + category.title);
      button.type = "button";
      button.classList.toggle("is-active", state.category === category.id);
      button.setAttribute("aria-pressed", state.category === category.id ? "true" : "false");
      button.addEventListener("click", () => {
        state.category = category.id;
        state.visibleLimit = 8;
        renderChips();
        renderLibrary();
        haptic("light");
      });
      els.chips.appendChild(button);
    });
  }

  function filteredItems() {
    const term = normalize(state.query);
    return state.catalogue.items.filter(item => {
      const categoryMatch = state.category === "all" || item.category === state.category;
      const text = normalize([item.title, item.text, item.source, item.reference, item.narrator, item.virtue].join(" "));
      return categoryMatch && (!term || text.includes(term));
    });
  }

  function makeCard(item) {
    const article = node("article", "content-card");
    const open = node("button", "card-open");
    open.type = "button";
    open.setAttribute("aria-label", "فتح " + (item.title || "المحتوى"));
    const top = node("div", "card-top");
    const icon = node("span", "card-category-icon", (categoryFor(item.category) || {}).icon || "۞");
    const type = node("span", "card-category", categoryTitle(item.category));
    top.append(icon, type);
    const title = node("h3", "card-title", item.title || "محتوى");
    const excerpt = node("p", "card-excerpt", item.text);
    const meta = node("span", "card-source", "📚 " + [item.source, item.reference].filter(Boolean).join(" · "));
    const arrow = node("span", "card-arrow", "↗");
    open.append(top, title, excerpt, meta, arrow);
    open.addEventListener("click", () => openItem(item));
    const save = node("button", "card-save", saved(item) ? "★" : "☆");
    save.type = "button";
    save.setAttribute("aria-label", saved(item) ? "إزالة من المحفوظات" : "حفظ");
    save.classList.toggle("is-saved", saved(item));
    save.addEventListener("click", () => toggleFavorite(item));
    article.append(open, save);
    return article;
  }

  function renderLibrary() {
    const items = filteredItems();
    const visible = items.slice(0, state.visibleLimit);
    els.list.replaceChildren();
    if (!items.length) {
      els.list.appendChild(node("div", "empty-state", state.query ? "ما لقيناش نتائج بهذا البحث. جرّب كلمة أخرى." : "هذا القسم فارغ حالياً."));
    } else {
      visible.forEach(item => els.list.appendChild(makeCard(item)));
    }
    els.listHeading.textContent = state.query ? "نتائج البحث" : (state.category === "all" ? "كل المحتوى" : categoryTitle(state.category));
    els.count.textContent = items.length + " مادة";
    els.loadMore.hidden = items.length <= state.visibleLimit;
    els.loadMore.textContent = "عرض المزيد · " + (items.length - state.visibleLimit) + " متبقية";
  }

  function renderSaved() {
    els.savedList.replaceChildren();
    const items = state.catalogue.items.filter(item => state.favorites.has(item.id));
    if (!items.length) {
      els.savedList.appendChild(node("div", "empty-state", "ما حفظت حتى مادة بعد. اضغط ☆ على أي ذكر أو حديث باش تلقاه هنا."));
      return;
    }
    items.forEach(item => els.savedList.appendChild(makeCard(item)));
  }

  function openItem(item) {
    state.selected = item;
    els.modalCategory.textContent = categoryTitle(item.category);
    els.modalTitle.textContent = item.title || "محتوى";
    els.modalText.textContent = item.text;
    const meta = [];
    if (item.repeat) meta.push("التكرار الوارد: " + item.repeat);
    if (item.narrator) meta.push("الراوي: " + item.narrator);
    if (item.source) meta.push("المصدر: " + item.source + (item.reference ? " · " + item.reference : ""));
    if (item.grading) meta.push("الدرجة: " + item.grading);
    els.modalMeta.replaceChildren();
    meta.forEach(line => els.modalMeta.appendChild(node("span", "meta-line", line)));
    els.modalVirtue.hidden = !item.virtue;
    els.modalVirtue.textContent = item.virtue || "";
    els.sourceLink.hidden = !item.source_url;
    if (item.source_url) els.sourceLink.href = item.source_url;
    updateFavoriteButtons();
    els.modal.hidden = false;
    document.body.classList.add("modal-open");
    try { if (tg && tg.BackButton) tg.BackButton.show(); } catch (_) {}
    haptic("light");
  }

  function closeModal() {
    els.modal.hidden = true;
    document.body.classList.remove("modal-open");
    state.selected = null;
    try { if (tg && tg.BackButton && state.view === "home") tg.BackButton.hide(); } catch (_) {}
  }

  function navigate(view) {
    state.view = view;
    const isHome = view === "home";
    $("#homeView").hidden = !isHome;
    $(".quick-strip").hidden = !isHome;
    $("#featuredSection").hidden = !isHome;
    $("#librarySection").hidden = !(isHome || view === "library");
    $("#counterSection").hidden = view !== "counter";
    $("#savedView").hidden = view !== "saved";
    $("#aboutView").hidden = view !== "about";
    $$(".nav-item").forEach(button => {
      const active = button.dataset.view === view;
      button.classList.toggle("is-active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    if (view === "saved") renderSaved();
    if (view === "library") {
      renderChips();
      renderLibrary();
      setTimeout(() => els.search.focus({ preventScroll: true }), 80);
    }
    try {
      if (tg && tg.BackButton) {
        if (view === "home" && !els.modal.hidden) tg.BackButton.show();
        else if (view === "home") tg.BackButton.hide();
        else tg.BackButton.show();
      }
    } catch (_) {}
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function getCounterKey(item) {
    return item ? item.id + ":" + new Date().toISOString().slice(0, 10) : "daily";
  }

  function setCounterItem(item) {
    state.counterItem = item || state.daily;
    if (!state.counterItem) return;
    const repeat = Number(state.counterItem.repeat);
    state.counterTarget = Number.isFinite(repeat) && repeat > 0 ? repeat : 100;
    const key = getCounterKey(state.counterItem);
    state.counterValue = Number(state.counterData[key] || 0);
    els.counterDhikr.textContent = state.counterItem.text;
    els.counterTarget.textContent = String(state.counterTarget);
    $("#targetLabel").textContent = String(state.counterTarget);
    updateCounter();
  }

  function updateCounter() {
    els.counterValue.textContent = String(state.counterValue);
    const ratio = state.counterTarget ? Math.min(100, state.counterValue / state.counterTarget * 100) : 0;
    els.counterProgress.style.width = ratio + "%";
    const complete = state.counterTarget > 0 && state.counterValue >= state.counterTarget;
    els.counterButton.classList.toggle("is-complete", complete);
    els.counterHint.textContent = complete ? "أتممت العدد الذي اخترته، تقبّل الله منك." : "استحضر المعنى، وخذ وقتك في الذكر.";
    const key = getCounterKey(state.counterItem);
    state.counterData[key] = state.counterValue;
    storage.set("counter", state.counterData);
  }

  async function copyText(text) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const area = node("textarea");
        area.value = text;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        document.execCommand("copy");
        area.remove();
      }
      toast("تم نسخ النص");
      haptic("success");
    } catch (_) { toast("تعذّر النسخ على هذا الجهاز"); }
  }

  function renderAll() {
    updateDate();
    renderDaily();
    renderChips();
    renderLibrary();
    renderSaved();
    updateSavedBadge();
    setCounterItem(state.daily);
    els.summary.textContent = state.catalogue.items.length + " مادة · " + state.catalogue.categories.length + " أقسام";
  }

  async function load() {
    try {
      const headers = {};
      if (tg && tg.initData) headers.Authorization = "tma " + tg.initData;
      const response = await fetch("/api/adhkar", { headers: headers, cache: "no-store" });
      if (!response.ok) throw new Error(response.status === 401 ? "telegram" : "network");
      const payload = await response.json();
      if (!payload || !Array.isArray(payload.categories) || !Array.isArray(payload.items)) throw new Error("shape");
      state.catalogue = payload;
      state.daily = chooseDaily();
      renderAll();
      setConnection(true, "المكتبة متصلة");
      $("#featuredCard").classList.add("is-ready");
    } catch (error) {
      setConnection(false, "تعذّر الاتصال");
      const message = error.message === "telegram"
        ? "افتح المكتبة من زر «مكتبتي» داخل بوت أَثَر حتى نقدروا نتحققوا من جلسة تيليجرام."
        : "ما قدرناش نحمّل المحتوى حالياً. تحقق من الاتصال وحاول مجدداً.";
      els.list.replaceChildren(node("div", "empty-state error-state", message));
      els.dailyText.textContent = "المحتوى سيظهر عند الاتصال بمكتبة أَثَر.";
      els.count.textContent = "غير متصل";
      els.summary.textContent = "بانتظار الاتصال";
      const retry = node("button", "retry-button", "إعادة المحاولة");
      retry.type = "button";
      retry.addEventListener("click", load);
      els.list.appendChild(retry);
    }
  }

  $("#themeButton").addEventListener("click", () => {
    setTheme(state.theme === "dark" ? "light" : "dark");
    haptic("light");
  });
  $$(".nav-item").forEach(button => button.addEventListener("click", () => navigate(button.dataset.view)));
  $$("[data-view]").filter(button => !button.classList.contains("nav-item")).forEach(button => button.addEventListener("click", () => navigate(button.dataset.view)));
  $$("[data-category]").forEach(button => button.addEventListener("click", () => {
    state.category = button.dataset.category;
    state.query = "";
    els.search.value = "";
    state.visibleLimit = 8;
    navigate("library");
    renderChips();
    renderLibrary();
  }));
  $$("[data-home]").forEach(button => button.addEventListener("click", event => {
    event.preventDefault();
    navigate("home");
  }));

  els.search.addEventListener("input", () => {
    state.query = els.search.value;
    state.visibleLimit = 8;
    els.clear.hidden = !state.query;
    renderLibrary();
  });
  els.clear.addEventListener("click", () => {
    els.search.value = "";
    state.query = "";
    state.visibleLimit = 8;
    els.clear.hidden = true;
    renderLibrary();
    els.search.focus();
  });
  els.loadMore.addEventListener("click", () => {
    state.visibleLimit += 8;
    renderLibrary();
  });
  els.dailySave.addEventListener("click", () => toggleFavorite(state.daily));
  els.copy.addEventListener("click", () => state.daily && copyText(state.daily.text));
  $("#closeModal").addEventListener("click", closeModal);
  els.modal.addEventListener("click", event => { if (event.target === els.modal) closeModal(); });
  $("#modalSave").addEventListener("click", () => toggleFavorite(state.selected));
  $("#modalCopy").addEventListener("click", () => state.selected && copyText(state.selected.text));
  $("#modalCounter").addEventListener("click", () => {
    setCounterItem(state.selected);
    closeModal();
    navigate("counter");
  });
  els.counterButton.addEventListener("click", () => {
    state.counterValue += 1;
    updateCounter();
    haptic("selection");
  });
  $("#counterReset").addEventListener("click", () => {
    state.counterValue = 0;
    updateCounter();
    toast("تمت إعادة العداد");
  });
  $("#counterTargetButton").addEventListener("click", () => {
    const choices = [33, 100, 300, 0];
    const next = choices[(choices.indexOf(state.counterTarget) + 1) % choices.length];
    state.counterTarget = next;
    els.counterTarget.textContent = next ? String(next) : "مفتوح";
    $("#targetLabel").textContent = next ? String(next) : "مفتوح";
    els.counterHint.textContent = "هذا هدف شخصي للمتابعة، وليس تحديداً لعدد شرعي.";
    updateCounter();
    toast(next ? "تغيّر هدفك الشخصي" : "العدّ مفتوح بلا هدف");
  });

  if (tg && tg.BackButton) {
    tg.BackButton.onClick(() => {
      if (!els.modal.hidden) closeModal();
      else navigate("home");
    });
  }
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && !els.modal.hidden) closeModal();
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      navigate("library");
      els.search.focus();
    }
  });

  setTheme(state.theme);
  updateDate();
  updateSavedBadge();
  load();
}());