(function () {
  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) {
    tg.ready();
    tg.expand();
  }

  const categoryRoot = document.getElementById("categories");
  const itemCount = document.getElementById("itemCount");
  const dailyText = document.getElementById("dailyText");
  const dailySource = document.getElementById("dailySource");
  const copyButton = document.getElementById("copyButton");
  let catalogue = null;

  function textElement(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    element.textContent = text;
    return element;
  }

  function renderCategories(categories, items) {
    categoryRoot.replaceChildren();
    categories.forEach(function (category) {
      const count = items.filter(function (item) { return item.category === category.id; }).length;
      const button = document.createElement("button");
      button.className = "category-card";
      button.type = "button";
      button.appendChild(textElement("span", "category-icon", category.icon));
      const copy = document.createElement("span");
      copy.appendChild(textElement("strong", "", category.title));
      copy.appendChild(textElement("small", "", count + " عناصر متاحة"));
      button.appendChild(copy);
      button.addEventListener("click", function () {
        const chosen = items.filter(function (item) { return item.category === category.id; });
        showCategory(category, chosen);
      });
      categoryRoot.appendChild(button);
    });
  }

  function showCategory(category, items) {
    categoryRoot.replaceChildren();
    const back = textElement("button", "category-card", "↩ العودة إلى الأقسام");
    back.type = "button";
    back.addEventListener("click", function () {
      renderCategories(catalogue.categories || [], catalogue.items || []);
    });
    categoryRoot.appendChild(back);

    if (!items.length) {
      categoryRoot.appendChild(textElement("div", "empty-state", "سيُضاف المحتوى المراجع إلى هذا القسم قريباً."));
      return;
    }

    items.forEach(function (item) {
      const card = document.createElement("article");
      card.className = "category-card";
      card.appendChild(textElement("span", "eyebrow", category.title));
      card.appendChild(textElement("strong", "dhikr-text", item.text));
      const repeatLabel = item.repeat ? "التكرار: " + item.repeat : "حديث نبوي";
      card.appendChild(textElement("small", "", item.source + " · " + item.reference + " · " + repeatLabel));
      const sourceLink = document.createElement("a");
      sourceLink.href = item.source_url;
      sourceLink.target = "_blank";
      sourceLink.rel = "noopener noreferrer";
      sourceLink.textContent = "فتح المصدر";
      sourceLink.className = "section-note";
      card.appendChild(sourceLink);
      categoryRoot.appendChild(card);
    });
  }

  async function load() {
    try {
      const headers = {};
      if (tg && tg.initData) headers.Authorization = "tma " + tg.initData;
      const response = await fetch("/api/adhkar", { headers: headers });
      if (!response.ok) throw new Error("Open the Mini App from Telegram");
      catalogue = await response.json();
      const items = catalogue.items || [];
      renderCategories(catalogue.categories || [], items);
      itemCount.textContent = items.length + " عناصر موثقة";
      const featured = items.find(function (item) { return item.type === "dhikr"; });
      if (featured) {
        dailyText.textContent = featured.text;
        dailySource.textContent = featured.source + " · " + featured.reference;
        copyButton.onclick = async function () {
          try {
            await navigator.clipboard.writeText(featured.text);
            copyButton.textContent = "تم النسخ ✓";
          } catch (error) {
            copyButton.textContent = "تعذر النسخ";
          }
        };
      }
    } catch (error) {
      categoryRoot.replaceChildren(textElement("div", "empty-state", "افتح أَثَر من داخل تيليجرام لعرض مكتبة الأذكار."));
      itemCount.textContent = "بانتظار الاتصال";
      dailyText.textContent = "المحتوى الموثّق سيظهر عند فتح المكتبة من تيليجرام.";
      dailySource.textContent = "";
    }
  }

  document.getElementById("themeButton").addEventListener("click", function () {
    document.body.classList.toggle("light");
  });

  load();
}());
