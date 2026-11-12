const state = {
  categories: [],
  activeCategory: "Featured Cross-Store",
  currentQuery: "",
  catalogItems: [],
  selectedItem: null,
  compareData: null,
  activeReviewPlatform: "ALL",
};

function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatInr(num) {
  if (num === null || num === undefined || Number.isNaN(Number(num))) return "N/A";
  return "₹" + Number(num).toLocaleString("en-IN");
}

function showView(view) {
  document.getElementById("catalog-view").classList.toggle("hidden", view !== "catalog");
  document.getElementById("compare-view").classList.toggle("hidden", view !== "compare");
}

async function init() {
  document.getElementById("search-form").addEventListener("submit", (e) => {
    e.preventDefault();
    state.currentQuery = document.getElementById("search-input").value.trim();
    showView("catalog");
    loadCatalog(false);
  });

  document.getElementById("brand-home").addEventListener("click", () => {
    document.getElementById("search-input").value = "";
    state.currentQuery = "";
    state.activeCategory = state.categories[0] || "Featured Cross-Store";
    renderCategories();
    showView("catalog");
    loadCatalog(false);
  });

  document.getElementById("refresh-btn").addEventListener("click", () => {
    if (!document.getElementById("compare-view").classList.contains("hidden") && state.selectedItem) {
      openComparison(state.selectedItem, true);
    } else {
      loadCatalog(true);
    }
  });

  document.getElementById("back-btn").addEventListener("click", () => showView("catalog"));

  document.getElementById("priority-select").addEventListener("change", () => {
    if (state.compareData && state.compareData.analysis) {
      reanalyzeOffers(state.compareData.analysis.offers);
    }
  });

  document.getElementById("compare-refresh-btn").addEventListener("click", () => {
    if (state.selectedItem) openComparison(state.selectedItem, true);
  });

  document.getElementById("custom-offer-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!state.compareData) return;
    const plat = document.getElementById("custom-platform").value;
    const price = Number(document.getElementById("custom-price").value);
    const ratingVal = document.getElementById("custom-rating").value;
    const rcVal = document.getElementById("custom-rating-count").value;
    const delivDaysVal = document.getElementById("custom-delivery-days").value;
    const customUrl = document.getElementById("custom-url").value.trim();
    if (!price || price <= 0) return;

    const existing = (state.compareData.analysis?.offers || []).filter((o) => o.platform !== plat);
    const days = delivDaysVal !== "" ? Number(delivDaysVal) : null;
    existing.push({
      platform: plat,
      title: state.selectedItem?.title || state.compareData.product.title,
      brand: state.selectedItem?.brand || "",
      price_inr: price,
      mrp_inr: price,
      rating: ratingVal ? Number(ratingVal) : null,
      rating_count: rcVal ? Number(rcVal) : 0,
      review_count: 0,
      image_url: state.compareData.product.image_url || "",
      url: customUrl || `https://www.google.com/search?q=${encodeURIComponent(plat + " " + state.compareData.product.title)}`,
      in_stock: true,
      shipping_fee_inr: 0,
      delivery_text: days !== null ? `Est. delivery in ${days} day(s)` : "User-verified quote",
      delivery_days: days,
      return_policy: "Platform Standard Policy",
      seller: `${plat} (User-Verified)`,
      reviews: [],
      is_user_verified: true,
      match_confidence: 100,
    });
    await reanalyzeOffers(existing);
    e.target.reset();
  });

  await loadCategories();
  await loadCatalog(false);
}

async function loadCategories() {
  const res = await fetch("/api/categories");
  state.categories = await res.json();
  if (!state.categories.includes(state.activeCategory)) {
    state.activeCategory = state.categories[0] || "Featured Cross-Store";
  }
  renderCategories();
}

function renderCategories() {
  const strip = document.getElementById("category-strip");
  strip.innerHTML = state.categories
    .map(
      (cat) => `
      <button class="cat-pill ${cat === state.activeCategory && !state.currentQuery ? "active" : ""}"
              data-cat="${escapeHtml(cat)}">
        ${escapeHtml(cat)}
      </button>`
    )
    .join("");

  strip.querySelectorAll(".cat-pill").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.activeCategory = btn.getAttribute("data-cat");
      state.currentQuery = "";
      document.getElementById("search-input").value = "";
      renderCategories();
      showView("catalog");
      loadCatalog(false);
    });
  });
}

