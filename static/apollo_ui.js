(() => {
  const state = {
    activeTab: "overview",
    budgetMonth: "",
    budgetFocus: true,
    summary: {},
    groups: [],
    transactions: [],
    accounts: [],
    paychecks: [],
    goals: [],
    insights: { summary: "", sources: [] },
    graph: {
      data: null,
      fingerprint: "",
      selectedNodeId: "",
      autoRefreshHandle: null,
      lastLoadedAt: 0,
    },
    ocrDashboardPreviewUrl: "",
    ocrDashboardExamples: {},
    swing: { latest: null, selectedTicker: "", selectedProposal: null, account: null, reportOpen: false, pretrade: null, dailyReport: null, dailyReportOpen: false },
    loanReview: { session: null },
  };

  const byId = (id) => document.getElementById(id);
  const apiUrl = (path) => {
    if (typeof window.__apolloApiUrl === "function") return window.__apolloApiUrl(path);
    return path;
  };
  const quickLinks = Array.from(document.querySelectorAll(".quick-link"));
  const navItems = Array.from(document.querySelectorAll(".nav-item"));
  const panels = Array.from(document.querySelectorAll(".tab-panel"));

  const statusEl = byId("status");
  const modelEl = byId("model");
  const budgetMonthInput = byId("budget-month");
  const budgetFocusToggle = byId("budget-focus-toggle");
  const apolloCalendarToggle = byId("apollo-calendar-toggle");
  const apolloScheduleToggle = byId("apollo-schedule-toggle");
  const apolloScheduleOverlay = byId("apollo-schedule-overlay");
  const apolloScheduleClose = byId("apollo-schedule-close");
  const scheduleDetail = byId("schedule-detail");
  const contextChip = byId("context-chip");
  const railToggle = byId("rail-toggle");
  const railOverlay = byId("rail-overlay");
  const topbarRuntimeShell = byId("topbar-runtime-shell");
  const topbarRuntimeToggle = byId("topbar-runtime-toggle");
  const topbarRuntimeMenu = byId("topbar-runtime-menu");
  const rebuildReloadBtn = byId("rebuild-reload-btn");
  const uiCacheBustBtn = byId("ui-cache-bust-btn");
  const runtimeMenuStatus = byId("runtime-menu-status");

  const chatEl = byId("chatWindow");
  const reasoningEl = byId("reasoningWindow");
  const userInput = byId("userInput");
  const sendBtn = byId("sendBtn");
  const loanReviewMode = byId("loan-review-mode");
  const loanReviewStatus = byId("loan-review-status");
  const loanReviewSummary = byId("loan-review-summary");
  const loanReviewMeta = byId("loan-review-meta");
  const loanReviewSample = byId("loan-review-sample");
  const loanReviewImage = byId("loan-review-image");
  const loanReviewCaptureBtn = byId("loan-review-capture");
  const loanReviewCompareBtn = byId("loan-review-compare");
  const loanReviewPauseBtn = byId("loan-review-pause");
  const loanReviewResumeBtn = byId("loan-review-resume");

  const ocrDashFile = byId("ocrd-file");
  const ocrDashGoal = byId("ocrd-goal");
  const ocrDashRun = byId("ocrd-run");
  const ocrDashStatus = byId("ocrd-status");
  const ocrDashOutput = byId("ocrd-output");
  const ocrDashTypicalOutput = byId("ocrd-typical-output");
  const ocrDashSnippet = byId("ocrd-snippet");
  const ocrDashPreview = byId("ocrd-preview");
  const ocrDashMetrics = byId("ocrd-metrics");
  const ocrDashMaxPages = byId("ocrd-max-pages");
  const ocrDashMaxChars = byId("ocrd-max-chars");
  const ocrDashExampleSelect = byId("ocrd-example-select");
  const ocrDashExampleRun = byId("ocrd-example-run");
  const ocrDashTypicalEngine = byId("ocrd-typical-engine");
  const ebayDraftsFrame = byId("ebay-drafts-frame");

  const budgetStatus = byId("budget-status");
  const overviewReady = byId("overview-ready");
  const overviewOverspent = byId("overview-overspent");
  const overviewNet = byId("overview-net");
  const overviewSafe = byId("overview-safe");
  const overviewInsights = byId("overview-insights");

  const budgetReady = byId("budget-ready");
  const budgetOverspent = byId("budget-overspent-count");
  const budgetSubtitle = byId("budget-subtitle");
  const summaryAssigned = byId("summary-assigned");
  const summaryActivity = byId("summary-activity");
  const summaryAvailable = byId("summary-available");
  const summaryIncome = byId("summary-income");
  const summaryExpense = byId("summary-expense");

  const categoryList = byId("budget-category-list");
  const transactionList = byId("budget-transaction-list");
  const accountsList = byId("budget-accounts-list");
  const paychecksList = byId("budget-paycheck-list");
  const dueList = byId("due-list");
  const goalList = byId("goal-list");
  const goalsList = byId("goals-list");
  const budgetGuidance = byId("budget-guidance");
  const budgetContext = byId("budget-context");

  const reportsSummary = byId("reports-summary");
  const reportsList = byId("reports-list");
  const cashflowSummary = byId("cashflow-summary");
  const cashflowTimeline = byId("cashflow-timeline");

  const scenarioSlider = byId("scenario-slider");
  const scenarioAmount = byId("scenario-amount");
  const scenarioImpact = byId("scenario-impact");

  const edCsvFile = byId("ed-csv-file");
  const edCsvImport = byId("ed-csv-import");
  const edCsvStatus = byId("ed-csv-status");
  const excelFile = byId("excel-file");
  const excelPreview = byId("excel-preview");
  const excelImport = byId("excel-import");
  const excelMapping = byId("excel-mapping");
  const excelStatus = byId("excel-status");

  const insightsRefresh = byId("insights-refresh");
  const insightsRefreshStandalone = byId("insights-refresh-standalone");
  const insightsBody = byId("budget-insights-body");
  const insightsSources = byId("budget-insights-sources");

  const graphLaunch = byId("graph-launch");
  const graphRefresh = byId("graph-refresh");
  const graphQuery = byId("graph-query");
  const graphLayout = byId("graph-layout");
  const graphHops = byId("graph-hops");
  const graphLimit = byId("graph-limit");
  const graphMinDegree = byId("graph-min-degree");
  const graphLive = byId("graph-live");
  const graphStatus = byId("graph-status");
  const graphStats = byId("graph-stats");
  const graphCanvas = byId("graph-canvas");
  const graphSelection = byId("graph-selection");
  const graphDetails = byId("graph-details");
  const swingRun = byId("swing-run");
  const swingRefresh = byId("swing-refresh");
  const swingStatus = byId("swing-status");
  const swingList = byId("swing-list");
  const swingRegime = byId("swing-regime");
  const swingBest = byId("swing-best");
  const swingActionable = byId("swing-actionable");
  const swingBlocked = byId("swing-blocked");
  const swingSelectedStrip = byId("swing-selected-strip");
  const swingDetailTitle = byId("swing-detail-title");
  const swingChart = byId("swing-chart");
  const swingDetail = byId("swing-detail");
  const swingJson = byId("swing-json");
  const swingTickers = byId("swing-tickers");
  const swingStatusFilter = byId("swing-status-filter");
  const swingCalendarOpen = byId("swing-calendar-open");
  const swingPretradeCheck = byId("swing-pretrade-check");
  const swingPretradeDecision = byId("swing-pretrade-decision");
  const swingPretradeNote = byId("swing-pretrade-note");
  const swingDailyReportOpen = byId("swing-daily-report-open");
  const swingDailyReport = byId("swing-daily-report");
  const swingDailyReportText = byId("swing-daily-report-text");
  const swingDailyReportPath = byId("swing-daily-report-path");
  const swingWatch = byId("swing-watch");
  const swingPaper = byId("swing-paper");
  const swingReject = byId("swing-reject");
  const swingSimNote = byId("swing-sim-note");
  const swingSimCash = byId("swing-sim-cash");
  const swingSimBalance = byId("swing-sim-balance");
  const swingSimInvested = byId("swing-sim-invested");
  const swingSimReturn = byId("swing-sim-return");
  const swingSimPlan = byId("swing-sim-plan");
  const swingSimSpread = byId("swing-sim-spread");
  const swingSimReport = byId("swing-sim-report");
  const swingSimReportText = byId("swing-sim-report-text");
  const swingSimReportPath = byId("swing-sim-report-path");
  const swingSimReportToggle = byId("swing-sim-report-toggle");
  const swingSimRefresh = byId("swing-sim-refresh");

  const budgetRefresh = byId("budget-refresh");
  const categoryAdd = byId("category-add");
  const transactionAdd = byId("transaction-add");
  const transactionSplit = byId("transaction-split");
  const budgetReceiptBtn = byId("budget-receipt");
  const budgetReceiptFile = byId("budget-receipt-file");
  const accountAdd = byId("account-add");
  const paycheckAdd = byId("paycheck-add");
  const goalAdd = byId("goal-add");
  const goalsAdd = byId("goals-add");

  const overviewAddTransaction = byId("overview-add-transaction");
  const overviewImport = byId("overview-import");
  const overviewRefreshInsights = byId("overview-refresh-insights");
  const overviewStartDialogue = byId("overview-start-dialogue");

  const dialogueRun = byId("dialogue-run");
  const dialogueLog = byId("dialogueLog");

  const currencyFormatter = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  });

  function formatCurrency(value) {
    const num = Number(value) || 0;
    return currencyFormatter.format(num);
  }

  function parseNumber(value) {
    const cleaned = String(value || "").replace(/[^0-9.-]/g, "");
    const parsed = Number(cleaned);
    return Number.isNaN(parsed) ? 0 : parsed;
  }

  function setBudgetStatus(text, isError) {
    if (!budgetStatus) return;
    budgetStatus.textContent = text || "";
    budgetStatus.classList.toggle("negative", Boolean(isError));
  }

  function setChipState(active) {
    if (!budgetFocusToggle) return;
    budgetFocusToggle.classList.toggle("off", !active);
    budgetFocusToggle.textContent = active ? "Budget Focus" : "Focus Off";
    if (contextChip) {
      contextChip.classList.toggle("off", !active);
    }
  }

  function setActiveTab(tab) {
    state.activeTab = tab;
    setTopbarRuntimeOpen(false);
    panels.forEach((panel) => {
      panel.classList.toggle("is-active", panel.dataset.tab === tab);
    });
    navItems.forEach((item) => {
      item.classList.toggle("is-active", item.dataset.tab === tab);
    });
    if (tab === "budget") {
      state.budgetFocus = true;
    }
    setChipState(state.budgetFocus);
    if (tab === "graph") {
      ensureGraphLoaded();
      syncGraphAutoRefresh();
    } else {
      stopGraphAutoRefresh();
    }
    if (tab === "swing") {
      loadSwingLatest();
    }
    if (tab === "ebay" && ebayDraftsFrame) {
      const current = String(ebayDraftsFrame.getAttribute("src") || "").trim().toLowerCase();
      if (!current || current === "about:blank") {
        ebayDraftsFrame.setAttribute("src", apiUrl("/admin/ebay/drafts"));
      }
    }
    if (window.matchMedia && window.matchMedia("(max-width: 1100px)").matches) {
      toggleRail(false);
    }
  }

  function toggleRail(open) {
    document.body.classList.toggle("rail-open", open);
    if (!open) setTopbarRuntimeOpen(false);
  }

  function setTopbarRuntimeOpen(open) {
    const next = Boolean(open);
    if (!topbarRuntimeShell || !topbarRuntimeToggle || !topbarRuntimeMenu) return;
    topbarRuntimeShell.classList.toggle("open", next);
    topbarRuntimeToggle.setAttribute("aria-expanded", next ? "true" : "false");
    topbarRuntimeMenu.hidden = !next;
    if (next) {
      const btnRect = topbarRuntimeToggle.getBoundingClientRect();
      const menuW = Math.min(420, window.innerWidth - 32);
      const maxH = Math.min(window.innerHeight * 0.7, 560);
      let left = btnRect.left;
      if (left + menuW > window.innerWidth - 8) left = window.innerWidth - menuW - 8;
      if (left < 8) left = 8;
      let top = btnRect.bottom + 8;
      if (top + maxH > window.innerHeight - 8) top = Math.max(8, btnRect.top - maxH - 8);
      topbarRuntimeMenu.style.left = `${left}px`;
      topbarRuntimeMenu.style.top = `${top}px`;
    }
  }

  function cacheBustUrl(raw) {
    const url = new URL(String(raw || window.location.href), window.location.origin);
    url.searchParams.set("_ts", String(Date.now()));
    return url.toString();
  }

  async function runRuntimeRefresh() {
    if (!rebuildReloadBtn) {
      window.location.replace(cacheBustUrl(window.location.href));
      return;
    }
    const original = rebuildReloadBtn.textContent;
    rebuildReloadBtn.disabled = true;
    rebuildReloadBtn.textContent = "Rebuilding...";
    if (runtimeMenuStatus) runtimeMenuStatus.textContent = "Running runtime self-check...";
    try {
      const response = await fetch(apiUrl("/admin/self_check?force=1&fix=1"), {
        method: "POST",
        headers: typeof authHeaders === "function" ? authHeaders(true) : { "Content-Type": "application/json" },
        body: JSON.stringify({ fix: true, force_weather: true }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok && !payload.fixed) {
        throw new Error(payload.error || `status ${response.status}`);
      }
      if (runtimeMenuStatus) runtimeMenuStatus.textContent = "Runtime refreshed. Reloading UI...";
      window.location.replace(cacheBustUrl(window.location.href));
    } catch (err) {
      if (runtimeMenuStatus) runtimeMenuStatus.textContent = `Refresh failed: ${err}`;
    } finally {
      rebuildReloadBtn.disabled = false;
      rebuildReloadBtn.textContent = original;
    }
  }

  function currentMonth() {
    const now = new Date();
    const month = String(now.getMonth() + 1).padStart(2, "0");
    return `${now.getFullYear()}-${month}`;
  }

  async function apiGet(url) {
    const res = await fetch(apiUrl(url), { headers: typeof authHeaders === "function" ? authHeaders(false) : {} });
    const data = await res.json();
    if (!res.ok || data.ok === false) {
      throw new Error(data.error || res.statusText || "request_failed");
    }
    return data;
  }

  async function apiPostJson(url, payload) {
    const headers = { "Content-Type": "application/json" };
    if (typeof authHeaders === "function") {
      Object.assign(headers, authHeaders(false));
    }
    const res = await fetch(apiUrl(url), {
      method: "POST",
      headers,
      body: JSON.stringify(payload || {}),
    });
    const data = await res.json();
    if (!res.ok || data.ok === false) {
      throw new Error(data.error || res.statusText || "request_failed");
    }
    return data;
  }

  function renderLoanReview(payload) {
    const session = payload && payload.session ? payload.session : payload;
    if (!session || !loanReviewStatus) return;
    state.loanReview.session = session;
    const paused = Boolean(session.capture_paused);
    if (loanReviewMode) {
      loanReviewMode.textContent = paused ? "paused" : "ready";
      loanReviewMode.classList.toggle("off", paused);
    }
    const last = session.last_capture || {};
    if (loanReviewStatus) {
      loanReviewStatus.textContent = paused
        ? `Capture paused${session.pause_reason ? `: ${session.pause_reason}` : ""}.`
        : "Capture enabled. Ask Apollo to look at the current page when you want a one-shot screen grab.";
    }
    if (loanReviewSummary) {
      const takeaways = Array.isArray(last.takeaways) ? last.takeaways : [];
      loanReviewSummary.textContent = takeaways.length
        ? takeaways.join(" ")
        : "Apollo will summarize what it can read, what page it appears to be, and what matters next.";
    }
    if (loanReviewMeta) {
      const parts = [
        `Target: ${session.target || "aegis_tab"}`,
        last.captured_at ? `Last capture: ${last.captured_at}` : "Last capture: none",
      ];
      loanReviewMeta.textContent = parts.join(" • ");
    }
    if (loanReviewSample) {
      const lines = Array.isArray(last.sample_lines) && last.sample_lines.length
        ? last.sample_lines
        : ["(no capture yet)"];
      loanReviewSample.textContent = lines.join("\n");
    }
    if (loanReviewImage) {
      const artifactUrl = String(last.artifact_url || "");
      if (artifactUrl) {
        loanReviewImage.hidden = false;
        loanReviewImage.src = apiUrl(artifactUrl);
      } else {
        loanReviewImage.hidden = true;
        loanReviewImage.removeAttribute("src");
      }
    }
  }

  async function loadLoanReviewStatus() {
    try {
      const data = await apiGet("/admin/student_loan_review/status");
      renderLoanReview(data.session || {});
    } catch (err) {
      if (loanReviewStatus) loanReviewStatus.textContent = `Loan review unavailable: ${err}`;
    }
  }

  async function setLoanReviewMode(payload, statusText) {
    if (loanReviewStatus && statusText) loanReviewStatus.textContent = statusText;
    const data = await apiPostJson("/admin/student_loan_review/mode", payload || {});
    renderLoanReview(data.session || {});
    return data;
  }

  async function runLoanReviewCapture(compareLast) {
    if (loanReviewStatus) loanReviewStatus.textContent = compareLast ? "Capturing and comparing..." : "Capturing VM screen...";
    const prompt = (userInput && userInput.value ? userInput.value : "").trim();
    const data = await apiPostJson("/admin/student_loan_review/capture", {
      message: prompt,
      compare_last: Boolean(compareLast),
    });
    renderLoanReview(data.session || {});
    if (data.reply) {
      appendChat("Apollo", data.reply);
      if (reasoningEl) reasoningEl.textContent = "(apollo-student-loan-review)";
    }
    return data;
  }

  function buildGuidance() {
    const summary = state.summary || {};
    const overspent = [];
    state.groups.forEach((group) => {
      (group.categories || []).forEach((cat) => {
        if ((cat.available || 0) < 0) {
          overspent.push(cat);
        }
      });
    });
    if (overspent.length) {
      overspent.sort((a, b) => (a.available || 0) - (b.available || 0));
      const top = overspent[0];
      return `Cover ${top.name} overspent by ${formatCurrency(Math.abs(top.available || 0))}.`;
    }
    if ((summary.net || 0) < 0) {
      return "Net cashflow is negative. Consider adjusting spending or adding income.";
    }
    if ((summary.available_total || 0) === 0) {
      return "Ready to assign is zero. Add income or reduce assignments.";
    }
    return "Budget is balanced. Review upcoming bills and keep funding priorities.";
  }

  function updateOverview() {
    const summary = state.summary || {};
    if (overviewReady) overviewReady.textContent = formatCurrency(summary.available_total || 0);
    if (overviewOverspent) overviewOverspent.textContent = String(summary.overspent_count || 0);
    if (overviewNet) overviewNet.textContent = formatCurrency(summary.net || 0);
    if (overviewSafe) overviewSafe.textContent = formatCurrency((summary.available_total || 0) + Math.max(summary.net || 0, 0));
    if (overviewInsights) {
      overviewInsights.textContent = state.insights.summary || buildGuidance();
    }
  }

  function renderSummary() {
    const summary = state.summary || {};
    if (budgetReady) budgetReady.textContent = formatCurrency(summary.available_total || 0);
    if (budgetOverspent) budgetOverspent.textContent = String(summary.overspent_count || 0);
    if (summaryAssigned) summaryAssigned.textContent = formatCurrency(summary.assigned_total || 0);
    if (summaryActivity) summaryActivity.textContent = formatCurrency(summary.activity_total || 0);
    if (summaryAvailable) summaryAvailable.textContent = formatCurrency(summary.available_total || 0);
    if (summaryIncome) summaryIncome.textContent = formatCurrency(summary.income_total || 0);
    if (summaryExpense) summaryExpense.textContent = formatCurrency(summary.expense_total || 0);
  }

  function renderCategories() {
    if (!categoryList) return;
    categoryList.textContent = "";
    if (!state.groups.length) {
      categoryList.textContent = "No categories yet.";
      return;
    }
    state.groups.forEach((group) => {
      const groupEl = document.createElement("div");
      groupEl.className = "category-group";
      groupEl.innerHTML = `<div class="category-group-title">${escapeHtml(group.name || "Group")}</div>`;

      (group.categories || []).forEach((cat) => {
        const row = document.createElement("div");
        row.className = "category-row";
        row.dataset.categoryId = cat.id;
        row.dataset.groupName = group.name || "";

        const assigned = Number(cat.assigned || 0);
        const activity = Number(cat.activity || 0);
        const available = Number(cat.available || 0);
        const progress = assigned !== 0 ? Math.min(1, Math.abs(activity) / Math.abs(assigned)) : 0;

        row.innerHTML = `
          <div class="category-name">
            <input class="category-name-input" value="${escapeHtml(cat.name || "Category")}" />
            <input class="category-date-input" type="date" value="${escapeHtml(cat.due_date || "")}" />
          </div>
          <input class="category-assigned" value="${formatCurrency(assigned)}" />
          <div class="category-activity ${activity < 0 ? "negative" : "positive"}">${formatCurrency(activity)}</div>
          <div class="category-available ${available < 0 ? "negative" : "positive"}">${formatCurrency(available)}</div>
          <div class="category-progress"><span style="width:${Math.round(progress * 100)}%"></span></div>
        `;

        const assignedInput = row.querySelector(".category-assigned");
        const nameInput = row.querySelector(".category-name-input");
        const dateInput = row.querySelector(".category-date-input");

        if (assignedInput) {
          assignedInput.addEventListener("blur", () => updateCategoryFromRow(row));
        }
        if (nameInput) {
          nameInput.addEventListener("blur", () => updateCategoryFromRow(row));
        }
        if (dateInput) {
          dateInput.addEventListener("change", () => updateCategoryFromRow(row));
        }

        groupEl.appendChild(row);
      });

      categoryList.appendChild(groupEl);
    });
  }

  async function updateCategoryFromRow(row) {
    const id = row.dataset.categoryId;
    const group = row.dataset.groupName || "General";
    const nameInput = row.querySelector(".category-name-input");
    const assignedInput = row.querySelector(".category-assigned");
    const dateInput = row.querySelector(".category-date-input");
    const payload = {
      id,
      group,
      name: nameInput ? nameInput.value.trim() : "Category",
      assigned: parseNumber(assignedInput ? assignedInput.value : 0),
      due_date: dateInput ? dateInput.value : "",
      month: state.budgetMonth,
    };
    try {
      await apiPostJson("/budget/category", payload);
      await loadBudgetData();
      setBudgetStatus("Category updated.");
    } catch (err) {
      setBudgetStatus(`Category update failed: ${err}`, true);
    }
  }

  function renderTransactions() {
    if (!transactionList) return;
    transactionList.textContent = "";
    if (!state.transactions.length) {
      const empty = document.createElement("div");
      empty.className = "transaction-row";
      empty.innerHTML = "<span>-</span><span>No transactions yet</span><span>-</span><span>-</span>";
      transactionList.appendChild(empty);
      return;
    }
    state.transactions.forEach((tx) => {
      const row = document.createElement("div");
      row.className = "transaction-row";
      row.innerHTML = `
        <span>${escapeHtml(tx.date || "-")}</span>
        <span>${escapeHtml(tx.payee || "-")}</span>
        <span>${escapeHtml(tx.category_name || "-")}</span>
        <span class="${(tx.amount || 0) < 0 ? "negative" : "positive"}">${formatCurrency(tx.amount || 0)}</span>
      `;
      transactionList.appendChild(row);
    });
  }

  function renderAccounts() {
    if (!accountsList) return;
    accountsList.textContent = "";
    if (!state.accounts.length) {
      accountsList.textContent = "No accounts yet.";
      return;
    }
    state.accounts.forEach((acct) => {
      const row = document.createElement("div");
      row.className = "list-row";
      row.innerHTML = `
        <span>${escapeHtml(acct.name || "Account")}</span>
        <span class="${(acct.balance || 0) < 0 ? "negative" : "positive"}">${formatCurrency(acct.balance || 0)}</span>
      `;
      accountsList.appendChild(row);
    });
  }

  function renderPaychecks() {
    if (!paychecksList) return;
    paychecksList.textContent = "";
    if (!state.paychecks.length) {
      paychecksList.textContent = "No paychecks yet.";
      return;
    }
    state.paychecks.forEach((pc) => {
      const row = document.createElement("div");
      row.className = "list-row";
      row.innerHTML = `
        <span>${escapeHtml(pc.date || "-")}</span>
        <span>${formatCurrency(pc.amount || 0)}</span>
      `;
      paychecksList.appendChild(row);
    });
  }

  function renderGoals() {
    const lists = [goalList, goalsList];
    lists.forEach((list) => {
      if (!list) return;
      list.textContent = "";
    });
    if (!state.goals.length) {
      lists.forEach((list) => {
        if (list) list.textContent = "No goals yet.";
      });
      return;
    }
    state.goals.forEach((goal) => {
      const row = document.createElement("div");
      row.className = "list-row";
      row.innerHTML = `
        <span>${escapeHtml(goal.name || "Goal")}</span>
        <span>${formatCurrency(goal.saved_amount || 0)} / ${formatCurrency(goal.target_amount || 0)}</span>
      `;
      lists.forEach((list) => {
        if (list) list.appendChild(row.cloneNode(true));
      });
    });
  }

  function renderDueDates() {
    if (!dueList) return;
    dueList.textContent = "";
    const items = [];
    state.groups.forEach((group) => {
      (group.categories || []).forEach((cat) => {
        if (cat.due_date) {
          items.push(cat);
        }
      });
    });
    if (!items.length) {
      dueList.textContent = "No due dates set.";
      return;
    }
    items.sort((a, b) => String(a.due_date || "").localeCompare(String(b.due_date || "")));
    items.slice(0, 6).forEach((cat) => {
      const row = document.createElement("div");
      row.className = "list-row";
      row.innerHTML = `<span>${escapeHtml(cat.name || "Bill")}</span><span>${escapeHtml(cat.due_date || "")}</span>`;
      dueList.appendChild(row);
    });
  }

  function setSwingStatus(text, isError) {
    if (!swingStatus) return;
    swingStatus.textContent = text || "";
    swingStatus.classList.toggle("negative", Boolean(isError));
  }

  function swingRows() {
    const latest = state.swing.latest || {};
    const rows = Array.isArray(latest.proposals) ? latest.proposals : [];
    const filter = swingStatusFilter ? String(swingStatusFilter.value || "").trim().toLowerCase() : "";
    return filter ? rows.filter((row) => String(row.status || "").toLowerCase() === filter) : rows;
  }

  function swingSetupLabel(value) {
    return String(value || "-").replace(/_/g, " ");
  }

  function swingScoreValue(row) {
    const score = Number((row || {}).score);
    return Number.isFinite(score) ? Math.max(0, Math.min(100, Math.round(score))) : null;
  }

  function swingRiskReward(row) {
    const rr = Number((row || {}).risk_reward_estimate);
    return Number.isFinite(rr) ? rr : null;
  }

  function swingTradeReady(row) {
    const setup = String((row || {}).setup_type || "").toLowerCase();
    const status = String((row || {}).status || "").toLowerCase();
    const rr = swingRiskReward(row);
    if (["no_trade", "extended_no_chase", "broken_trend"].includes(setup)) return false;
    if (["rejected", "invalidated"].includes(status)) return false;
    if (rr != null && rr < 1) return false;
    return true;
  }

  function swingStatusLabel(row) {
    if (!row) return "-";
    if (swingTradeReady(row)) return row.status ? String(row.status).replace(/_/g, " ") : "review";
    const setup = String(row.setup_type || "").toLowerCase();
    if (setup === "no_trade") return "no trade";
    if (setup === "extended_no_chase") return "no chase";
    if (setup === "broken_trend") return "broken trend";
    return row.status ? String(row.status).replace(/_/g, " ") : "blocked";
  }

  function swingRrClass(rr) {
    if (rr == null) return "";
    if (rr >= 1.25) return "good";
    if (rr >= 1) return "warn";
    return "bad";
  }

  function swingDecision(row) {
    if (!row) return "Select a setup to review next action.";
    const setup = String(row.setup_type || "").toLowerCase();
    const rr = swingRiskReward(row);
    if (setup === "extended_no_chase") return "Blocked: extended move, wait for a better entry.";
    if (setup === "broken_trend") return "Blocked: trend damage needs repair first.";
    if (setup === "no_trade") return "Blocked: Apollo marked this as no trade.";
    if (rr != null && rr < 1) return "Blocked: reward does not justify risk.";
    if (rr != null && rr < 1.25) return "Watch only: acceptable but thin risk/reward.";
    return "Paper candidate: review levels before simulated execution.";
  }

  function swingPillClass(row) {
    if (!swingTradeReady(row)) return "blocked";
    const rr = swingRiskReward(row);
    if (rr != null && rr < 1.25) return "watch";
    return "ready";
  }

  function renderSwingSelectedStrip(row) {
    if (!swingSelectedStrip) return;
    if (!row) {
      swingSelectedStrip.textContent = "Select a setup to review tradeability, levels, and next action.";
      return;
    }
    const score = swingScoreValue(row);
    const rr = swingRiskReward(row);
    swingSelectedStrip.innerHTML = `
      <span><strong>${escapeHtml(row.ticker || "-")}</strong>${escapeHtml(swingSetupLabel(row.setup_type))}</span>
      <span><strong>${escapeHtml(swingDecision(row))}</strong>${escapeHtml(row.invalidation_trigger || "No invalidation note recorded.")}</span>
      <span><strong>Score ${escapeHtml(score != null ? String(score) : "-")}</strong><span class="swing-rr ${swingRrClass(rr)}">R/R ${escapeHtml(rr != null ? rr.toFixed(2) : "-")}</span></span>
      <span><strong>Levels</strong>Entry ${escapeHtml(row.entry_zone || "-")} | Stop ${escapeHtml(row.stop_zone || "-")} | Target ${escapeHtml(row.target_zone || "-")}</span>
    `;
  }

  function renderSwingSimulationAccount() {
    const payload = state.swing.account || {};
    const account = payload.account || {};
    const plan = payload.next_cycle_plan || {};
    const report = payload.report || {};
    const realized = Number(account.realized_return || 0);
    const unrealized = Number(account.unrealized_return || 0);
    const totalReturn = realized + unrealized;
    if (swingSimCash) swingSimCash.textContent = formatCurrency(account.cash_balance || 0);
    if (swingSimBalance) swingSimBalance.textContent = formatCurrency(account.market_value != null ? account.market_value : account.cash_balance || 100);
    if (swingSimInvested) swingSimInvested.textContent = formatCurrency(account.invested_amount || 0);
    if (swingSimReturn) {
      swingSimReturn.textContent = formatCurrency(totalReturn);
      swingSimReturn.classList.toggle("positive", totalReturn > 0);
      swingSimReturn.classList.toggle("negative", totalReturn < 0);
    }
    if (swingSimPlan) swingSimPlan.textContent = formatCurrency(plan.planned_investment || 0);
    if (swingSimNote) {
      const status = String(account.status || "not_started").replace(/_/g, " ");
      const cycle = plan.cycle_date ? ` Next cycle: ${plan.cycle_date}.` : "";
      swingSimNote.textContent = `Current run: ${status}. ${plan.note || "Paper simulation only."}${cycle}`;
    }
    if (swingSimSpread) {
      const allocations = Array.isArray(plan.allocations) ? plan.allocations : [];
      if (!allocations.length) {
        swingSimSpread.textContent = "No paper capital is allocated yet; Apollo keeps the $100 start balance in cash until a setup clears the rules.";
      } else {
        swingSimSpread.innerHTML = allocations.map((item) => `
          <div class="swing-sim-allocation">
            <strong>${escapeHtml(item.ticker || "-")} ${escapeHtml(formatCurrency(item.amount || 0))}</strong>
            <span>${escapeHtml(item.weight_pct != null ? `${item.weight_pct}%` : "")} near ${escapeHtml(item.entry_zone || "-")} | stop ${escapeHtml(item.stop_zone || "-")} | target ${escapeHtml(item.target_zone || "-")}</span>
            <small>${escapeHtml(item.defer_reason ? `Waiting: ${item.defer_reason}` : item.reason || "Real daily candle must touch entry zone before paper fill.")}</small>
          </div>
        `).join("");
      }
    }
    if (swingSimReport) swingSimReport.hidden = !state.swing.reportOpen;
    if (swingSimReportToggle) swingSimReportToggle.textContent = state.swing.reportOpen ? "Hide Report" : "Report";
    if (swingSimReportText) swingSimReportText.textContent = report.markdown || "No paper simulation report has been generated yet.";
    if (swingSimReportPath) swingSimReportPath.textContent = report.path ? "saved report" : "paper only";
  }

  async function loadSwingSimulationAccount() {
    if (!swingSimBalance && !swingSimSpread) return;
    try {
      const data = await apiGet("/api/simulation/account");
      state.swing.account = data;
      renderSwingSimulationAccount();
    } catch (err) {
      if (swingSimNote) swingSimNote.textContent = `Paper account load failed: ${err}`;
    }
  }

  function renderPretradeGate() {
    const status = state.swing.pretrade || {};
    if (swingPretradeDecision) {
      const decision = String(status.decision || "No check yet").replace(/_/g, " ");
      const ticker = ((status.candidate || {}).ticker || "").toString();
      swingPretradeDecision.textContent = ticker ? `${ticker}: ${decision}` : decision;
    }
    if (swingPretradeNote) {
      const checked = status.checked_at_ct ? ` Checked ${status.checked_at_ct}.` : "";
      const blocker = status.blocker ? ` Blocker: ${status.blocker}.` : "";
      swingPretradeNote.textContent = `${status.next_action || "Apollo will run a light quote/news gate before paper-sim action."}${blocker}${checked}`;
    }
  }

  async function loadPretradeStatus() {
    try {
      const data = await apiGet("/admin/apollo/pretrade/status");
      state.swing.pretrade = data.status || {};
      renderPretradeGate();
    } catch (_) {
      renderPretradeGate();
    }
  }

  async function runPretradeCheck() {
    const row = state.swing.selectedProposal || {};
    setSwingStatus("Running pre-trade gate...");
    try {
      const payload = { trigger: "manual", force: true };
      if (row && row.ticker) payload.ticker = row.ticker;
      const data = await apiPostJson("/admin/apollo/pretrade/check", payload);
      state.swing.pretrade = data;
      renderPretradeGate();
      await loadDailyReport();
      setSwingStatus(`Pre-trade gate: ${String(data.decision || "complete").replace(/_/g, " ")}`);
    } catch (err) {
      setSwingStatus(`Pre-trade gate failed: ${err}`, true);
    }
  }

  function renderDailyReport() {
    const payload = state.swing.dailyReport || {};
    if (swingDailyReport) swingDailyReport.hidden = !state.swing.dailyReportOpen;
    if (swingDailyReportText) swingDailyReportText.textContent = payload.markdown || "No Apollo daily report has been created yet.";
    if (swingDailyReportPath) {
      const paths = payload.paths || {};
      swingDailyReportPath.textContent = paths.markdown ? "saved report" : "daily report";
    }
    if (swingDailyReportOpen) swingDailyReportOpen.textContent = state.swing.dailyReportOpen ? "Hide Daily Report" : "Daily Report";
  }

  async function loadDailyReport() {
    try {
      const data = await apiGet("/admin/apollo/daily_report");
      state.swing.dailyReport = data;
      renderDailyReport();
    } catch (_) {
      renderDailyReport();
    }
  }

  function renderSwing() {
    if (!swingList) return;
    const latest = state.swing.latest || {};
    const regime = latest.market_regime || {};
    const allRows = Array.isArray(latest.proposals) ? latest.proposals : [];
    const best = allRows.slice().sort((a, b) => (swingScoreValue(b) || 0) - (swingScoreValue(a) || 0))[0];
    const tradeReadyCount = allRows.filter(swingTradeReady).length;
    const blockedCount = allRows.length ? allRows.length - tradeReadyCount : 0;
    if (swingRegime) swingRegime.textContent = `${regime.label || "unknown"} ${regime.score != null ? Math.round(Number(regime.score)) : ""}`.trim();
    if (swingBest) swingBest.textContent = best ? `${best.ticker || "-"} ${swingScoreValue(best) != null ? swingScoreValue(best) : "-"}` : "-";
    if (swingActionable) swingActionable.textContent = String(tradeReadyCount);
    if (swingBlocked) swingBlocked.textContent = String(blockedCount);
    if (swingTickers && !swingTickers.value && Array.isArray(latest.tickers)) {
      swingTickers.placeholder = latest.tickers.slice(0, 8).join(", ");
    }
    const rows = swingRows();
    swingList.textContent = "";
    if (!rows.length) {
      swingList.innerHTML = '<div class="swing-empty">No swing setups loaded.</div>';
      renderSwingDetail(null);
      return;
    }
    rows.forEach((row) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "swing-row swing-row-btn";
      if (String(row.ticker || "") === state.swing.selectedTicker) item.classList.add("is-selected");
      const score = swingScoreValue(row);
      const rr = swingRiskReward(row);
      item.innerHTML = `
        <span class="swing-row-ticker"><strong>${escapeHtml(row.ticker || "-")}</strong><small>${escapeHtml(row.confidence_label || "")}</small></span>
        <span>${escapeHtml(swingSetupLabel(row.setup_type))}<small class="swing-row-decision">${escapeHtml(swingDecision(row))}</small></span>
        <span class="swing-score"><strong>${escapeHtml(score != null ? String(score) : "-")}</strong><span class="swing-score-line" style="--score-width:${score != null ? score : 0}%"><i></i></span></span>
        <span>${escapeHtml(row.relative_strength_rank != null ? String(row.relative_strength_rank) : "-")}</span>
        <span><span class="swing-rr ${swingRrClass(rr)}">R/R ${escapeHtml(rr != null ? rr.toFixed(2) : "-")}</span></span>
        <span><span class="swing-pill ${swingPillClass(row)}">${escapeHtml(swingStatusLabel(row))}</span></span>
      `;
      item.addEventListener("click", () => {
        state.swing.selectedTicker = String(row.ticker || "");
        state.swing.selectedProposal = row;
        renderSwing();
      });
      swingList.appendChild(item);
    });
    const selected = rows.find((row) => String(row.ticker || "") === state.swing.selectedTicker) || rows[0];
    state.swing.selectedTicker = String((selected && selected.ticker) || "");
    state.swing.selectedProposal = selected || null;
    renderSwingSelectedStrip(selected || null);
    renderSwingDetail(selected || null);
    renderSwingSimulationAccount();
    renderPretradeGate();
    renderDailyReport();
  }

  function swingChartPoint(row, idx, values, width, height, pad) {
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = Math.max(0.01, max - min);
    const x = pad + (idx / Math.max(1, values.length - 1)) * (width - pad * 2);
    const y = height - pad - ((Number(row.close || 0) - min) / span) * (height - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }

  function renderSwingChart(row) {
    if (!swingChart) return;
    const candles = (((row || {}).swing_snapshot || {}).chart || {}).candles || [];
    const data = candles.slice(-80).filter((item) => Number.isFinite(Number(item.close)));
    if (!row || data.length < 5) {
      swingChart.textContent = "No chart data available.";
      return;
    }
    const width = 640;
    const height = 220;
    const pad = 22;
    const values = [];
    data.forEach((item) => {
      ["high", "low", "close", "sma20", "sma50", "sma200", "ema8", "ema21"].forEach((key) => {
        const value = Number(item[key]);
        if (Number.isFinite(value) && value > 0) values.push(value);
      });
    });
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = Math.max(0.01, max - min);
    const yFor = (value) => height - pad - ((Number(value || 0) - min) / span) * (height - pad * 2);
    const xFor = (idx) => pad + (idx / Math.max(1, data.length - 1)) * (width - pad * 2);
    const lineFor = (key) => data.map((item, idx) => {
      const value = Number(item[key]);
      if (!Number.isFinite(value) || value <= 0) return "";
      return `${xFor(idx).toFixed(1)},${yFor(value).toFixed(1)}`;
    }).filter(Boolean).join(" ");
    const closeLine = lineFor("close");
    const sma20 = lineFor("sma20");
    const sma50 = lineFor("sma50");
    const ema8 = lineFor("ema8");
    const levels = row.chart_levels || {};
    const levelMarkup = ["stop", "target", "current"].map((key) => {
      const value = Number(levels[key]);
      if (!Number.isFinite(value) || value <= 0) return "";
      const y = yFor(value).toFixed(1);
      const color = key === "stop" ? "#ef4444" : key === "target" ? "#3cb179" : "#e5e7eb";
      return `<line x1="${pad}" y1="${y}" x2="${width - pad}" y2="${y}" stroke="${color}" stroke-width="1" stroke-dasharray="4 4"><title>${key} ${value.toFixed(2)}</title></line>`;
    }).join("");
    swingChart.innerHTML = `
      <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(row.ticker || "Ticker")} swing chart">
        <rect x="0" y="0" width="${width}" height="${height}" rx="10" />
        <polyline points="${sma50}" fill="none" stroke="#7c8aa5" stroke-width="1.4" opacity="0.75" />
        <polyline points="${sma20}" fill="none" stroke="#f59e0b" stroke-width="1.5" opacity="0.82" />
        <polyline points="${ema8}" fill="none" stroke="#60a5fa" stroke-width="1.2" opacity="0.75" />
        <polyline points="${closeLine}" fill="none" stroke="#f8fafc" stroke-width="2.2" />
        ${levelMarkup}
      </svg>
      <div class="swing-chart-legend">
        <span>Close</span><span>SMA20</span><span>SMA50</span><span>EMA8</span><span>Stop/Target</span>
      </div>
    `;
  }

  function renderSwingDetail(row) {
    if (!row) {
      renderSwingSelectedStrip(null);
      if (swingDetailTitle) swingDetailTitle.textContent = "Details";
      if (swingChart) swingChart.textContent = "Select a setup to load chart context.";
      if (swingDetail) swingDetail.textContent = "Select a setup to review levels, catalysts, and lifecycle state.";
      if (swingJson) swingJson.textContent = "(no proposal selected)";
      return;
    }
    if (swingDetailTitle) swingDetailTitle.textContent = `${row.ticker || "-"} ${swingSetupLabel(row.setup_type)}`.trim();
    renderSwingChart(row);
    const eventRisk = row.earnings_or_event_risk || {};
    const catalysts = Array.isArray(row.catalyst_summary) ? row.catalyst_summary : [];
    const riskNotes = Array.isArray(row.risk_notes) ? row.risk_notes : [];
    const notes = Array.isArray(row.learning_notes) ? row.learning_notes : [];
    const badges = [
      row.confidence_label ? `Confidence ${row.confidence_label}` : "",
      row.provider_confidence != null ? `Provider ${row.provider_confidence}` : "",
      row.catalyst_quality_score != null ? `Catalyst ${row.catalyst_quality_score}` : "",
      row.outcome_status && row.outcome_status.status ? `Outcome ${row.outcome_status.status}` : "",
    ].filter(Boolean);
    if (swingDetail) {
      swingDetail.innerHTML = `
        <div class="swing-badges">${badges.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>
        <div class="swing-detail-grid">
          <div><span>Entry</span><strong>${escapeHtml(row.entry_zone || "-")}</strong></div>
          <div><span>Stop</span><strong>${escapeHtml(row.stop_zone || "-")}</strong></div>
          <div><span>Target</span><strong>${escapeHtml(row.target_zone || "-")}</strong></div>
          <div><span>R/R</span><strong>${escapeHtml(row.risk_reward_estimate != null ? String(row.risk_reward_estimate) : "-")}</strong></div>
          <div><span>ATR</span><strong>${escapeHtml(row.atr_pct != null ? `${row.atr_pct}%` : "-")}</strong></div>
          <div><span>Review</span><strong>${escapeHtml(row.review_by_date || "-")}</strong></div>
        </div>
        <div class="swing-detail-block"><strong>Invalidation</strong><br>${escapeHtml(row.invalidation_trigger || "-")}</div>
        <div class="swing-detail-block"><strong>Event risk</strong><br>${escapeHtml(eventRisk.label || "normal")} ${escapeHtml((eventRisk.flags || []).join(", "))}</div>
        <div class="swing-detail-block"><strong>Catalysts</strong><br>${catalysts.length ? catalysts.map(escapeHtml).join("<br>") : "None recorded."}</div>
        <div class="swing-detail-block"><strong>Risk notes</strong><br>${riskNotes.length ? riskNotes.map(escapeHtml).join("<br>") : "None recorded."}</div>
        <div class="swing-detail-block"><strong>Learning notes</strong><br>${notes.length ? notes.map(escapeHtml).join("<br>") : "No learning notes yet."}</div>
      `;
    }
    if (swingJson) swingJson.textContent = JSON.stringify(row, null, 2);
  }

  async function loadSwingLatest() {
    if (!swingList) return;
    setSwingStatus("Loading swing study...");
    try {
      const data = await apiGet("/admin/swing/latest");
      state.swing.latest = data;
      setSwingStatus(data.created_at ? `Loaded ${data.run_id || "latest"} at ${new Date(data.created_at).toLocaleString()}` : "No completed swing study yet.");
      renderSwing();
      loadSwingSimulationAccount();
      loadPretradeStatus();
      loadDailyReport();
    } catch (err) {
      setSwingStatus(`Swing load failed: ${err}`, true);
    }
  }

  async function runSwingStudy() {
    setSwingStatus("Running swing study...");
    try {
      const payload = {};
      if (swingTickers && swingTickers.value.trim()) payload.tickers = swingTickers.value.trim();
      const data = await apiPostJson("/admin/swing/run", payload);
      state.swing.latest = data;
      setSwingStatus(`Swing study complete: ${data.run_id || "run"}`);
      renderSwing();
      loadSwingSimulationAccount();
      loadPretradeStatus();
      loadDailyReport();
    } catch (err) {
      setSwingStatus(`Swing run failed: ${err}`, true);
    }
  }

  async function decideSwing(action) {
    const row = state.swing.selectedProposal;
    if (!row || !row.ticker) {
      setSwingStatus("Select a setup first.", true);
      return;
    }
    try {
      const result = await apiPostJson("/admin/swing/proposal_decision", {
        ticker: row.ticker,
        action,
        confidence: row.score || row.confidence || 0,
        thesis: `${row.setup_type || "swing"} score ${row.score || 0}`,
      });
      setSwingStatus(`Updated ${row.ticker}: ${result.action}`);
      await loadSwingLatest();
    } catch (err) {
      setSwingStatus(`Decision failed: ${err}`, true);
    }
  }

  function renderInsights() {
    if (budgetGuidance) budgetGuidance.textContent = buildGuidance();
    if (budgetContext) {
      budgetContext.textContent = state.insights.summary ? `Latest guidance: ${state.insights.summary}` : "";
    }
    if (insightsBody) {
      insightsBody.textContent = state.insights.summary || "No insights yet.";
    }
    if (insightsSources) {
      insightsSources.textContent = "";
      (state.insights.sources || []).forEach((src) => {
        const item = document.createElement("div");
        item.textContent = `${src.title || "Source"} (${Math.round((src.trust || 0) * 100)}%)`;
        insightsSources.appendChild(item);
      });
    }
  }

  function renderReports() {
    if (!reportsList || !reportsSummary) return;
    reportsList.textContent = "";
    const groups = state.groups || [];
    if (!groups.length) {
      reportsSummary.textContent = "No report data yet.";
      return;
    }
    const totals = groups.map((group) => {
      const cats = group.categories || [];
      return {
        name: group.name,
        assigned: cats.reduce((sum, c) => sum + (c.assigned || 0), 0),
        activity: cats.reduce((sum, c) => sum + (c.activity || 0), 0),
        available: cats.reduce((sum, c) => sum + (c.available || 0), 0),
      };
    });
    reportsSummary.textContent = `Tracking ${totals.length} category groups.`;
    totals.forEach((group) => {
      const row = document.createElement("div");
      row.className = "list-row";
      row.innerHTML = `
        <span>${escapeHtml(group.name || "Group")}</span>
        <span>${formatCurrency(group.available)}</span>
      `;
      reportsList.appendChild(row);
    });
  }

  function renderCashflow() {
    if (!cashflowSummary || !cashflowTimeline) return;
    cashflowTimeline.textContent = "";
    const summary = state.summary || {};
    cashflowSummary.textContent = `Net ${formatCurrency(summary.net || 0)} with ${state.paychecks.length} paychecks tracked.`;
    const events = [];
    state.paychecks.forEach((pc) => events.push({ label: `Paycheck ${pc.source || ""}`.trim(), date: pc.date, amount: pc.amount }));
    state.groups.forEach((group) => {
      (group.categories || []).forEach((cat) => {
        if (cat.due_date) {
          events.push({ label: `Bill ${cat.name}`, date: cat.due_date, amount: -(cat.assigned || 0) });
        }
      });
    });
    events.sort((a, b) => String(a.date || "").localeCompare(String(b.date || "")));
    if (!events.length) {
      cashflowTimeline.textContent = "No cashflow events scheduled.";
      return;
    }
    events.slice(0, 8).forEach((evt) => {
      const row = document.createElement("div");
      row.className = "list-row";
      row.innerHTML = `<span>${escapeHtml(evt.date || "-")} ${escapeHtml(evt.label || "")}</span><span class="${(evt.amount || 0) < 0 ? "negative" : "positive"}">${formatCurrency(evt.amount || 0)}</span>`;
      cashflowTimeline.appendChild(row);
    });
  }

  function updateScenario() {
    if (!scenarioSlider || !scenarioAmount || !scenarioImpact) return;
    const pct = Number(scenarioSlider.value || 0);
    scenarioAmount.textContent = `${pct}%`;
    const expense = Math.abs(state.summary.expense_total || 0);
    const impact = -(expense * (pct / 100));
    scenarioImpact.textContent = `${formatCurrency(impact)} impact`;
    if (overviewSafe) {
      const safe = (state.summary.available_total || 0) + (state.summary.net || 0) + impact;
      overviewSafe.textContent = formatCurrency(safe);
    }
  }

  async function loadBudgetData() {
    const month = state.budgetMonth;
    try {
      const [summary, categories, transactions, accounts, paychecks, goals, insights] = await Promise.all([
        apiGet(`/budget/summary?month=${encodeURIComponent(month)}`),
        apiGet(`/budget/categories?month=${encodeURIComponent(month)}`),
        apiGet(`/budget/transactions?month=${encodeURIComponent(month)}`),
        apiGet("/budget/accounts"),
        apiGet(`/budget/paychecks?month=${encodeURIComponent(month)}`),
        apiGet("/budget/goals"),
        apiGet(`/budget/insights?month=${encodeURIComponent(month)}`),
      ]);

      state.summary = summary || {};
      state.groups = categories.groups || [];
      state.transactions = transactions.transactions || [];
      state.accounts = accounts.accounts || [];
      state.paychecks = paychecks.paychecks || [];
      state.goals = goals.goals || [];
      state.insights = insights || { summary: "", sources: [] };

      if (budgetSubtitle) budgetSubtitle.textContent = `Month ${month}`;

      renderSummary();
      renderCategories();
      renderTransactions();
      renderAccounts();
      renderPaychecks();
      renderGoals();
      renderDueDates();
      renderInsights();
      renderReports();
      renderCashflow();
      updateOverview();
      updateScenario();

      setBudgetStatus("Budget loaded.");
    } catch (err) {
      setBudgetStatus(`Budget load failed: ${err}`, true);
    }
  }

  async function refreshInsights() {
    try {
      await apiPostJson(`/budget/insights/refresh?month=${encodeURIComponent(state.budgetMonth)}`, {});
      await loadBudgetData();
      setBudgetStatus("Insights refreshed.");
    } catch (err) {
      setBudgetStatus(`Insights refresh failed: ${err}`, true);
    }
  }

  async function addCategory() {
    const group = window.prompt("Category group", "General");
    if (group === null) return;
    const name = window.prompt("Category name", "New Category");
    if (name === null) return;
    const assigned = window.prompt("Assigned amount", "0");
    if (assigned === null) return;
    const dueDate = window.prompt("Due date (YYYY-MM-DD)", "");
    if (dueDate === null) return;
    try {
      await apiPostJson("/budget/category", {
        group,
        name,
        assigned: parseNumber(assigned),
        due_date: dueDate,
        month: state.budgetMonth,
      });
      await loadBudgetData();
      setBudgetStatus("Category added.");
    } catch (err) {
      setBudgetStatus(`Add category failed: ${err}`, true);
    }
  }

  async function addAccount() {
    const name = window.prompt("Account name", "Checking");
    if (name === null) return;
    const type = window.prompt("Account type", "Cash");
    if (type === null) return;
    const balance = window.prompt("Balance", "0");
    if (balance === null) return;
    const institution = window.prompt("Institution (optional)", "");
    try {
      await apiPostJson("/budget/account", {
        name,
        type,
        balance: parseNumber(balance),
        institution: institution || "",
      });
      await loadBudgetData();
      setBudgetStatus("Account added.");
    } catch (err) {
      setBudgetStatus(`Add account failed: ${err}`, true);
    }
  }

  async function addPaycheck() {
    const date = window.prompt("Paycheck date", new Date().toISOString().slice(0, 10));
    if (date === null) return;
    const amount = window.prompt("Amount", "0");
    if (amount === null) return;
    const source = window.prompt("Source", "Employer");
    try {
      await apiPostJson("/budget/paycheck", {
        date,
        amount: parseNumber(amount),
        source,
        month: state.budgetMonth,
      });
      await loadBudgetData();
      setBudgetStatus("Paycheck added.");
    } catch (err) {
      setBudgetStatus(`Add paycheck failed: ${err}`, true);
    }
  }

  async function addGoal() {
    const name = window.prompt("Goal name", "Emergency Fund");
    if (name === null) return;
    const target = window.prompt("Target amount", "0");
    if (target === null) return;
    const saved = window.prompt("Saved amount", "0");
    if (saved === null) return;
    const due = window.prompt("Due date (YYYY-MM-DD)", "");
    try {
      await apiPostJson("/budget/goal", {
        name,
        target_amount: parseNumber(target),
        saved_amount: parseNumber(saved),
        due_date: due,
      });
      await loadBudgetData();
      setBudgetStatus("Goal added.");
    } catch (err) {
      setBudgetStatus(`Add goal failed: ${err}`, true);
    }
  }

  async function addTransaction() {
    const date = window.prompt("Transaction date", new Date().toISOString().slice(0, 10));
    if (date === null) return;
    const payee = window.prompt("Payee", "");
    if (payee === null) return;
    const amount = window.prompt("Amount (positive for income, negative for expense)", "-");
    if (amount === null) return;
    const category = window.prompt("Category", "Uncategorized");
    if (category === null) return;
    const group = window.prompt("Group", "General");
    if (group === null) return;
    try {
      await apiPostJson("/budget/transaction", {
        date,
        payee,
        amount: parseNumber(amount),
        category,
        group,
        month: state.budgetMonth,
        source: "manual",
      });
      await loadBudgetData();
      setBudgetStatus("Transaction added.");
    } catch (err) {
      setBudgetStatus(`Add transaction failed: ${err}`, true);
    }
  }

  async function addSplitTransaction() {
    const date = window.prompt("Parent date", new Date().toISOString().slice(0, 10));
    if (date === null) return;
    const payee = window.prompt("Parent payee", "");
    if (payee === null) return;
    const amount = window.prompt("Parent amount (negative for expense)", "-");
    if (amount === null) return;
    const splitCountRaw = window.prompt("Number of splits", "2");
    if (splitCountRaw === null) return;
    const splitCount = Math.max(1, parseInt(splitCountRaw, 10) || 1);
    const splits = [];
    for (let i = 0; i < splitCount; i += 1) {
      const splitCategory = window.prompt(`Split ${i + 1} category`, "Uncategorized");
      if (splitCategory === null) return;
      const splitAmount = window.prompt(`Split ${i + 1} amount (negative for expense)`, "-");
      if (splitAmount === null) return;
      splits.push({
        category: splitCategory,
        amount: parseNumber(splitAmount),
        group: "General",
      });
    }
    try {
      await apiPostJson("/budget/transaction", {
        date,
        payee,
        amount: parseNumber(amount),
        category: "Split",
        group: "General",
        month: state.budgetMonth,
        source: "manual",
        splits,
      });
      await loadBudgetData();
      setBudgetStatus("Split transaction added.");
    } catch (err) {
      setBudgetStatus(`Split transaction failed: ${err}`, true);
    }
  }

  function extractReceiptTotal(text) {
    const raw = String(text || "");
    const totalRegex = /(grand\s*total|total\s*due|total|amount\s*due|balance)\D{0,20}(\d+[.,]\d{2})/gi;
    let match;
    let best = 0;
    while ((match = totalRegex.exec(raw)) !== null) {
      best = Math.max(best, parseNumber(match[2]));
    }
    if (best > 0) return best;
    const generic = raw.match(/(\d+[.,]\d{2})/g) || [];
    generic.forEach((token) => { best = Math.max(best, parseNumber(token)); });
    return best;
  }

  function parseReceiptTransaction(text) {
    const total = extractReceiptTotal(text);
    if (!total) return null;
    const payee = String(text || "").replace(/\s+/g, " ").trim().split(" ").slice(0, 3).join(" ") || "Receipt";
    return {
      date: new Date().toISOString().slice(0, 10),
      payee,
      amount: -Math.abs(total),
      category: "Uncategorized",
      group: "General",
      source: "receipt",
      month: state.budgetMonth,
    };
  }

  async function importReceiptFile(file) {
    if (!file) return;
    setBudgetStatus(`Importing receipt: ${file.name}`);
    const formData = new FormData();
    formData.append("file", file);
    formData.append("goal", "Extract receipt date, merchant, and total.");
    formData.append("engine", "auto");
    try {
      const fetchOptions = { method: "POST", body: formData };
      if (typeof authHeaders === "function") fetchOptions.headers = authHeaders(false);
      const res = await fetch(apiUrl("/ocr/run"), fetchOptions);
      const data = await res.json();
      if (!res.ok || data.ok === false) {
        setBudgetStatus(`Receipt OCR failed: ${data.error || res.status}`, true);
        return;
      }
      const tx = parseReceiptTransaction(data.text || data.output || "");
      if (!tx) {
        setBudgetStatus("Receipt parsed but no total found.", true);
        return;
      }
      await apiPostJson("/budget/transaction", tx);
      await loadBudgetData();
      setBudgetStatus("Receipt imported.");
    } catch (err) {
      setBudgetStatus(`Receipt import failed: ${err}`, true);
    }
  }

  function shouldTriggerReceiptImport(message) {
    const low = String(message || "").toLowerCase();
    return low.includes("import receipt") || low.includes("scan receipt") || (low.includes("receipt") && low.includes("import"));
  }

  async function importEveryDollarCsv() {
    if (!edCsvFile || !edCsvFile.files || !edCsvFile.files[0]) {
      if (edCsvStatus) edCsvStatus.textContent = "Choose a CSV file first.";
      return;
    }
    const file = edCsvFile.files[0];
    const formData = new FormData();
    formData.append("file", file);
    formData.append("month", state.budgetMonth);
    if (edCsvStatus) edCsvStatus.textContent = "Importing...";
    try {
      const fetchOptions = { method: "POST", body: formData };
      if (typeof authHeaders === "function") fetchOptions.headers = authHeaders(false);
      const res = await fetch(apiUrl("/budget/import/everydollar_csv"), fetchOptions);
      const data = await res.json();
      if (!res.ok || data.ok === false) {
        throw new Error(data.error || res.statusText);
      }
      if (edCsvStatus) edCsvStatus.textContent = `Imported ${data.imported} rows for ${data.month}.`;
      await loadBudgetData();
    } catch (err) {
      if (edCsvStatus) edCsvStatus.textContent = `Import failed: ${err}`;
    }
  }

  function inferMapping(columns) {
    const norm = (v) => String(v || "").toLowerCase().replace(/[^a-z0-9]/g, "");
    const mapping = {};
    const table = {};
    columns.forEach((col) => { table[col] = norm(col); });

    function pick(keys) {
      for (const col of columns) {
        if (keys.includes(table[col])) return col;
      }
      for (const col of columns) {
        if (keys.some((k) => table[col].includes(k))) return col;
      }
      return "";
    }

    mapping.date = pick(["date", "transactiondate", "posteddate"]);
    mapping.payee = pick(["payee", "merchant", "description", "vendor"]);
    mapping.amount = pick(["amount", "amt", "value", "total"]);
    mapping.category = pick(["category", "cat"]);
    mapping.group = pick(["group", "categorygroup"]);
    mapping.type = pick(["type", "transactiontype"]);
    mapping.memo = pick(["memo", "note", "notes", "comment"]);
    mapping.account = pick(["account", "acct"]);
    return mapping;
  }

  function buildMappingUI(columns) {
    if (!excelMapping) return;
    excelMapping.textContent = "";
    const fields = [
      { key: "date", label: "Date" },
      { key: "payee", label: "Payee" },
      { key: "amount", label: "Amount" },
      { key: "category", label: "Category" },
      { key: "group", label: "Group" },
      { key: "type", label: "Type" },
      { key: "memo", label: "Memo" },
      { key: "account", label: "Account" },
    ];
    const inferred = inferMapping(columns);

    fields.forEach((field) => {
      const label = document.createElement("label");
      label.textContent = field.label;
      const select = document.createElement("select");
      select.dataset.mappingField = field.key;
      const blank = document.createElement("option");
      blank.value = "";
      blank.textContent = "-";
      select.appendChild(blank);
      columns.forEach((col) => {
        const opt = document.createElement("option");
        opt.value = col;
        opt.textContent = col;
        if (inferred[field.key] === col) opt.selected = true;
        select.appendChild(opt);
      });
      label.appendChild(select);
      excelMapping.appendChild(label);
    });
  }

  async function previewExcel() {
    if (!excelFile || !excelFile.files || !excelFile.files[0]) {
      if (excelStatus) excelStatus.textContent = "Choose an Excel file first.";
      return;
    }
    const file = excelFile.files[0];
    const formData = new FormData();
    formData.append("file", file);
    if (excelStatus) excelStatus.textContent = "Previewing...";
    try {
      const fetchOptions = { method: "POST", body: formData };
      if (typeof authHeaders === "function") fetchOptions.headers = authHeaders(false);
      const res = await fetch(apiUrl("/budget/import/excel?preview=1"), fetchOptions);
      const data = await res.json();
      if (!res.ok || data.ok === false) {
        throw new Error(data.error || res.statusText);
      }
      buildMappingUI(data.columns || []);
      if (excelStatus) excelStatus.textContent = "Preview loaded.";
    } catch (err) {
      if (excelStatus) excelStatus.textContent = `Preview failed: ${err}`;
    }
  }

  async function importExcel() {
    if (!excelFile || !excelFile.files || !excelFile.files[0]) {
      if (excelStatus) excelStatus.textContent = "Choose an Excel file first.";
      return;
    }
    const file = excelFile.files[0];
    const mapping = {};
    if (excelMapping) {
      excelMapping.querySelectorAll("select").forEach((select) => {
        if (select.value) mapping[select.dataset.mappingField] = select.value;
      });
    }
    const formData = new FormData();
    formData.append("file", file);
    formData.append("mapping", JSON.stringify(mapping));
    formData.append("month", state.budgetMonth);
    if (excelStatus) excelStatus.textContent = "Importing...";
    try {
      const fetchOptions = { method: "POST", body: formData };
      if (typeof authHeaders === "function") fetchOptions.headers = authHeaders(false);
      const res = await fetch(apiUrl("/budget/import/excel"), fetchOptions);
      const data = await res.json();
      if (!res.ok || data.ok === false) {
        throw new Error(data.error || res.statusText);
      }
      if (excelStatus) excelStatus.textContent = `Imported ${data.imported} rows for ${data.month}.`;
      await loadBudgetData();
    } catch (err) {
      if (excelStatus) excelStatus.textContent = `Import failed: ${err}`;
    }
  }

  function setGraphStatus(text, isError) {
    if (!graphStatus) return;
    graphStatus.textContent = text || "";
    graphStatus.classList.toggle("negative", Boolean(isError));
  }

  function graphControlNumber(el, fallback) {
    const parsed = Number(el && el.value ? el.value : fallback);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function stopGraphAutoRefresh() {
    if (state.graph.autoRefreshHandle) {
      clearInterval(state.graph.autoRefreshHandle);
      state.graph.autoRefreshHandle = null;
    }
  }

  function syncGraphAutoRefresh() {
    stopGraphAutoRefresh();
    if (state.activeTab !== "graph") return;
    if (graphLive && !graphLive.checked) return;
    state.graph.autoRefreshHandle = window.setInterval(() => {
      if (state.activeTab === "graph") {
        loadGraph({ silent: true });
      }
    }, 30000);
  }

  function updateGraphStats(payload) {
    if (!graphStats) return;
    const stats = payload && payload.stats ? payload.stats : {};
    const filters = payload && payload.filters ? payload.filters : {};
    const updatedAt = payload && payload.updated_at ? new Date(payload.updated_at).toLocaleString() : "not built yet";
    graphStats.innerHTML = `
      <span class="graph-stat-pill"><strong>${stats.nodes || 0}</strong> total entities</span>
      <span class="graph-stat-pill"><strong>${stats.edges || 0}</strong> total relations</span>
      <span class="graph-stat-pill"><strong>${stats.subgraph_nodes || 0}</strong> visible nodes</span>
      <span class="graph-stat-pill"><strong>${stats.subgraph_edges || 0}</strong> visible edges</span>
      <span class="graph-stat-pill"><strong>${filters.query_match_count || 0}</strong> matches</span>
      <span class="graph-stat-pill"><strong>${escapeHtml(updatedAt)}</strong> updated</span>
    `;
  }

  function graphNodeById(nodeId) {
    const data = state.graph.data;
    if (!data || !Array.isArray(data.nodes)) return null;
    return data.nodes.find((node) => node.id === nodeId) || null;
  }

  function renderGraphDetails(nodeId) {
    if (!graphSelection || !graphDetails) return;
    const data = state.graph.data;
    const node = graphNodeById(nodeId);
    if (!data || !node) {
      graphSelection.textContent = "No node selected.";
      graphDetails.textContent = "Apollo will show node, edge, and document coverage details here.";
      return;
    }
    graphSelection.textContent = `${node.label} â€” degree ${node.degree}, docs ${node.doc_count}`;
    const relatedEdges = (data.edges || []).filter((edge) => edge.source === node.id || edge.target === node.id).slice(0, 12);
    const edgeLines = relatedEdges.length
      ? relatedEdges.map((edge) => {
          const direction = edge.source === node.id ? "â†’" : "â†";
          const other = edge.source === node.id ? edge.target : edge.source;
          return `${direction} ${other} (${edge.relation}, docs ${edge.doc_count || 0})`;
        })
      : ["No visible relations in the current subgraph."];
    const docLines = Array.isArray(node.sample_doc_ids) && node.sample_doc_ids.length
      ? node.sample_doc_ids.map((docId) => `- ${docId}`)
      : ["- none"];
    graphDetails.textContent = [
      `Entity: ${node.label}`,
      `Degree: ${node.degree} | In: ${node.in_degree} | Out: ${node.out_degree}`,
      `Matched query: ${node.matched ? "yes" : "no"} | Center node: ${node.center ? "yes" : "no"}`,
      "",
      "Visible relations:",
      ...edgeLines,
      "",
      "Sample chunk ids:",
      ...docLines,
    ].join("\n");
  }

  function buildGraphAdjacency(payload) {
    const adjacency = new Map();
    (payload.nodes || []).forEach((node) => adjacency.set(node.id, new Set()));
    (payload.edges || []).forEach((edge) => {
      if (!adjacency.has(edge.source)) adjacency.set(edge.source, new Set());
      if (!adjacency.has(edge.target)) adjacency.set(edge.target, new Set());
      adjacency.get(edge.source).add(edge.target);
      adjacency.get(edge.target).add(edge.source);
    });
    return adjacency;
  }

  function computeRadialLayout(payload, width, height, centerId) {
    const nodes = payload.nodes || [];
    const positions = new Map();
    if (!nodes.length) return positions;
    const adjacency = buildGraphAdjacency(payload);
    const defaultCenter = centerId && adjacency.has(centerId)
      ? centerId
      : [...nodes].sort((a, b) => (b.degree || 0) - (a.degree || 0))[0].id;
    const distances = new Map([[defaultCenter, 0]]);
    const queue = [defaultCenter];
    while (queue.length) {
      const current = queue.shift();
      const currentDistance = distances.get(current) || 0;
      (adjacency.get(current) || []).forEach((neighbor) => {
        if (!distances.has(neighbor)) {
          distances.set(neighbor, currentDistance + 1);
          queue.push(neighbor);
        }
      });
    }
    const maxDistance = Math.max(1, ...Array.from(distances.values()));
    const rings = new Map();
    nodes.forEach((node) => {
      const distance = distances.has(node.id) ? distances.get(node.id) : maxDistance + 1;
      if (!rings.has(distance)) rings.set(distance, []);
      rings.get(distance).push(node);
    });
    const centerX = width / 2;
    const centerY = height / 2;
    positions.set(defaultCenter, { x: centerX, y: centerY });
    Array.from(rings.entries())
      .sort((a, b) => a[0] - b[0])
      .forEach(([distance, ringNodes]) => {
        if (distance === 0) return;
        const radius = 90 + (distance * 95);
        ringNodes.sort((a, b) => (b.degree || 0) - (a.degree || 0));
        ringNodes.forEach((node, index) => {
          const angle = ((Math.PI * 2) / ringNodes.length) * index;
          positions.set(node.id, {
            x: centerX + Math.cos(angle) * radius,
            y: centerY + Math.sin(angle) * radius,
          });
        });
      });
    return positions;
  }

  function computeForceLayout(payload, width, height, centerId) {
    const nodes = payload.nodes || [];
    const edges = payload.edges || [];
    const positions = computeRadialLayout(payload, width, height, centerId);
    if (!nodes.length) return positions;
    const centerX = width / 2;
    const centerY = height / 2;
    const fixedCenter = centerId && positions.has(centerId) ? centerId : null;
    const area = width * height;
    const idealLength = Math.max(90, Math.sqrt(area / Math.max(nodes.length, 1)) * 0.95);
    for (let iter = 0; iter < 70; iter += 1) {
      const disp = new Map(nodes.map((node) => [node.id, { x: 0, y: 0 }]));
      for (let i = 0; i < nodes.length; i += 1) {
        for (let j = i + 1; j < nodes.length; j += 1) {
          const a = nodes[i];
          const b = nodes[j];
          const pa = positions.get(a.id);
          const pb = positions.get(b.id);
          const dx = pa.x - pb.x;
          const dy = pa.y - pb.y;
          const dist = Math.max(1, Math.hypot(dx, dy));
          const force = (idealLength * idealLength) / dist;
          const fx = (dx / dist) * force * 0.018;
          const fy = (dy / dist) * force * 0.018;
          disp.get(a.id).x += fx;
          disp.get(a.id).y += fy;
          disp.get(b.id).x -= fx;
          disp.get(b.id).y -= fy;
        }
      }
      edges.forEach((edge) => {
        const source = positions.get(edge.source);
        const target = positions.get(edge.target);
        if (!source || !target) return;
        const dx = target.x - source.x;
        const dy = target.y - source.y;
        const dist = Math.max(1, Math.hypot(dx, dy));
        const force = (dist - idealLength) * 0.02;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        disp.get(edge.source).x += fx;
        disp.get(edge.source).y += fy;
        disp.get(edge.target).x -= fx;
        disp.get(edge.target).y -= fy;
      });
      nodes.forEach((node) => {
        const pos = positions.get(node.id);
        const driftX = (centerX - pos.x) * 0.004;
        const driftY = (centerY - pos.y) * 0.004;
        disp.get(node.id).x += driftX;
        disp.get(node.id).y += driftY;
      });
      nodes.forEach((node) => {
        const pos = positions.get(node.id);
        if (fixedCenter && node.id === fixedCenter) {
          pos.x = centerX;
          pos.y = centerY;
          return;
        }
        const delta = disp.get(node.id);
        pos.x = Math.min(width - 36, Math.max(36, pos.x + delta.x));
        pos.y = Math.min(height - 36, Math.max(36, pos.y + delta.y));
      });
    }
    return positions;
  }

  function renderGraph(payload) {
    if (!graphCanvas) return;
    const nodes = payload && Array.isArray(payload.nodes) ? payload.nodes : [];
    const edges = payload && Array.isArray(payload.edges) ? payload.edges : [];
    if (!nodes.length) {
      graphCanvas.innerHTML = '<div class="graph-empty">No HippoRAG graph data is available yet. Run a graph build or wait for the nightly pipeline to publish `financial_kg.json`.</div>';
      renderGraphDetails("");
      return;
    }
    const width = Math.max(graphCanvas.clientWidth || 920, 720);
    const height = Math.max(graphCanvas.clientHeight || 620, 420);
    const selectedNodeId = state.graph.selectedNodeId && nodes.some((node) => node.id === state.graph.selectedNodeId)
      ? state.graph.selectedNodeId
      : ((payload.filters && payload.filters.center_found && payload.filters.center) || nodes[0].id);
    state.graph.selectedNodeId = selectedNodeId;
    const layoutMode = graphLayout && graphLayout.value ? graphLayout.value : "force";
    const positions = layoutMode === "radial"
      ? computeRadialLayout(payload, width, height, selectedNodeId)
      : computeForceLayout(payload, width, height, selectedNodeId);
    const edgeMarkup = edges.map((edge, index) => {
      const source = positions.get(edge.source);
      const target = positions.get(edge.target);
      if (!source || !target) return "";
      const midX = (source.x + target.x) / 2;
      const midY = (source.y + target.y) / 2;
      const showLabel = index < 30 && layoutMode === "radial";
      const label = showLabel ? `<text class="graph-edge-label" x="${midX.toFixed(1)}" y="${midY.toFixed(1)}">${escapeHtml(edge.relation || "related_to")}</text>` : "";
      return `
        <g>
          <line x1="${source.x.toFixed(1)}" y1="${source.y.toFixed(1)}" x2="${target.x.toFixed(1)}" y2="${target.y.toFixed(1)}" stroke="rgba(152, 173, 194, 0.30)" stroke-width="${Math.max(1, Math.min(3.5, 1 + (edge.doc_count || 0) * 0.18)).toFixed(1)}" />
          <title>${escapeHtml(`${edge.source} -> ${edge.target} (${edge.relation || "related_to"}, docs ${edge.doc_count || 0})`)}</title>
          ${label}
        </g>
      `;
    }).join("");
    const nodeMarkup = nodes.map((node) => {
      const pos = positions.get(node.id);
      if (!pos) return "";
      const encodedNodeId = encodeURIComponent(node.id);
      const radius = Math.max(8, Math.min(22, 8 + (node.doc_count || 0) * 0.9 + (node.degree || 0) * 0.25));
      const isSelected = node.id === selectedNodeId;
      const fill = node.center ? "#7ef0b2" : (node.matched ? "#79b8ff" : "#2f9a67");
      const stroke = isSelected ? "#f5f7fa" : "rgba(245,247,250,0.16)";
      const label = String(node.label || node.id);
      const trimmedLabel = label.length > 20 ? `${label.slice(0, 17)}...` : label;
      return `
        <g class="graph-node-button" data-node-id="${encodedNodeId}">
          <circle cx="${pos.x.toFixed(1)}" cy="${pos.y.toFixed(1)}" r="${radius.toFixed(1)}" fill="${fill}" fill-opacity="${isSelected ? "0.96" : "0.78"}" stroke="${stroke}" stroke-width="${isSelected ? "2.2" : "1.1"}"></circle>
          <text class="graph-node-label" x="${(pos.x + radius + 6).toFixed(1)}" y="${(pos.y + 4).toFixed(1)}">${escapeHtml(trimmedLabel)}</text>
          <title>${escapeHtml(`${label} | degree ${node.degree} | docs ${node.doc_count}`)}</title>
        </g>
      `;
    }).join("");
    graphCanvas.innerHTML = `<svg viewBox="0 0 ${width} ${height}" aria-label="Apollo HippoRAG graph">${edgeMarkup}${nodeMarkup}</svg>`;
    graphCanvas.querySelectorAll("[data-node-id]").forEach((el) => {
      el.addEventListener("click", () => {
        state.graph.selectedNodeId = decodeURIComponent(el.getAttribute("data-node-id") || "");
        renderGraph(state.graph.data);
        renderGraphDetails(state.graph.selectedNodeId);
      });
      el.addEventListener("dblclick", () => {
        const nodeId = decodeURIComponent(el.getAttribute("data-node-id") || "");
        if (graphQuery) graphQuery.value = nodeId;
        loadGraph({ center: nodeId, force: true });
      });
    });
    renderGraphDetails(selectedNodeId);
  }

  async function loadGraph(options) {
    const opts = options || {};
    if (!graphCanvas) return;
    const params = new URLSearchParams();
    const queryText = typeof opts.query === "string" ? opts.query : ((graphQuery && graphQuery.value) || "").trim();
    const centerNode = typeof opts.center === "string" ? opts.center : "";
    params.set("limit_nodes", String(graphControlNumber(graphLimit, 120)));
    params.set("min_degree", String(graphControlNumber(graphMinDegree, 1)));
    params.set("hops", String(graphControlNumber(graphHops, 2)));
    if (queryText) params.set("q", queryText);
    if (centerNode) params.set("center", centerNode);
    if (!opts.silent) {
      setGraphStatus("Loading HippoRAG graph...", false);
    }
    try {
      const payload = await apiGet(`/admin/hipporag/graph?${params.toString()}`);
      updateGraphStats(payload);
      if (!opts.force && payload.fingerprint === state.graph.fingerprint && state.graph.data) {
        if (!opts.silent) {
          setGraphStatus(`Graph already current (${payload.stats?.subgraph_nodes || 0} visible nodes).`, false);
        }
        return;
      }
      state.graph.data = payload;
      state.graph.fingerprint = payload.fingerprint || "";
      state.graph.lastLoadedAt = Date.now();
      if (centerNode) {
        state.graph.selectedNodeId = centerNode;
      } else if (!state.graph.selectedNodeId || !(payload.nodes || []).some((node) => node.id === state.graph.selectedNodeId)) {
        state.graph.selectedNodeId = (payload.filters && payload.filters.center_found && payload.filters.center) || ((payload.nodes || [])[0] && payload.nodes[0].id) || "";
      }
      renderGraph(payload);
      if (!opts.silent) {
        const subgraphNodes = payload.stats && payload.stats.subgraph_nodes ? payload.stats.subgraph_nodes : 0;
        const updated = payload.updated_at ? new Date(payload.updated_at).toLocaleTimeString() : "unknown";
        setGraphStatus(`Loaded ${subgraphNodes} nodes from the live HippoRAG graph. Updated ${updated}.`, false);
      }
    } catch (err) {
      setGraphStatus(`Graph load failed: ${err}`, true);
      if (!opts.silent && graphCanvas) {
        graphCanvas.innerHTML = `<div class="graph-empty">Graph load failed: ${escapeHtml(String(err))}</div>`;
      }
    }
  }

  function ensureGraphLoaded(force) {
    if (!graphCanvas) return;
    if (force || !state.graph.data) {
      loadGraph({ force: Boolean(force) });
    } else {
      renderGraph(state.graph.data);
      renderGraphDetails(state.graph.selectedNodeId);
    }
  }

  async function ping() {
    try {
      const h = await fetch(apiUrl("/health"));
      const m = await fetch(apiUrl("/meta"));
      if (h.ok) {
        statusEl.textContent = "online";
        statusEl.classList.remove("bad");
        statusEl.classList.add("good");
      } else {
        statusEl.textContent = "error";
        statusEl.classList.add("bad");
      }
      if (m.ok) {
        const meta = await m.json();
        const prov = meta.provider || "auto";
        const model = meta.ollama?.model || meta.owui?.model || "?";
        if (meta.ollama?.up && meta.ollama?.version) {
          modelEl.textContent = `${prov} - ${model} - ollama ${meta.ollama.version}`;
        } else {
          modelEl.textContent = `${prov} - ${model}`;
        }
      }
    } catch {
      statusEl.textContent = "offline";
      statusEl.classList.add("bad");
    }
  }

  function mountThinkingUi() {
    const bubble = appendChat("Apollo", "");
    if (!bubble) return null;
    bubble.innerHTML = `
      <div class="chat-thinking-head">
        <span class="chat-thinking-lamp" aria-hidden="true"></span>
        <span class="chat-thinking-shimmer">Apollo is thinking...</span>
        <span class="chat-stream-stats">0 chars • ~0 tok</span>
      </div>
      <div class="chat-gate-line">Routing</div>
      <div class="chat-reply-text"></div>
    `;
    bubble._replyText = "";
    bubble._gates = ["Routing"];
    return bubble;
  }

  function pushGate(bubble, gate) {
    if (!bubble || !gate) return;
    const gates = Array.isArray(bubble._gates) ? bubble._gates : (bubble._gates = []);
    if (!gates.includes(gate)) gates.push(gate);
    const gateEl = bubble.querySelector(".chat-gate-line");
    if (gateEl) gateEl.textContent = gates.join(" -> ");
  }

  function finalizeThinkingUi(bubble, text) {
    if (!bubble) return;
    const gates = Array.isArray(bubble._gates) ? bubble._gates : [];
    const gateLine = gates.length ? gates.join(" -> ") : "Routing";
    const finalText = String(text != null ? text : (bubble._replyText || ""));
    bubble.innerHTML = `
      <details class="chat-gate-caret">
        <summary>Gates passed (${gates.length || 1})</summary>
        <div class="chat-gate-line">${escapeHtml(gateLine)}</div>
      </details>
      <div class="chat-reply-text">${escapeHtml(finalText)}</div>
    `;
  }

  function updateThinkingUi(bubble, text) {
    if (!bubble) return;
    const replyText = String(text || "");
    bubble._replyText = replyText;
    const replyEl = bubble.querySelector(".chat-reply-text");
    if (replyEl) replyEl.textContent = replyText;
    const statsEl = bubble.querySelector(".chat-stream-stats");
    if (statsEl) {
      const approxTokens = replyText ? Math.max(1, Math.round(replyText.length / 4)) : 0;
      statsEl.textContent = `${replyText.length} chars • ~${approxTokens} tok`;
    }
    scrollChatToBottom();
  }

  function parseSseEvent(rawBlock) {
    const raw = String(rawBlock || "").trim();
    if (!raw) return null;
    const lines = raw.split(/\r?\n/);
    let eventName = "message";
    const dataLines = [];
    lines.forEach((line) => {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    });
    if (!dataLines.length) return null;
    const dataText = dataLines.join("\n");
    try {
      return { event: eventName, data: JSON.parse(dataText) };
    } catch {
      return { event: eventName, data: { text: dataText } };
    }
  }

  async function streamChatReply(payload, pendingBubble) {
    const headers = { "Content-Type": "application/json" };
    if (typeof authHeaders === "function") Object.assign(headers, authHeaders(false));
    const resp = await fetch(apiUrl("/chat/stream"), {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
    if (!resp.body) {
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || resp.statusText || "stream_failed");
      return data;
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finalPayload = null;
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const evt = parseSseEvent(block);
        if (evt && evt.event === "delta") {
          const delta = String((evt.data && evt.data.delta) || "");
          updateThinkingUi(pendingBubble, String((pendingBubble && pendingBubble._replyText) || "") + delta);
        } else if (evt && evt.event === "final") {
          finalPayload = evt.data || {};
        }
        boundary = buffer.indexOf("\n\n");
      }
      if (done) break;
    }
    return finalPayload || { reply: String((pendingBubble && pendingBubble._replyText) || "") };
  }

  function shouldUseConversationalFastPath(message) {
    const m = String(message || "").trim().toLowerCase();
    if (!m || m.includes("?")) return false;
    if (/^(hi|hey|hello|thanks|thank you|lol)\b/.test(m)) return true;
    if (/(how are you|how's it going|hows it going|what are you up to|whatcha up to)/.test(m)) return true;
    return m.length <= 48;
  }

  async function sendMessage() {
    const msg = (userInput.value || "").trim();
    if (!msg) return;
    const modelModeEl = document.getElementById('model-mode');
    const modelMode = modelModeEl ? modelModeEl.value : 'auto';
    const pinned = typeof window.pinnedModel === "function" ? window.pinnedModel() : "auto";
    const msgToSend = modelMode === 'fast' ? 'fast:' + msg :
                      modelMode === 'deep' ? 'deep:' + msg : msg;
    appendChat("You", msg);
    userInput.value = "";
    sendBtn.disabled = true;

    if (shouldTriggerReceiptImport(msg)) {
      if (budgetReceiptFile) {
        budgetReceiptFile.value = "";
        budgetReceiptFile.click();
      }
    }

    const pendingBubble = mountThinkingUi();
    if (pendingBubble) {
      pushGate(pendingBubble, modelMode === "deep" ? "GX10 deep lane" : "3090 chat lane");
      pushGate(pendingBubble, shouldUseConversationalFastPath(msg) ? "Fast dialogue path" : "Context gate");
      pushGate(pendingBubble, "Response stream");
    }

    try {
      const payload = {
        message: msgToSend,
        budget_focus: state.budgetFocus,
        budget_month: state.budgetMonth,
        ui_tab: state.activeTab,
        intent: state.budgetFocus ? "personal_finance" : "",
      };
      if (pinned && pinned !== "auto") payload.model = pinned;
      const data = await streamChatReply(payload, pendingBubble);
      finalizeThinkingUi(pendingBubble, data.reply || pendingBubble._replyText || "(no reply)");
      if (reasoningEl) reasoningEl.textContent = data.reasoning || "(none)";
      if (data && data.loan_review) renderLoanReview(data.loan_review.session || data.loan_review);
    } catch (err) {
      finalizeThinkingUi(pendingBubble, `(error: ${err})`);
    } finally {
      sendBtn.disabled = false;
      scrollChatToBottom();
    }
  }

  function appendChat(who, text) {
    if (!chatEl) return null;
    const p = document.createElement("div");
    const isUser = (who === "You");
    p.className = "chat-message " + (isUser ? "user" : "agent");
    p.textContent = text;
    chatEl.appendChild(p);
    scrollChatToBottom();
    return p;
  }

  function scrollChatToBottom() {
    if (!chatEl) return;
    requestAnimationFrame(() => {
      chatEl.scrollTop = chatEl.scrollHeight;
    });
  }

  function escapeHtml(text) {
    return String(text || "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  }

  function trimForPanel(text, maxChars = 1400) {
    const raw = String(text || "");
    if (raw.length <= maxChars) return raw;
    return `${raw.slice(0, maxChars - 3)}...`;
  }

  async function postMultipartJson(endpoint, formData) {
    const fetchOptions = { method: "POST", body: formData };
    if (typeof authHeaders === "function") fetchOptions.headers = authHeaders(false);
    const res = await fetch(apiUrl(endpoint), fetchOptions);
    let data = {};
    try {
      data = await res.json();
    } catch (_) {
      data = {};
    }
    if (!res.ok || data.ok === false) {
      throw new Error(data.error || res.statusText || `http_${res.status}`);
    }
    return data;
  }

  function clearOcrDashboardPreview() {
    if (!state.ocrDashboardPreviewUrl) return;
    try {
      URL.revokeObjectURL(state.ocrDashboardPreviewUrl);
    } catch (_) {}
    state.ocrDashboardPreviewUrl = "";
  }

  async function renderOcrDashboardRemotePreview(url, kind) {
    if (!ocrDashPreview) return;
    clearOcrDashboardPreview();
    ocrDashPreview.textContent = "";
    if (!url) {
      ocrDashPreview.textContent = "(preview unavailable)";
      return;
    }
    try {
      const headers = typeof authHeaders === "function" ? authHeaders(false) : {};
      const res = await fetch(url, { headers });
      if (!res.ok) throw new Error(`http_${res.status}`);
      const blob = await res.blob();
      const objectUrl = URL.createObjectURL(blob);
      state.ocrDashboardPreviewUrl = objectUrl;
      if (kind === "pdf") {
        const frame = document.createElement("iframe");
        frame.src = `${objectUrl}#page=1&zoom=page-width`;
        frame.title = "Example PDF preview";
        ocrDashPreview.appendChild(frame);
        return;
      }
      if (kind === "image") {
        const img = document.createElement("img");
        img.src = objectUrl;
        img.alt = "Example OCR source preview";
        ocrDashPreview.appendChild(img);
        return;
      }
      ocrDashPreview.textContent = "(preview unavailable for this file type)";
    } catch (err) {
      ocrDashPreview.textContent = `(preview unavailable: ${err})`;
    }
  }

  function renderOcrDashboardFilePreview(file) {
    if (!ocrDashPreview) return;
    clearOcrDashboardPreview();
    ocrDashPreview.textContent = "";
    if (!file) {
      ocrDashPreview.textContent = "(no file selected)";
      return;
    }
    const fileName = String(file.name || "").toLowerCase();
    const isPdf = String(file.type || "").toLowerCase() === "application/pdf" || fileName.endsWith(".pdf");
    const isImage = String(file.type || "").toLowerCase().startsWith("image/")
      || /\.(png|jpg|jpeg|bmp|tif|tiff|webp|gif)$/i.test(fileName);
    const objectUrl = URL.createObjectURL(file);
    state.ocrDashboardPreviewUrl = objectUrl;
    if (isPdf) {
      const frame = document.createElement("iframe");
      frame.src = `${objectUrl}#page=1&zoom=page-width`;
      frame.title = "PDF snippet preview";
      ocrDashPreview.appendChild(frame);
      return;
    }
    if (isImage) {
      const img = document.createElement("img");
      img.src = objectUrl;
      img.alt = "OCR source preview";
      ocrDashPreview.appendChild(img);
      return;
    }
    ocrDashPreview.textContent = "Preview not available for this file type.";
  }

  function renderOcrDashboardMetrics(comparePayload) {
    if (!ocrDashMetrics) return;
    const ocr97 = comparePayload && comparePayload.ocr97 ? comparePayload.ocr97 : {};
    const typical = comparePayload && comparePayload.typical ? comparePayload.typical : {};
    const ocr97q = ocr97 && ocr97.quality ? ocr97.quality : {};
    const typicalQ = typical && typical.quality ? typical.quality : {};
    const ocr97P2 = ocr97 && ocr97.phase2 ? ocr97.phase2 : {};
    const chips = [
      { label: "ocr97", value: ocr97 && ocr97.engine ? ocr97.engine : "unknown" },
      { label: "ocr97 score", value: ocr97q && ocr97q.score != null ? Number(ocr97q.score).toFixed(3) : "n/a" },
      { label: "ocr97 structure", value: ocr97q && ocr97q.structure_score != null ? Number(ocr97q.structure_score).toFixed(3) : "n/a" },
      { label: "ocr97 variants", value: ocr97P2 && ocr97P2.variant_count != null ? String(ocr97P2.variant_count) : "0" },
      { label: "typical", value: typical && typical.engine ? typical.engine : "unknown" },
      { label: "typical score", value: typicalQ && typicalQ.score != null ? Number(typicalQ.score).toFixed(3) : "n/a" },
      { label: "typical structure", value: typicalQ && typicalQ.structure_score != null ? Number(typicalQ.structure_score).toFixed(3) : "n/a" },
    ];
    ocrDashMetrics.innerHTML = chips
      .map((item) => `<span class="ocrdash-metric">${escapeHtml(item.label)}: <strong>${escapeHtml(item.value)}</strong></span>`)
      .join("");
  }

  function renderOcrDashboardComparison(result) {
    const source = result && result.source ? result.source : {};
    const snippet = source && source.snippet ? source.snippet : {};
    const ocr97 = result && result.ocr97 ? result.ocr97 : {};
    const typical = result && result.typical ? result.typical : {};
    if (ocrDashSnippet) {
      const snippetText = snippet && snippet.text ? snippet.text : "(no source snippet)";
      ocrDashSnippet.textContent = trimForPanel(snippetText, 1700);
    }
    if (ocrDashOutput) {
      ocrDashOutput.textContent = (ocr97 && ocr97.text) ? ocr97.text : "(no OCR97 output)";
    }
    if (ocrDashTypicalOutput) {
      ocrDashTypicalOutput.textContent = (typical && typical.text) ? typical.text : "(no typical OCR output)";
    }
    renderOcrDashboardMetrics(result);
  }

  function getOcrDashboardParams() {
    const goal = ocrDashGoal && ocrDashGoal.value ? String(ocrDashGoal.value) : "";
    const maxPages = Math.max(1, Math.min(8, Number(ocrDashMaxPages && ocrDashMaxPages.value ? ocrDashMaxPages.value : 1) || 1));
    const maxChars = Math.max(200, Math.min(20000, Number(ocrDashMaxChars && ocrDashMaxChars.value ? ocrDashMaxChars.value : 4000) || 4000));
    const typicalEngine = ocrDashTypicalEngine && ocrDashTypicalEngine.value ? String(ocrDashTypicalEngine.value) : "tesseract";
    return { goal, maxPages, maxChars, typicalEngine };
  }

  function resetOcrDashboardOutput(statusText) {
    if (ocrDashStatus) ocrDashStatus.textContent = statusText || "Running compare...";
    if (ocrDashSnippet) ocrDashSnippet.textContent = "(running source snippet...)";
    if (ocrDashOutput) ocrDashOutput.textContent = "(running OCR97...)";
    if (ocrDashTypicalOutput) ocrDashTypicalOutput.textContent = "(running typical OCR...)";
    if (ocrDashMetrics) ocrDashMetrics.textContent = "";
  }

  async function loadOcrDashboardExamples() {
    if (!ocrDashExampleSelect) return;
    try {
      const data = await apiGet("/ocr/dashboard/examples");
      const rows = Array.isArray(data.examples) ? data.examples : [];
      const previousSelection = String(ocrDashExampleSelect.value || "");
      state.ocrDashboardExamples = {};
      ocrDashExampleSelect.innerHTML = '<option value="">Choose built-in example...</option>';
      rows.forEach((row) => {
        if (!row || !row.id) return;
        state.ocrDashboardExamples[String(row.id)] = row;
        const opt = document.createElement("option");
        opt.value = String(row.id);
        opt.textContent = `${row.label || row.id}${row.exists ? "" : " (missing)"}`;
        if (!row.exists) opt.disabled = true;
        ocrDashExampleSelect.appendChild(opt);
      });
      const keepSelection = previousSelection && state.ocrDashboardExamples[previousSelection]
        && state.ocrDashboardExamples[previousSelection].exists;
      if (keepSelection) {
        ocrDashExampleSelect.value = previousSelection;
      } else {
        const firstExisting = rows.find((row) => row && row.id && row.exists);
        if (firstExisting && firstExisting.id) {
          ocrDashExampleSelect.value = String(firstExisting.id);
        }
      }
      const selectedId = String(ocrDashExampleSelect.value || "");
      if (selectedId) {
        const selected = state.ocrDashboardExamples[selectedId] || {};
        await renderOcrDashboardRemotePreview(
          apiUrl(selected.preview_url || selected.asset_url || ""),
          String(selected.preview_kind || "unknown")
        );
        if (ocrDashStatus && !(ocrDashFile && ocrDashFile.files && ocrDashFile.files[0])) {
          ocrDashStatus.textContent = "Example preloaded. Click 'Run Example Compare' to compare OCR outputs.";
        }
      }
    } catch (_) {
      if (ocrDashStatus) ocrDashStatus.textContent = "Could not load OCR examples.";
    }
  }

  async function runOcrDashboardUpload() {
    if (!ocrDashFile || !ocrDashFile.files || !ocrDashFile.files[0]) {
      if (ocrDashStatus) ocrDashStatus.textContent = "Select a file first.";
      return;
    }
    const file = ocrDashFile.files[0];
    const params = getOcrDashboardParams();

    renderOcrDashboardFilePreview(file);
    resetOcrDashboardOutput("Running OCR97 + typical compare...");

    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("goal", params.goal);
      formData.append("max_pages", String(params.maxPages));
      formData.append("max_chars", String(params.maxChars));
      formData.append("typical_engine", params.typicalEngine);
      const result = await postMultipartJson("/ocr/dashboard/compare", formData);
      renderOcrDashboardComparison(result);
      if (ocrDashStatus) ocrDashStatus.textContent = "Compare complete.";
    } catch (err) {
      if (ocrDashStatus) ocrDashStatus.textContent = `Compare failed: ${err}`;
      if (ocrDashOutput) ocrDashOutput.textContent = "";
      if (ocrDashTypicalOutput) ocrDashTypicalOutput.textContent = "";
    }
  }

  async function runOcrDashboardExample() {
    if (!ocrDashExampleSelect || !ocrDashExampleSelect.value) {
      if (ocrDashStatus) ocrDashStatus.textContent = "Choose an example first.";
      return;
    }
    const params = getOcrDashboardParams();
    const exampleId = String(ocrDashExampleSelect.value);
    const meta = state.ocrDashboardExamples[exampleId] || {};
    await renderOcrDashboardRemotePreview(
      apiUrl(meta.preview_url || meta.asset_url || ""),
      String(meta.preview_kind || "unknown")
    );
    resetOcrDashboardOutput("Running example compare...");
    try {
      const result = await apiPostJson("/ocr/dashboard/examples/run", {
        example_id: exampleId,
        goal: params.goal,
        max_pages: params.maxPages,
        max_chars: params.maxChars,
        typical_engine: params.typicalEngine,
      });
      const example = result && result.example ? result.example : {};
      await renderOcrDashboardRemotePreview(
        apiUrl(example.preview_url || example.asset_url || meta.preview_url || meta.asset_url || ""),
        String(example.preview_kind || meta.preview_kind || "unknown")
      );
      renderOcrDashboardComparison(result);
      if (ocrDashStatus) ocrDashStatus.textContent = "Example compare complete.";
    } catch (err) {
      if (ocrDashStatus) ocrDashStatus.textContent = `Example compare failed: ${err}`;
      if (ocrDashOutput) ocrDashOutput.textContent = "";
      if (ocrDashTypicalOutput) ocrDashTypicalOutput.textContent = "";
    }
  }

  async function runOcrDashboard() {
    const hasFile = Boolean(ocrDashFile && ocrDashFile.files && ocrDashFile.files[0]);
    if (hasFile) {
      await runOcrDashboardUpload();
      return;
    }
    await runOcrDashboardExample();
  }

  async function runOcr() {
    const ocrFile = byId("ocr-file");
    const ocrGoal = byId("ocr-goal");
    const ocrEngine = byId("ocr-engine");
    const ocrStatus = byId("ocr-status");
    const ocrOutput = byId("ocr-output");
    const mediaTask = byId("media-task");
    const visionMode = byId("vision-mode");
    const visionModel = byId("vision-model");

    if (!ocrFile || !ocrFile.files || !ocrFile.files[0]) {
      if (ocrStatus) ocrStatus.textContent = "Select a file first.";
      return;
    }

    const task = mediaTask ? mediaTask.value : "ocr";
    const formData = new FormData();
    formData.append("file", ocrFile.files[0]);
    formData.append("goal", ocrGoal && ocrGoal.value ? ocrGoal.value : "");

    let endpoint = "/ocr/run";
    if (task === "vision") {
      formData.append("mode", visionMode ? visionMode.value : "auto");
      formData.append("model", visionModel ? visionModel.value : "auto");
      endpoint = "/vision/run";
    } else {
      formData.append("engine", ocrEngine ? ocrEngine.value : "auto");
    }

    if (ocrStatus) ocrStatus.textContent = task === "vision" ? "Running vision..." : "Running OCR...";
    if (ocrOutput) ocrOutput.textContent = "(running...)";

    try {
      const data = await postMultipartJson(endpoint, formData);
      if (ocrStatus) ocrStatus.textContent = "Task complete.";
      const outputText = data.text || data.output || (data.data ? JSON.stringify(data.data, null, 2) : "");
      if (ocrOutput) ocrOutput.textContent = outputText;
    } catch (err) {
      if (ocrStatus) ocrStatus.textContent = `Task failed: ${err}`;
      if (ocrOutput) ocrOutput.textContent = "";
    }
  }

  async function runDialogue() {
    if (!dialogueLog) return;
    dialogueLog.textContent = "Running inter-agent dialogue...";
    try {
      const startResp = await fetch(apiUrl("/dialogue/start"), { method: "POST" });
      if (!startResp.ok) throw new Error(`start failed: ${startResp.status}`);
      const logResp = await fetch(apiUrl("/dialogue/log"));
      if (!logResp.ok) throw new Error(`log failed: ${logResp.status}`);
      const data = await logResp.json();
      dialogueLog.textContent = JSON.stringify(data, null, 2);
    } catch (err) {
      dialogueLog.textContent = `Error: ${err}`;
    }
  }

  function scheduleStatusClass(value) {
    if (value === true || String(value).toLowerCase() === "true") return "ok";
    if (value === false || String(value).toLowerCase() === "false") return "fail";
    return "";
  }

  function scheduleStatusText(value) {
    if (value === true || String(value).toLowerCase() === "true") return "ok";
    if (value === false || String(value).toLowerCase() === "false") return "needs review";
    if (value == null || value === "") return "no run yet";
    return String(value);
  }

  function renderScheduleJob(job) {
    if (!scheduleDetail) return;
    if (!job) {
      scheduleDetail.textContent = "No schedule detail available.";
      return;
    }
    const latest = job.latest || null;
    const runs = Array.isArray(job.runs) ? job.runs : [];
    const latestStatus = latest ? latest.ok : null;
    const commands = Array.isArray(job.commands) ? job.commands : [];
    const commandMarkup = commands.length ? `
      <div class="schedule-command-list">
        ${commands.map((item) => `
          <div class="schedule-command">
            <strong>${escapeHtml(item.label || "Command")}</strong>
            <code>${escapeHtml(item.command || "")}</code>
          </div>
        `).join("")}
      </div>
    ` : "";
    const runMarkup = runs.length ? runs.map((run) => {
      const files = Array.isArray(run.files) ? run.files : [];
      const summary = run.summary || {};
      return `
        <article class="schedule-run">
          <div class="schedule-run-head">
            <strong>${escapeHtml(run.run_id || "run")}</strong>
            <span class="schedule-status-pill ${scheduleStatusClass(run.ok)}">${escapeHtml(scheduleStatusText(run.ok))}</span>
          </div>
          <div class="schedule-run-meta">
            <span>${escapeHtml(run.mtime || "")}</span>
            <span>${escapeHtml(run.run_dir || "")}</span>
            ${summary.current_stage ? `<span>stage: ${escapeHtml(summary.current_stage)}</span>` : ""}
            ${summary.score != null ? `<span>score: ${escapeHtml(summary.score)}</span>` : ""}
            ${summary.best_ticker ? `<span>best: ${escapeHtml(summary.best_ticker)}</span>` : ""}
            ${summary.error ? `<span>error: ${escapeHtml(summary.error)}</span>` : ""}
          </div>
          ${files.map((file, idx) => `
            <details class="schedule-file-block" ${idx === 0 ? "open" : ""}>
              <summary>${escapeHtml(file.name || "file")} ${file.mtime ? `- ${escapeHtml(file.mtime)}` : ""}</summary>
              <pre>${escapeHtml(file.text || file.error || "(empty)")}</pre>
            </details>
          `).join("")}
        </article>
      `;
    }).join("") : '<div class="schedule-run">No past runs found for this job yet.</div>';
    scheduleDetail.innerHTML = `
      <div class="schedule-detail-head">
        <div>
          <h3>${escapeHtml(job.time || "")} - ${escapeHtml(job.title || "Scheduled job")}</h3>
          <p>${escapeHtml(job.description || "")}</p>
          <p>${escapeHtml((job.task_names || []).length ? `Windows task: ${(job.task_names || []).join(", ")}` : "No direct Windows task; driven by queue/tooling or report review.")}</p>
        </div>
        <span class="schedule-status-pill ${scheduleStatusClass(latestStatus)}">${escapeHtml(scheduleStatusText(latestStatus))}</span>
      </div>
      ${commandMarkup}
      <div class="schedule-runs">${runMarkup}</div>
    `;
  }

  async function loadScheduleJob(jobId) {
    if (!scheduleDetail) return;
    scheduleDetail.textContent = "Loading schedule runs...";
    document.querySelectorAll("[data-schedule-job]").forEach((item) => {
      item.classList.toggle("active", String(item.dataset.scheduleJob || "") === String(jobId || ""));
    });
    try {
      const data = await apiGet(`/admin/apollo/schedule_runs?job=${encodeURIComponent(jobId || "")}&limit=20`);
      renderScheduleJob(data && Array.isArray(data.jobs) ? data.jobs[0] : null);
    } catch (err) {
      scheduleDetail.textContent = `Schedule load failed: ${err}`;
    }
  }

  function wireEvents() {
    navItems.forEach((item) => {
      item.addEventListener("click", () => setActiveTab(item.dataset.tab));
    });

    const setScheduleOpen = (open) => {
      if (!apolloScheduleOverlay) return;
      apolloScheduleOverlay.hidden = !open;
      document.body.classList.toggle("schedule-open", Boolean(open));
      if (open) loadScheduleJob("day_trade");
    };

    const openApolloCalendar = () => setScheduleOpen(true);

    quickLinks.forEach((item) => {
      item.addEventListener("click", () => {
        const action = String(item.dataset.quickAction || "");
        const tab = String(item.dataset.quickTab || "");
        if (action === "calendar") {
          openApolloCalendar();
          return;
        }
        if (tab) setActiveTab(tab);
      });
    });

    if (apolloCalendarToggle) {
      apolloCalendarToggle.addEventListener("click", openApolloCalendar);
    }
    if (apolloScheduleToggle) {
      apolloScheduleToggle.addEventListener("click", openApolloCalendar);
    }
    if (swingCalendarOpen) {
      swingCalendarOpen.addEventListener("click", openApolloCalendar);
    }
    if (swingPretradeCheck) swingPretradeCheck.addEventListener("click", runPretradeCheck);
    if (swingDailyReportOpen) {
      swingDailyReportOpen.addEventListener("click", async () => {
        state.swing.dailyReportOpen = !state.swing.dailyReportOpen;
        if (state.swing.dailyReportOpen) await loadDailyReport();
        renderDailyReport();
      });
    }
    if (apolloScheduleClose) {
      apolloScheduleClose.addEventListener("click", () => setScheduleOpen(false));
    }
    if (apolloScheduleOverlay) {
      apolloScheduleOverlay.querySelectorAll("[data-schedule-close]").forEach((el) => {
        el.addEventListener("click", () => setScheduleOpen(false));
      });
      apolloScheduleOverlay.querySelectorAll("[data-schedule-job]").forEach((el) => {
        el.addEventListener("click", () => loadScheduleJob(el.dataset.scheduleJob || ""));
      });
    }

    if (budgetFocusToggle) {
      budgetFocusToggle.addEventListener("click", () => {
        state.budgetFocus = !state.budgetFocus;
        setChipState(state.budgetFocus);
      });
    }

    if (railToggle) {
      railToggle.addEventListener("click", () => toggleRail(!document.body.classList.contains("rail-open")));
    }
    if (railOverlay) {
      railOverlay.addEventListener("click", () => toggleRail(false));
    }
    if (topbarRuntimeToggle) {
      topbarRuntimeToggle.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        setTopbarRuntimeOpen(Boolean(topbarRuntimeMenu && topbarRuntimeMenu.hidden));
      });
    }
    if (uiCacheBustBtn) {
      uiCacheBustBtn.addEventListener("click", () => {
        window.location.replace(cacheBustUrl(window.location.href));
      });
    }
    if (rebuildReloadBtn) {
      rebuildReloadBtn.addEventListener("click", runRuntimeRefresh);
    }
    document.addEventListener("click", (event) => {
      if (!topbarRuntimeShell || !topbarRuntimeMenu || topbarRuntimeMenu.hidden) return;
      if (topbarRuntimeShell.contains(event.target)) return;
      setTopbarRuntimeOpen(false);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && topbarRuntimeMenu && !topbarRuntimeMenu.hidden) {
        setTopbarRuntimeOpen(false);
      }
      if (event.key === "Escape" && apolloScheduleOverlay && !apolloScheduleOverlay.hidden) {
        setScheduleOpen(false);
      }
    });

    if (budgetMonthInput) {
      budgetMonthInput.addEventListener("change", () => {
        state.budgetMonth = budgetMonthInput.value;
        loadBudgetData();
      });
    }

    if (budgetRefresh) budgetRefresh.addEventListener("click", loadBudgetData);
    if (categoryAdd) categoryAdd.addEventListener("click", addCategory);
    if (transactionAdd) transactionAdd.addEventListener("click", addTransaction);
    if (transactionSplit) transactionSplit.addEventListener("click", addSplitTransaction);
    if (budgetReceiptBtn) budgetReceiptBtn.addEventListener("click", () => budgetReceiptFile && budgetReceiptFile.click());
    if (budgetReceiptFile) budgetReceiptFile.addEventListener("change", () => {
      if (!budgetReceiptFile.files || !budgetReceiptFile.files[0]) return;
      importReceiptFile(budgetReceiptFile.files[0]);
    });
    if (accountAdd) accountAdd.addEventListener("click", addAccount);
    if (paycheckAdd) paycheckAdd.addEventListener("click", addPaycheck);
    if (goalAdd) goalAdd.addEventListener("click", addGoal);
    if (goalsAdd) goalsAdd.addEventListener("click", addGoal);

    if (overviewAddTransaction) overviewAddTransaction.addEventListener("click", () => {
      setActiveTab("budget");
      addTransaction();
    });
    if (overviewImport) overviewImport.addEventListener("click", () => setActiveTab("imports"));
    if (overviewRefreshInsights) overviewRefreshInsights.addEventListener("click", refreshInsights);
    if (overviewStartDialogue) overviewStartDialogue.addEventListener("click", runDialogue);

    if (insightsRefresh) insightsRefresh.addEventListener("click", refreshInsights);
    if (insightsRefreshStandalone) insightsRefreshStandalone.addEventListener("click", refreshInsights);

    if (edCsvImport) edCsvImport.addEventListener("click", importEveryDollarCsv);
    if (excelPreview) excelPreview.addEventListener("click", previewExcel);
    if (excelImport) excelImport.addEventListener("click", importExcel);

    if (scenarioSlider) scenarioSlider.addEventListener("input", updateScenario);

    if (sendBtn) sendBtn.addEventListener("click", sendMessage);
    if (userInput) {
      userInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          sendMessage();
        }
      });
    }

    const ocrRun = byId("ocr-run");
    if (ocrRun) ocrRun.addEventListener("click", runOcr);
    if (loanReviewCaptureBtn) loanReviewCaptureBtn.addEventListener("click", () => runLoanReviewCapture(false).catch((err) => { if (loanReviewStatus) loanReviewStatus.textContent = `Capture failed: ${err}`; }));
    if (loanReviewCompareBtn) loanReviewCompareBtn.addEventListener("click", () => runLoanReviewCapture(true).catch((err) => { if (loanReviewStatus) loanReviewStatus.textContent = `Compare failed: ${err}`; }));
    if (loanReviewPauseBtn) loanReviewPauseBtn.addEventListener("click", () => setLoanReviewMode({ mode_active: true, capture_paused: true, pause_reason: "login_blind_mode" }, "Capture paused for login blind mode.").catch((err) => { if (loanReviewStatus) loanReviewStatus.textContent = `Pause failed: ${err}`; }));
    if (loanReviewResumeBtn) loanReviewResumeBtn.addEventListener("click", () => setLoanReviewMode({ mode_active: true, capture_paused: false, pause_reason: "" }, "Capture resumed.").catch((err) => { if (loanReviewStatus) loanReviewStatus.textContent = `Resume failed: ${err}`; }));
    if (ocrDashRun) ocrDashRun.addEventListener("click", runOcrDashboard);
    if (ocrDashExampleRun) ocrDashExampleRun.addEventListener("click", runOcrDashboardExample);
    if (ocrDashFile) {
      ocrDashFile.addEventListener("change", () => {
        const file = ocrDashFile.files && ocrDashFile.files[0] ? ocrDashFile.files[0] : null;
        renderOcrDashboardFilePreview(file);
        if (ocrDashSnippet) ocrDashSnippet.textContent = "(no snippet yet)";
        if (ocrDashOutput) ocrDashOutput.textContent = "(no OCR97 output yet)";
        if (ocrDashTypicalOutput) ocrDashTypicalOutput.textContent = "(no typical OCR output yet)";
        if (ocrDashMetrics) ocrDashMetrics.textContent = "";
        if (ocrDashStatus) ocrDashStatus.textContent = file ? "File loaded. Run compare to render source/OCR97/typical output." : "Select a file first.";
      });
    }
    if (ocrDashExampleSelect) {
      ocrDashExampleSelect.addEventListener("change", () => {
        const id = String(ocrDashExampleSelect.value || "");
        const meta = state.ocrDashboardExamples[id] || {};
        if (!id) {
          if (!(ocrDashFile && ocrDashFile.files && ocrDashFile.files[0])) {
            if (ocrDashPreview) ocrDashPreview.textContent = "(no file selected)";
          }
          return;
        }
        renderOcrDashboardRemotePreview(
          apiUrl(meta.preview_url || meta.asset_url || ""),
          String(meta.preview_kind || "unknown")
        )
          .catch(() => {});
        if (ocrDashStatus) ocrDashStatus.textContent = "Example selected. Click 'Run Example Compare'.";
      });
    }
    if (dialogueRun) dialogueRun.addEventListener("click", runDialogue);
    if (graphLaunch) graphLaunch.addEventListener("click", () => loadGraph({ force: true }));
    if (graphRefresh) graphRefresh.addEventListener("click", () => loadGraph({ force: true }));
    if (graphQuery) {
      graphQuery.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          loadGraph({ force: true });
        }
      });
    }
    if (graphLayout) graphLayout.addEventListener("change", () => ensureGraphLoaded(true));
    if (graphHops) graphHops.addEventListener("change", () => loadGraph({ force: true }));
    if (graphLimit) graphLimit.addEventListener("change", () => loadGraph({ force: true }));
    if (graphMinDegree) graphMinDegree.addEventListener("change", () => loadGraph({ force: true }));
    if (graphLive) graphLive.addEventListener("change", syncGraphAutoRefresh);
    if (swingRun) swingRun.addEventListener("click", runSwingStudy);
    if (swingRefresh) swingRefresh.addEventListener("click", loadSwingLatest);
    if (swingStatusFilter) swingStatusFilter.addEventListener("change", renderSwing);
    if (swingWatch) swingWatch.addEventListener("click", () => decideSwing("watching"));
    if (swingPaper) swingPaper.addEventListener("click", () => decideSwing("paper_trade"));
    if (swingReject) swingReject.addEventListener("click", () => decideSwing("reject"));
    if (swingSimRefresh) swingSimRefresh.addEventListener("click", loadSwingSimulationAccount);
    if (swingSimReportToggle) {
      swingSimReportToggle.addEventListener("click", () => {
        state.swing.reportOpen = !state.swing.reportOpen;
        renderSwingSimulationAccount();
      });
    }
    loadLoanReviewStatus();
  }

  function init() {
    state.budgetMonth = currentMonth();
    if (budgetMonthInput) budgetMonthInput.value = state.budgetMonth;
    if (runtimeMenuStatus) runtimeMenuStatus.textContent = "Runtime controls ready.";
    setChipState(state.budgetFocus);
    wireEvents();
    ping();
    setInterval(ping, 10000);
    loadBudgetData();
    syncGraphAutoRefresh();
    loadOcrDashboardExamples();
    window.addEventListener("beforeunload", clearOcrDashboardPreview);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();


