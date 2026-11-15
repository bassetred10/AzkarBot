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

  function renderCategories(categories, items) {
    categoryRoot.innerHTML = "";
    categories.forEach(function (category) {
      const count = items.filter(function (item) { return item.category === category.id; }).length;
      const button = document.createElement("button");
      button.className = "category-card";
      button.type = "button";
      button.innerHTML = '<span class="category-icon">' + category.icon + '</span><span><strong>' + category.title + '</strong><br><small>' + count + ' أذكار متاحة</small></span>';
      button.addEventListener("click", function () {
        const chosen = items.filter(function (item) { return item.category === category.id; });
        showCategory(category, chosen);
      });
      categoryRoot.appendChild(button);
    });
  }

  function showCategory(category, items) {
    categoryRoot.innerHTML = "";
    if (!items.length) {
      categoryRoot.innerHTML = '<div class="empty-state">سيُضاف المحتوى المراجع إلى هذا القسم قريباً.</div>';
      return;
    }
    items.forEach(function (item) {
      const card = document.createElement("article");
      card.className = "category-card";
      card.innerHTML = '<span class="eyebrow">' + category.title + '</span><strong>' + item.text + '</strong><small>' + item.source + ' · ' + item.reference + ' · التكرار ' + item.repeat + '</small>';
      categoryRoot.appendChild(card);
    });
  }

  async function load() {
    try {
      const headers = {};
      if (tg && tg.initData) headers.Authorization = "tma " + tg.initData;
      const response = await fetch("/api/adhkar", { headers: headers });
      if (!response.ok) throw new Error("Open the Mini App from Telegram");
      const payload = await response.json();
      const items = payload.items || [];
      renderCategories(payload.categories || [], items);
      itemCount.textContent = items.length + " أذكار موثقة";
      const featured = items[0];
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
      categoryRoot.innerHTML = '<div class="empty-state">افتح أَثَر من داخل تيليجرام لعرض مكتبة الأذكار.</div>';
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