async function loadCatalog(refresh) {
  const loading = document.getElementById("catalog-loading");
  const grid = document.getElementById("catalog-grid");
  const meta = document.getElementById("catalog-meta");
  const titleEl = document.getElementById("catalog-title");

  loading.classList.remove("hidden");
  grid.innerHTML = "";
  meta.classList.add("hidden");

  titleEl.textContent = state.currentQuery
    ? `Live Search: "${state.currentQuery}"`
    : state.activeCategory;

  try {
    const params = new URLSearchParams({
      category: state.activeCategory,
      q: state.currentQuery,
      refresh: refresh ? "1" : "0",
    });
    const res = await fetch(`/api/catalog?${params.toString()}`);
    const data = await res.json();
    state.catalogItems = data.items || [];

    if (data.fetched_at) {
      meta.textContent = `${state.catalogItems.length} live items • Fetched ${data.fetched_at}${data.cached ? " (cached)" : ""}`;
      meta.classList.remove("hidden");
    }
    renderCatalogGrid();
  } catch (err) {
    grid.innerHTML = `<div class="card">Failed to load live listings: ${escapeHtml(err.message)}</div>`;
  } finally {
    loading.classList.add("hidden");
  }
}

function renderCatalogGrid() {
  const grid = document.getElementById("catalog-grid");
  if (!state.catalogItems.length) {
    grid.innerHTML = `
      <div class="card" style="grid-column: 1 / -1; text-align:center;">
        No live listings matched that query. Try a broader product name (e.g. "iPhone 15", "boAt Airdopes 141", "Puma Smashic").
      </div>`;
    return;
  }

  grid.innerHTML = state.catalogItems
    .map((item, idx) => {
      const hasDiscount = item.mrp_inr && item.mrp_inr > item.price_inr;
      return `
        <article class="product-card" data-idx="${idx}">
          <div class="product-img-wrap">
            <span class="store-tag">${escapeHtml(item.platform)}</span>
            ${
              item.image_url
                ? `<img src="${escapeHtml(item.image_url)}" alt="${escapeHtml(item.title)}" loading="lazy" />`
                : `<span class="muted small">No Image</span>`
            }
          </div>
          <div class="product-body">
            <div class="product-top-row">
              <span class="brand-label">${escapeHtml(item.brand || "Product")}</span>
              ${
                item.rating
                  ? `<span class="rating-badge">${escapeHtml(item.rating)} ★ (${Number(item.rating_count || 0).toLocaleString("en-IN")})</span>`
                  : `<span class="muted small">New listing</span>`
              }
            </div>
            <h3 class="product-title">${escapeHtml(item.title)}</h3>
            <div class="price-row">
              <span class="price-now">${formatInr(item.price_inr)}</span>
              ${hasDiscount ? `<span class="price-mrp">${formatInr(item.mrp_inr)}</span>` : ""}
            </div>
          </div>
          <div class="card-cta">Compare Across Platforms &amp; Analyze Price →</div>
        </article>`;
    })
    .join("");

  grid.querySelectorAll(".product-card").forEach((card) => {
    card.addEventListener("click", () => {
      const idx = Number(card.getAttribute("data-idx"));
      openComparison(state.catalogItems[idx], false);
    });
  });
}

async function openComparison(item, refresh) {
  state.selectedItem = item;
  state.activeReviewPlatform = "ALL";
  showView("compare");

  const loading = document.getElementById("compare-loading");
  const content = document.getElementById("compare-content");
  loading.classList.remove("hidden");
  content.classList.add("hidden");

  try {
    const priority = document.getElementById("priority-select").value;
    const params = new URLSearchParams({
      title: item.title,
      brand: item.brand || "",
      url: item.url || "",
      platform: item.platform || "",
      priority,
      refresh: refresh ? "1" : "0",
    });
    const res = await fetch(`/api/compare?${params.toString()}`);
    const data = await res.json();
    state.compareData = data;
    renderComparison();
    content.classList.remove("hidden");
  } catch (err) {
    content.innerHTML = `<div class="card">Comparison error: ${escapeHtml(err.message)}</div>`;
    content.classList.remove("hidden");
  } finally {
    loading.classList.add("hidden");
  }
}

async function reanalyzeOffers(offers) {
  const priority = document.getElementById("priority-select").value;
  const res = await fetch("/api/reanalyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ offers, priority }),
  });
  const data = await res.json();
  if (data.analysis && state.compareData) {
    state.compareData.analysis = data.analysis;
    renderComparison();
  }
}

