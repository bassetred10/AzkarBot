(function () {
  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) { tg.ready(); tg.expand(); }

  const root = document.getElementById("categories");
  const count = document.getElementById("itemCount");
  const search = document.getElementById("searchInput");
  const dailyText = document.getElementById("dailyText");
  const dailySource = document.getElementById("dailySource");
  const copyButton = document.getElementById("copyButton");
  let catalogue = { categories: [], items: [] };

  function node(tag, className, value) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    el.textContent = value || "";
    return el;
  }

  function normalized(value) {
    return (value || "").toLowerCase().replace(/[\u064B-\u065F\u0670]/g, "").trim();
  }

  function renderCategories() {
    root.replaceChildren();
    catalogue.categories.forEach(function (category) {
      const total = catalogue.items.filter(function (item) { return item.category === category.id; }).length;
      const button = node("button", "category-card");
      button.type = "button";
      button.appendChild(node("span", "category-icon", category.icon));
      const copy = document.createElement("span");
      copy.appendChild(node("strong", "", category.title));
      copy.appendChild(node("small", "", total + " عناصر"));
      button.appendChild(copy);
      button.addEventListener("click", function () { renderItems(catalogue.items.filter(function (item) { return item.category === category.id; }), category.title); });
      root.appendChild(button);
    });
  }

  function renderItems(items, heading) {
    root.replaceChildren();
    const back = node("button", "back-card", "← رجوع إلى الأقسام");
    back.type = "button";
    back.addEventListener("click", renderCategories);
    root.appendChild(back);
    if (!items.length) {
      root.appendChild(node("div", "empty-state", "لا توجد نتائج في هذا القسم."));
      return;
    }
    items.forEach(function (item) {
      const card = document.createElement("article");
      card.className = "reading-card";
      card.appendChild(node("span", "eyebrow", heading || item.title || "محتوى"));
      card.appendChild(node("h3", "reading-title", item.title || "محتوى"));
      card.appendChild(node("p", "reading-text", item.text));
      if (item.repeat) card.appendChild(node("small", "reading-meta", "التكرار: " + item.repeat));
      card.appendChild(node("small", "reading-meta", "📚 " + item.source + " · رقم " + item.reference));
      if (item.grading) card.appendChild(node("small", "reading-meta", "الدرجة: " + item.grading));
      if (item.virtue) card.appendChild(node("p", "reading-virtue", item.virtue));
      root.appendChild(card);
    });
  }

  function searchItems() {
    const term = normalized(search.value);
    if (!term) { renderCategories(); return; }
    const filtered = catalogue.items.filter(function (item) {
      return normalized([item.title, item.text, item.source, item.reference].join(" ")).includes(term);
    });
    renderItems(filtered, "نتائج البحث");
    count.textContent = filtered.length + " نتيجة";
  }

  async function load() {
    try {
      const headers = {};
      if (tg && tg.initData) headers.Authorization = "tma " + tg.initData;
      const response = await fetch("/api/adhkar", { headers: headers });
      if (!response.ok) throw new Error("Open inside Telegram");
      catalogue = await response.json();
      renderCategories();
      count.textContent = catalogue.items.length + " عناصر";
      const featured = catalogue.items.find(function (item) { return item.type === "dhikr"; });
      if (featured) {
        dailyText.textContent = featured.text;
        dailySource.textContent = featured.source + " · " + featured.reference;
        copyButton.addEventListener("click", async function () {
          try {
            await navigator.clipboard.writeText(featured.text);
            copyButton.textContent = "تم النسخ ✓";
          } catch (_) { copyButton.textContent = "تعذر النسخ"; }
        });
      }
    } catch (_) {
      root.replaceChildren(node("div", "empty-state", "افتح أَثَر من داخل تيليجرام لعرض المكتبة."));
      count.textContent = "بانتظار الاتصال";
      dailyText.textContent = "المحتوى الموثق سيظهر عند فتح المكتبة من تيليجرام.";
      dailySource.textContent = "";
    }
  }

  search.addEventListener("input", searchItems);
  document.getElementById("themeButton").addEventListener("click", function () { document.body.classList.toggle("light"); });
  load();
}());
