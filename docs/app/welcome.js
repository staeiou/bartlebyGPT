import { getAllPrompts, getStarterPrompts, PROMPT_ORDER } from "./prompts.js?v=20260320a1";

const EM_DASH = "\u2014";

export function createWelcomeController({ elements, state, updateInputCount }) {
  function useStarterPrompt(prompt) {
    if (!prompt || state.busy) return;

    const stripped = prompt.replace(/[:,]?\s*\[paste[^\]]*\]\s*$/, "").trimEnd();
    const bracketMatch = stripped.match(/\[([^\]]*)\]/);
    let cursorPos;
    let filledPrompt;

    if (bracketMatch) {
      cursorPos = bracketMatch.index;
      filledPrompt = stripped.slice(0, cursorPos) + stripped.slice(cursorPos + bracketMatch[0].length);
    } else {
      filledPrompt = stripped;
      cursorPos = filledPrompt.length;
    }

    elements.input.value = filledPrompt;
    updateInputCount();
    elements.input.focus();
    elements.input.setSelectionRange(cursorPos, cursorPos);
    elements.inputHint.textContent = "";
  }

  function clearWelcome() {
    const welcome = elements.messages.querySelector(".welcome-shell");
    if (!welcome) return;
    welcome.remove();
    elements.messages.classList.remove("is-welcome");
  }

  function updateWelcomeCardFit() {
    const shell = elements.messages.querySelector(".welcome-shell");
    if (!shell) return;

    const grid = shell.querySelector(".suggestion-grid");
    const toggleBar = shell.querySelector(".prompt-view-toggles");
    if (!grid || !toggleBar) return;

    grid.classList.remove("is-compact-mobile");

    if (!window.matchMedia("(max-width: 940px)").matches || grid.classList.contains("is-expanded")) {
      return;
    }

    const messagesRect = elements.messages.getBoundingClientRect();
    const toggleRect = toggleBar.getBoundingClientRect();
    if (toggleRect.bottom > messagesRect.bottom) {
      grid.classList.add("is-compact-mobile");
    }
  }

  function requestWelcomeCardFit() {
    window.requestAnimationFrame(updateWelcomeCardFit);
  }

  function makeCard(starter, expanded) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "suggestion-card";

    if (expanded) {
      const kicker = document.createElement("p");
      kicker.className = "suggestion-kicker";
      kicker.textContent = starter.kicker || "";
      button.appendChild(kicker);
    }

    const heading = document.createElement("h3");
    heading.className = "suggestion-title";
    heading.textContent = starter.title;

    const prompt = document.createElement("p");
    prompt.className = "suggestion-prompt";
    prompt.textContent = starter.prompt;

    button.appendChild(heading);
    button.appendChild(prompt);
    button.addEventListener("click", () => useStarterPrompt(starter.prompt));
    return button;
  }

  function matchesPromptQuery(starter, normalizedQuery) {
    if (!normalizedQuery) return true;
    const haystack = `${starter.kicker || ""} ${starter.title || ""} ${starter.prompt || ""}`.toLowerCase();
    return haystack.includes(normalizedQuery);
  }

  function renderExpandedCards(grid, prompts, query, metaEl) {
    const normalizedQuery = query.trim().toLowerCase();
    const visiblePrompts = normalizedQuery
      ? prompts.filter((starter) => matchesPromptQuery(starter, normalizedQuery))
      : prompts;

    grid.textContent = "";

    if (!visiblePrompts.length) {
      const empty = document.createElement("div");
      empty.className = "prompt-search-empty";

      const title = document.createElement("p");
      title.className = "prompt-search-empty-title";
      title.textContent = "No prompts matched your search.";

      const body = document.createElement("p");
      body.className = "prompt-search-empty-body";
      body.textContent = `Try a different keyword or clear "${query.trim()}".`;

      empty.appendChild(title);
      empty.appendChild(body);
      grid.appendChild(empty);
    } else {
      visiblePrompts.forEach((starter) => {
        grid.appendChild(makeCard(starter, true));
      });
    }

    if (metaEl) {
      const total = prompts.length;
      const visible = visiblePrompts.length;
      metaEl.textContent = normalizedQuery
        ? `${visible} of ${total} prompts`
        : `${total} prompts`;
    }
  }

  function buildIndex(prompts) {
    const groups = {};
    PROMPT_ORDER.forEach((kicker) => {
      groups[kicker] = [];
    });

    prompts.forEach((prompt) => {
      if (groups[prompt.kicker]) {
        groups[prompt.kicker].push(prompt);
      } else {
        groups[prompt.kicker] = [prompt];
      }
    });

    const index = document.createElement("div");
    index.className = "prompt-index";

    PROMPT_ORDER.forEach((kicker) => {
      const items = groups[kicker];
      if (!items || !items.length) return;

      const section = document.createElement("section");
      section.className = "index-group";

      const heading = document.createElement("h4");
      heading.className = "index-kicker-heading";
      heading.textContent = kicker;

      const list = document.createElement("ul");
      list.className = "index-list";

      items.forEach((starter) => {
        const item = document.createElement("li");
        const button = document.createElement("button");
        button.type = "button";
        button.className = "index-item";

        const titleSpan = document.createElement("span");
        titleSpan.className = "index-item-title";
        titleSpan.textContent = starter.title;

        const separator = document.createElement("span");
        separator.className = "index-item-sep";
        separator.textContent = EM_DASH;

        const description = document.createElement("span");
        description.className = "index-item-desc";
        description.textContent = starter.prompt;

        button.appendChild(titleSpan);
        button.appendChild(separator);
        button.appendChild(description);
        button.addEventListener("click", () => useStarterPrompt(starter.prompt));
        item.appendChild(button);
        list.appendChild(item);
      });

      section.appendChild(heading);
      section.appendChild(list);
      index.appendChild(section);
    });

    return index;
  }

  function setView(view, grid, toggleBar, searchControls) {
    const [cardsBtn, indexBtn] = toggleBar.children;
    const allPrompts = getAllPrompts();
    const searchInput = searchControls.querySelector(".prompt-search-input");
    const searchMeta = searchControls.querySelector(".prompt-search-meta");

    cardsBtn.classList.remove("is-active");
    indexBtn.classList.remove("is-active");
    grid.classList.remove("is-expanded");
    elements.messages.classList.remove("is-gallery");
    searchControls.hidden = true;
    searchControls._allPrompts = [];
    if (searchInput) searchInput.value = "";
    if (searchMeta) searchMeta.textContent = "";

    const existingIndex = grid.parentNode.querySelector(".prompt-index");
    if (existingIndex) {
      existingIndex.remove();
    }
    grid.style.display = "";

    if (view === "cards") {
      cardsBtn.classList.add("is-active");
      grid.classList.add("is-expanded");
      grid.classList.remove("is-compact-mobile");
      elements.messages.classList.add("is-gallery");
      const orderedPrompts = [...allPrompts].sort(() => Math.random() - 0.5);
      searchControls.hidden = false;
      searchControls._allPrompts = orderedPrompts;
      renderExpandedCards(grid, orderedPrompts, "", searchMeta);
      elements.messages.scrollTop = 0;
    } else if (view === "index") {
      indexBtn.classList.add("is-active");
      elements.messages.classList.add("is-gallery");
      grid.style.display = "none";
      const index = buildIndex(allPrompts);
      toggleBar.parentNode.insertBefore(index, toggleBar);
      elements.messages.scrollTop = 0;
    }
  }

  function renderWelcome() {
    if (state.history.length || elements.messages.querySelector(".message-row")) return;

    elements.messages.textContent = "";
    elements.messages.classList.add("is-welcome");

    const shell = document.createElement("section");
    shell.className = "welcome-shell";

    const title = document.createElement("h2");
    title.className = "welcome-title";
    title.textContent = "The world's most ethical AI. For good.";

    const subtitle = document.createElement("p");
    subtitle.className = "welcome-subtitle";
    subtitle.innerHTML = "Trust <em>Bartleby</em> for work that requires serious judgment.";

    const grid = document.createElement("div");
    grid.className = "suggestion-grid";
    getStarterPrompts().forEach((starter) => {
      grid.appendChild(makeCard(starter, false));
    });

    const searchControls = document.createElement("div");
    searchControls.className = "prompt-gallery-controls";
    searchControls.hidden = true;
    searchControls._allPrompts = [];

    const searchInput = document.createElement("input");
    searchInput.type = "search";
    searchInput.className = "prompt-search-input";
    searchInput.placeholder = "Search prompts by title, category, or text";
    searchInput.setAttribute("aria-label", "Search prompts");
    searchInput.autocomplete = "off";
    searchInput.spellcheck = false;

    const searchMeta = document.createElement("p");
    searchMeta.className = "prompt-search-meta";

    searchInput.addEventListener("input", () => {
      renderExpandedCards(grid, searchControls._allPrompts || [], searchInput.value, searchMeta);
    });

    searchInput.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && searchInput.value) {
        searchInput.value = "";
        renderExpandedCards(grid, searchControls._allPrompts || [], "", searchMeta);
      }
    });

    searchControls.appendChild(searchInput);
    searchControls.appendChild(searchMeta);

    const toggleBar = document.createElement("div");
    toggleBar.className = "prompt-view-toggles";

    const cardsBtn = document.createElement("button");
    cardsBtn.type = "button";
    cardsBtn.className = "prompt-view-toggle";
    cardsBtn.textContent = `${EM_DASH} all prompts ${EM_DASH}`;

    const indexBtn = document.createElement("button");
    indexBtn.type = "button";
    indexBtn.className = "prompt-view-toggle";
    indexBtn.textContent = `${EM_DASH} index ${EM_DASH}`;
    indexBtn.style.display = "none";

    cardsBtn.addEventListener("click", () => setView("cards", grid, toggleBar, searchControls));
    indexBtn.addEventListener("click", () => setView("index", grid, toggleBar, searchControls));

    toggleBar.appendChild(cardsBtn);
    toggleBar.appendChild(indexBtn);

    shell.appendChild(title);
    shell.appendChild(subtitle);
    shell.appendChild(searchControls);
    shell.appendChild(grid);
    shell.appendChild(toggleBar);
    elements.messages.appendChild(shell);
    if (window.innerWidth < 540) {
      setView("cards", grid, toggleBar, searchControls);
      searchMeta.hidden = true;
    }
    requestWelcomeCardFit();
  }

  function initCardRotation() {
    const FIRST_DELAY = 15000;
    const CYCLE_DELAY = 10000;
    const STAGGER_MS = 150;
    const FADE_MS = 400;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let rotationTimer = null;
    let hovered = false;

    function scheduleRotation(delay) {
      window.clearTimeout(rotationTimer);
      rotationTimer = window.setTimeout(rotateCycle, delay);
    }

    function replaceCardContent(card, starter) {
      const title = card.querySelector(".suggestion-title");
      const prompt = card.querySelector(".suggestion-prompt");
      if (!title || !prompt || !card.parentNode) return;

      title.textContent = starter.title;
      prompt.textContent = starter.prompt;

      const newCard = card.cloneNode(true);
      newCard.classList.remove("dissolving");
      newCard.addEventListener("click", () => useStarterPrompt(starter.prompt));
      card.parentNode.replaceChild(newCard, card);
      newCard.addEventListener("mouseenter", () => {
        hovered = true;
      });
      newCard.addEventListener("mouseleave", () => {
        hovered = false;
      });
    }

    function rotateCycle() {
      const grid = elements.messages.querySelector(".suggestion-grid");
      if (!grid || state.history.length) return;
      if (state.idle) {
        scheduleRotation(2000);
        return;
      }
      if (hovered || elements.messages.classList.contains("is-gallery")) {
        scheduleRotation(CYCLE_DELAY);
        return;
      }

      const cards = Array.from(grid.querySelectorAll(".suggestion-card"));
      const newPrompts = getStarterPrompts();

      if (reducedMotion) {
        cards.forEach((card, index) => {
          replaceCardContent(card, newPrompts[index]);
        });
        scheduleRotation(CYCLE_DELAY);
        return;
      }

      const order = cards
        .map((_, index) => index)
        .sort(() => Math.random() - 0.5);

      order.forEach((cardIndex, staggerIndex) => {
        const card = cards[cardIndex];
        const starter = newPrompts[cardIndex];
        window.setTimeout(() => {
          card.classList.add("dissolving");
          window.setTimeout(() => {
            replaceCardContent(card, starter);
          }, FADE_MS);
        }, staggerIndex * STAGGER_MS);
      });

      const totalDuration = (order.length - 1) * STAGGER_MS + FADE_MS;
      scheduleRotation(CYCLE_DELAY + totalDuration);
    }

    const observer = new MutationObserver(() => {
      const grid = elements.messages.querySelector(".suggestion-grid");
      if (grid && !grid.dataset.rotationBound) {
        grid.dataset.rotationBound = "true";
        grid.addEventListener("mouseenter", () => {
          hovered = true;
        });
        grid.addEventListener("mouseleave", () => {
          hovered = false;
        });
        scheduleRotation(FIRST_DELAY);
      }
      if (!grid) {
        window.clearTimeout(rotationTimer);
        hovered = false;
      }
    });

    observer.observe(elements.messages, { childList: true, subtree: false });
  }

  return {
    useStarterPrompt,
    clearWelcome,
    renderWelcome,
    initCardRotation,
    requestWelcomeCardFit,
  };
}