function renderComparison() {
  const data = state.compareData;
  if (!data) return;

  const prod = data.product || {};
  const analysis = data.analysis || {};
  const offers = analysis.offers || [];

  // 1. Hero
  document.getElementById("compare-hero").innerHTML = `
    ${
      prod.image_url || state.selectedItem?.image_url
        ? `<img class="hero-img" src="${escapeHtml(prod.image_url || state.selectedItem.image_url)}" alt="${escapeHtml(prod.title)}" />`
        : ""
    }
    <div class="hero-info">
      <span class="brand-label">${escapeHtml(prod.brand || "Verified Product")}</span>
      <h2>${escapeHtml(prod.title)}</h2>
      <p class="muted small">Cross-platform exact search query: <code>${escapeHtml(data.query)}</code></p>
    </div>
  `;

  // 2. Platform Status Strip
  document.getElementById("compare-fetched-at").textContent =
    data.fetched_at ? `Live fetched: ${data.fetched_at}${data.cached ? " (cached)" : ""}` : "";

  document.getElementById("platform-status-list").innerHTML = (data.platform_status || [])
    .map((st) => {
      const icon =
        st.status === "matched" ? "✓" : st.status === "blocked_by_antibot" ? "⚠" : "—";
      const linkHtml = st.search_url
        ? `<a href="${escapeHtml(st.search_url)}" target="_blank" rel="noopener noreferrer">${
            st.status === "matched" ? "View ↗" : "Check on Store ↗"
          }</a>`
        : "";
      return `
        <div class="status-pill ${escapeHtml(st.status)}">
          <span><strong>${icon} ${escapeHtml(st.platform)}</strong>: ${escapeHtml(st.message)}</span>
          ${linkHtml}
        </div>`;
    })
    .join("");

  // 3. Price Metrics Row
  const ps = analysis.price_summary;
  const picks = analysis.picks || {};
  const metricsRow = document.getElementById("price-metrics-row");
  if (ps) {
    metricsRow.innerHTML = `
      <div class="metric-box">
        <div class="metric-label">Lowest Live Price</div>
        <div class="metric-val green">${formatInr(ps.lowest_price_inr)}</div>
        <div class="muted small">on ${escapeHtml(picks.lowest_price?.platform || "")}</div>
      </div>
      <div class="metric-box">
        <div class="metric-label">Highest Live Price</div>
        <div class="metric-val">${formatInr(ps.highest_price_inr)}</div>
        <div class="muted small">Average: ${formatInr(ps.average_price_inr)}</div>
      </div>
      <div class="metric-box">
        <div class="metric-label">Price Spread &amp; Savings</div>
        <div class="metric-val amber">${formatInr(ps.price_spread_inr)}</div>
        <div class="muted small">Up to ${escapeHtml(ps.price_spread_pct)}% cheaper</div>
      </div>
      <div class="metric-box">
        <div class="metric-label">Recommended Platform</div>
        <div class="metric-val sky">${escapeHtml(picks.best_overall?.platform || "N/A")}</div>
        <div class="muted small">Score: ${escapeHtml(picks.best_overall?.score || 0)}/100</div>
      </div>
    `;
  } else {
    metricsRow.innerHTML = "";
  }

  // 4. Verdict Card
  const verdictCard = document.getElementById("verdict-card");
  verdictCard.innerHTML = `
    <span class="verdict-badge">Price &amp; Value Analysis Verdict</span>
    <p class="verdict-text">${analysis.recommendation_summary || "No matched offers to analyze."}</p>
    ${
      picks.best_overall
        ? `<div class="quick-picks">
            <span class="pick-chip">🏆 <strong>Best Overall:</strong> ${escapeHtml(picks.best_overall.platform)} (${formatInr(picks.best_overall.price_inr)})</span>
            <span class="pick-chip">💰 <strong>Cheapest:</strong> ${escapeHtml(picks.lowest_price.platform)} (${formatInr(picks.lowest_price.price_inr)})</span>
            <span class="pick-chip">⭐ <strong>Highest Rated:</strong> ${escapeHtml(picks.best_rated.platform)} (${picks.best_rated.rating ? picks.best_rated.rating + " ★" : "N/A"})</span>
            <span class="pick-chip">🚚 <strong>Best Delivery:</strong> ${escapeHtml(picks.fastest_delivery.platform)} (${escapeHtml(picks.fastest_delivery.delivery_text || "Standard")})</span>
          </div>`
        : ""
    }
  `;

  // 5. Visual Comparison Bars
  const barsContainer = document.getElementById("comparison-bars");
  if (offers.length) {
    barsContainer.innerHTML = offers
      .map((o) => {
        const score = o.scores?.overall || 0;
        return `
          <div class="bar-item">
            <div><strong>${escapeHtml(o.platform)}</strong></div>
            <div class="bar-track">
              <div class="bar-fill" style="width:${Math.min(100, Math.max(5, score))}%"></div>
            </div>
            <div class="small">
              <strong>${formatInr(o.price_inr)}</strong> • Score <strong>${score}/100</strong>
            </div>
          </div>`;
      })
      .join("");
  } else {
    barsContainer.innerHTML = `<p class="muted small">No platform offers available to chart.</p>`;
  }

  // 6. Side-by-Side Offer Cards
  const offersList = document.getElementById("offers-list");
  if (!offers.length) {
    offersList.innerHTML = `<div class="card">No accessible platforms returned an exact match for this product.</div>`;
  } else {
    offersList.innerHTML = offers
      .map((o) => {
        const breakdown = o.rating_breakdown || {};
        const hasBreakup = Object.keys(breakdown).length > 0;
        const breakupText = hasBreakup
          ? `5★:${breakdown["5"] || 0} | 4★:${breakdown["4"] || 0} | 3★:${breakdown["3"] || 0} | 2★:${breakdown["2"] || 0} | 1★:${breakdown["1"] || 0}`
          : "";

        return `
          <div class="offer-card ${o.is_recommended ? "recommended" : ""}">
            <div class="offer-header">
              <div>
                <div class="offer-platform">${escapeHtml(o.platform)}</div>
                <div class="badge-row">
                  ${o.is_recommended ? `<span class="pill pill-best">RECOMMENDED BUY</span>` : ""}
                  ${o.is_lowest_price ? `<span class="pill pill-low">LOWEST PRICE</span>` : ""}
                  ${o.is_user_verified ? `<span class="pill pill-user">USER-VERIFIED</span>` : ""}
                </div>
              </div>
              <div class="offer-price-block">
                <div class="offer-price">${formatInr(o.price_inr)}</div>
                ${
                  o.mrp_inr > o.price_inr
                    ? `<div class="small muted"><s>${formatInr(o.mrp_inr)}</s> (${o.discount_pct}% off)</div>`
                    : `<div class="small muted">MRP</div>`
                }
              </div>
            </div>

            <div class="small muted" title="${escapeHtml(o.title)}">
              Matched listing: <strong style="color:var(--text)">${escapeHtml(o.title)}</strong>
              ${o.match_confidence ? ` (${escapeHtml(o.match_confidence)}% match)` : ""}
            </div>

            <div class="offer-specs">
              <div>
                <div class="spec-label">Platform Rating</div>
                <div class="spec-val">${o.rating ? `${escapeHtml(o.rating)} ★` : "Unrated"} (${Number(o.rating_count || 0).toLocaleString("en-IN")})</div>
              </div>
              <div>
                <div class="spec-label">Delivery &amp; Shipping</div>
                <div class="spec-val">${escapeHtml(o.delivery_text || "Standard")}${o.shipping_fee_inr === 0 ? " (Free)" : o.shipping_fee_inr ? ` (+${formatInr(o.shipping_fee_inr)})` : ""}</div>
              </div>
              <div>
                <div class="spec-label">Return Policy</div>
                <div class="spec-val">${escapeHtml(o.return_policy || "Standard Policy")}</div>
              </div>
              <div>
                <div class="spec-label">Seller &amp; Stock</div>
                <div class="spec-val">${escapeHtml(o.seller || o.platform)} • ${o.in_stock ? "In Stock" : "Out of Stock"}</div>
              </div>
            </div>

            ${breakupText ? `<div class="muted small">Rating split: ${escapeHtml(breakupText)}</div>` : ""}

            <div class="score-breakdown">
              <div class="score-mini"><div class="muted">Price</div><strong>${o.scores?.price ?? 0}</strong></div>
              <div class="score-mini"><div class="muted">Rating</div><strong>${o.scores?.rating ?? 0}</strong></div>
              <div class="score-mini"><div class="muted">Delivery</div><strong>${o.scores?.delivery ?? 0}</strong></div>
              <div class="score-mini"><div class="muted">Overall</div><strong style="color:#34d399">${o.scores?.overall ?? 0}</strong></div>
            </div>

            <div class="offer-actions">
              <a href="${escapeHtml(o.url)}" target="_blank" rel="noopener noreferrer" class="btn btn-emerald">
                Open on ${escapeHtml(o.platform)} ↗
              </a>
            </div>
          </div>`;
      })
      .join("");
  }

  // 7. Platform-Specific Reviews Section
  renderReviewsSection(offers);
}

function renderReviewsSection(offers) {
  const tabsEl = document.getElementById("review-platform-tabs");
  const sentimentEl = document.getElementById("review-sentiment-strip");
  const containerEl = document.getElementById("reviews-container");

  const platforms = offers.map((o) => o.platform);
  if (state.activeReviewPlatform !== "ALL" && !platforms.includes(state.activeReviewPlatform)) {
    state.activeReviewPlatform = "ALL";
  }

  tabsEl.innerHTML = [
    `<button class="cat-pill ${state.activeReviewPlatform === "ALL" ? "active" : ""}" data-plat="ALL">All Matched Stores</button>`,
    ...offers.map(
      (o) => `
        <button class="cat-pill ${state.activeReviewPlatform === o.platform ? "active" : ""}" data-plat="${escapeHtml(o.platform)}">
          ${escapeHtml(o.platform)} (${(o.reviews || []).length})
        </button>`
    ),
  ].join("");

  tabsEl.querySelectorAll(".cat-pill").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.activeReviewPlatform = btn.getAttribute("data-plat");
      renderReviewsSection(offers);
    });
  });

  const filteredOffers =
    state.activeReviewPlatform === "ALL"
      ? offers
      : offers.filter((o) => o.platform === state.activeReviewPlatform);

  // Per-platform sentiment & keyword highlights
  sentimentEl.innerHTML = filteredOffers
    .map((o) => {
      const ra = o.review_analysis || {};
      const pros = (ra.pros_mentioned || []).join(", ");
      const cons = (ra.cons_mentioned || []).join(", ");
      return `
        <div class="sentiment-box">
          <strong>${escapeHtml(o.platform)} Review Summary</strong>
          <div class="muted small" style="margin-top:4px;">
            ${
              ra.sample_count
                ? `Sampled ${ra.sample_count} verified reviews • ${ra.positive_count} positive, ${ra.neutral_count} neutral, ${ra.negative_count} negative`
                : `Platform rating: ${o.rating ? o.rating + " ★" : "N/A"} (${Number(o.rating_count || 0).toLocaleString("en-IN")} ratings) • No text reviews exposed on page`
            }
          </div>
          ${pros ? `<div class="small" style="color:#34d399;margin-top:4px;">Pros mentioned: ${escapeHtml(pros)}</div>` : ""}
          ${cons ? `<div class="small" style="color:#fbbf24;margin-top:2px;">Concerns mentioned: ${escapeHtml(cons)}</div>` : ""}
        </div>`;
    })
    .join("");

  // Flatten reviews only for the selected matched platform(s)
  const allReviews = [];
  filteredOffers.forEach((o) => {
    (o.reviews || []).forEach((r) => {
      allReviews.push({ ...r, platform: o.platform });
    });
  });

  if (!allReviews.length) {
    containerEl.innerHTML = `
      <div class="muted small">
        No individual buyer text reviews were exposed on the selected store page(s) for this product — aggregate ratings and counts above are live from the store.
      </div>`;
    return;
  }

  containerEl.innerHTML = allReviews
    .map(
      (r) => `
      <div class="review-card">
        <div class="review-head">
          <div>
            <strong>${escapeHtml(r.user || "Verified Buyer")}</strong>
            <span class="review-plat">${escapeHtml(r.platform)}</span>
          </div>
          <span class="review-stars">${escapeHtml(r.rating)} ★</span>
        </div>
        ${r.title ? `<div class="small"><strong>${escapeHtml(r.title)}</strong></div>` : ""}
        <p class="review-comment">“${escapeHtml(r.comment)}”</p>
        ${r.date ? `<div class="muted small" style="margin-top:6px;">${escapeHtml(r.date)}</div>` : ""}
      </div>`
    )
    .join("");
}

init();

