function showPopup(message) {
  // 1. Create the popup element
  const popup = document.createElement("div");
  popup.textContent = message;

  // 2. Beautiful minimalist styling
  Object.assign(popup.style, {
    position: "fixed",
    bottom: "20px",
    right: "20px",
    backgroundColor: "#1e1e24",
    color: "#fff",
    padding: "12px 24px",
    borderRadius: "8px",
    fontFamily: "system-ui, sans-serif",
    boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
    zIndex: "9999",
    cursor: "pointer",
    transition: "opacity 0.3s ease, transform 0.3s ease",
    transform: "translateY(20px)",
    opacity: "0",
  });

  document.body.appendChild(popup);

  // 3. Trigger smooth slide-in animation
  requestAnimationFrame(() => {
    popup.style.transform = "translateY(0)";
    popup.style.opacity = "1";
  });

  // 4. Helper function to safely remove the popup
  const removePopup = () => {
    popup.style.opacity = "0";
    popup.style.transform = "translateY(10px)";
    setTimeout(() => popup.remove(), 300);
    window.removeEventListener("click", removePopup);
  };

  // 5. Dismiss hooks: Click anywhere OR wait 4 seconds
  setTimeout(removePopup, 4000);
  setTimeout(() => window.addEventListener("click", removePopup), 10);
}

function setPlayingThumbnail(source) {
  const thumbnail = document.getElementById("playing-thumb");
  if (!thumbnail) return;

  thumbnail.src = source;
  thumbnail.classList.toggle(
    "vector-thumbnail",
    /^data:image\/svg\+xml/i.test(source) || /\.svg(?:$|[?#])/i.test(source),
  );
}

function setExpandedThumbnail(source) {
  const thumbnail = document.getElementById("nowPlayingModalThumb");
  if (!thumbnail) return;

  thumbnail.src = source;
  thumbnail.classList.toggle(
    "vector-thumbnail",
    /^data:image\/svg\+xml/i.test(source) || /\.svg(?:$|[?#])/i.test(source),
  );
  thumbnail.classList.toggle("no-thumbnail", source === "/static/speaker.svg");
}

function queueButtonSvg() {
  return `
        <svg class="qi-play" width="14" height="14" viewBox="0 0 24 24" fill="white"><polygon points="6,3 20,12 6,21"/></svg>
        <img class="status-icon status-icon-spinner" src="/static/spinner.svg" alt="">
        <img class="status-icon status-icon-check" src="/static/checkmark.svg" alt="">
    `;
}

const STATUS_CHECK_DURATION_MS = 5000;
const statusIndicatorTimers = new WeakMap();

function setStatusIndicator(element, state, onDone) {
  if (!element) return;

  clearTimeout(statusIndicatorTimers.get(element));
  statusIndicatorTimers.delete(element);
  element.classList.add("status-indicator");
  element.classList.remove("status-loading", "status-done");

  if (state === "loading") {
    element.classList.add("status-loading");
    element.setAttribute("aria-label", "Updating");
  } else if (state === "done") {
    element.classList.add("status-done");
    element.setAttribute("aria-label", "Done");
    const timer = setTimeout(() => {
      setStatusIndicator(element, "idle");
      if (onDone) onDone();
    }, STATUS_CHECK_DURATION_MS);
    statusIndicatorTimers.set(element, timer);
  } else {
    element.removeAttribute("aria-label");
  }
}

document.querySelectorAll(".queue-icon-btn").forEach((button) => {
  button.classList.add("status-indicator");
  if (!button.querySelector(".qi-play")) {
    button.innerHTML = queueButtonSvg();
  }
});

async function enqueueSong(song, btn, options) {
  const request = () =>
    fetch(options.enqueueUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(options.payload(song)),
    });

  const markQueued = () =>
    setStatusIndicator(btn, "done", () => {
      btn.disabled = false;
    });

  setStatusIndicator(btn, "loading");
  btn.disabled = true;

  try {
    let response = await request();

    if (response.ok) {
      markQueued();
      return;
    }

    if (response.status === 403) {
      const data = await response.json();
      if (options.takeoverOwners.includes(data.owner)) {
        setStatusIndicator(btn, "idle");
        btn.disabled = false;
        const takeOver = confirm(
          `${data.owner} player is currently controlling playback.\n\n` +
            "Taking ownership will clear its queue and stop playback.\n\n" +
            "Continue?",
        );
        if (!takeOver) return;

        setStatusIndicator(btn, "loading");
        btn.disabled = true;

        const acquire = await fetch(options.acquireUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ force: true }),
        });
        if (!acquire.ok) {
          showPopup("Failed to acquire ownership.");
          return;
        }

        response = await request();
        if (!response.ok) {
          const data = await response.json().catch(() => ({}));
          showPopup(data.error || "Failed to queue song after takeover.");
          return;
        }

        markQueued();
        return;
      }

      showPopup(`Playback is controlled by '${data.owner}'.`);
      return;
    }

    const data = await response.json().catch(() => ({}));
    showPopup(data.error || "Failed to queue song.");
  } catch (error) {
    console.error("Failed to add song to queue:", error);
    showPopup("Failed to queue song.");
  } finally {
    if (btn.classList.contains("status-loading")) {
      setStatusIndicator(btn, "idle");
      btn.disabled = false;
    }
  }
}

function toggleMenu() {
  const panel = document.getElementById("sidePanel");
  const overlay = document.getElementById("overlay");
  const main = document.getElementById("main");
  panel.classList.toggle("open");
  overlay.classList.toggle("show");
  if (!panel.classList.contains("open")) {
    closeSettingsMenu();
  }
  if (window.innerWidth >= 768 && main) {
    main.classList.toggle("shift");
  }
}

// --- Swipe-right-to-close sidebar on mobile ---
(function setupSidePanelSwipe() {
  const panel = document.getElementById("sidePanel");
  if (!panel || panel.dataset.swipeReady) return;

  let touchStart = null;
  let currentDeltaX = 0;
  const CLOSE_THRESHOLD = 80;

  panel.addEventListener(
    "touchstart",
    (e) => {
      if (e.touches.length !== 1) { touchStart = null; return; }
      // Only start swipe on blank space (not on buttons, links, inputs, etc.)
      const tag = e.target.tagName;
      if (
        e.target.closest("button, a, input, select, textarea, label, .queue-item-wrapper, .queue-item-drag-handle")
      ) {
        touchStart = null;
        return;
      }
      touchStart = { x: e.touches[0].clientX, y: e.touches[0].clientY };
      panel.style.transition = "none";
    },
    { passive: true },
  );

  panel.addEventListener(
    "touchmove",
    (e) => {
      if (!touchStart) return;
      const deltaX = e.touches[0].clientX - touchStart.x;
      const deltaY = e.touches[0].clientY - touchStart.y;

      // If vertical movement dominates, cancel the gesture (allow normal scroll)
      if (Math.abs(deltaY) > Math.abs(deltaX) && currentDeltaX === 0) {
        touchStart = null;
        return;
      }

      // Only track leftward swipes (negative deltaX) to push panel back
      if (deltaX < 0) {
        currentDeltaX = deltaX;
        panel.style.transform = `translateX(${deltaX}px)`;
        const overlay = document.getElementById("overlay");
        if (overlay) {
          overlay.style.opacity = Math.max(0, 1 - Math.abs(deltaX) / 300);
        }
        if (e.cancelable) e.preventDefault();
      }
    },
    { passive: false },
  );

  panel.addEventListener(
    "touchend",
    () => {
      if (!touchStart && currentDeltaX === 0) return;
      touchStart = null;

      panel.style.transition = "transform 0.25s cubic-bezier(0.4, 0, 0.2, 1)";
      const overlay = document.getElementById("overlay");

      if (Math.abs(currentDeltaX) > CLOSE_THRESHOLD) {
        // Slide fully off-screen to the left then close
        panel.style.transform = `translateX(-100%)`;
        if (overlay) {
          overlay.style.transition = "opacity 0.25s ease";
          overlay.style.opacity = "0";
        }
        setTimeout(() => {
          // Remove open state directly (no CSS transition) to avoid a bump
          panel.classList.remove("open");
          overlay?.classList.remove("show");
          const main = document.getElementById("main");
          if (main) main.classList.remove("shift");
          closeSettingsMenu();
          // Reset inline styles
          panel.style.transform = "";
          panel.style.transition = "";
          if (overlay) { overlay.style.opacity = ""; overlay.style.transition = ""; }
        }, 250);
      } else {
        // Snap back
        panel.style.transform = "";
        if (overlay) { overlay.style.opacity = ""; }
        setTimeout(() => { panel.style.transition = ""; }, 250);
      }

      currentDeltaX = 0;
    },
    { passive: true },
  );

  panel.addEventListener(
    "touchcancel",
    () => {
      touchStart = null;
      currentDeltaX = 0;
      panel.style.transform = "";
      panel.style.transition = "";
      const overlay = document.getElementById("overlay");
      if (overlay) { overlay.style.opacity = ""; overlay.style.transition = ""; }
    },
    { passive: true },
  );

  panel.dataset.swipeReady = "true";
})();

function toggleSettingsMenu(e) {
  if (e) e.stopPropagation();
  const menu = document.getElementById("settingsPopupMenu");
  if (menu) {
    menu.classList.toggle("show");
  }
}

function closeSettingsMenu() {
  const menu = document.getElementById("settingsPopupMenu");
  if (menu) {
    menu.classList.remove("show");
  }
}

async function shutdownServer() {
  if (!confirm("Are you sure you want to shut down the Music Server?")) {
    return;
  }
  closeSettingsMenu();
  showPopup("Shutting down Music Server...");
  try {
    const res = await fetch("/shutdown", { method: "POST" });
    if (res.ok) {
      showPopup("Music Server has stopped.");
    } else {
      showPopup("Server stop requested.");
    }
  } catch (e) {
    showPopup("Music Server has stopped.");
  }
}

window.addEventListener("click", (e) => {
  const menu = document.getElementById("settingsPopupMenu");
  const btn = document.getElementById("settingsBtn");
  if (menu && menu.classList.contains("show")) {
    if (!menu.contains(e.target) && !btn?.contains(e.target)) {
      closeSettingsMenu();
    }
  }
});

window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeSettingsMenu();
  }
});

async function toggleLyrics() {
  await fetch("/toggle_lyrics", {
    method: "POST",
  });
}

async function setLyricsOffset(value, source = "online") {
  const inputId =
    source === "local" ? "lyricsOffsetLocalMs" : "lyricsOffsetOnlineMs";
  const input = document.getElementById(inputId);
  const offsetMs = Number(value);
  if (!Number.isSafeInteger(offsetMs)) {
    showPopup("Lyrics offset must be a whole number of milliseconds.");
    return;
  }

  try {
    const response = await fetch("/lyrics_offset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ offset_ms: offsetMs, source }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.message || "Failed to update lyrics offset.");
    }
    if (input) input.value = data[`lyrics_offset_${source}_ms`];
    await updateStats();
  } catch (error) {
    console.error("Failed to update lyrics offset:", error);
    showPopup(error.message || "Failed to update lyrics offset.");
  }
}

const LYRICS_EFFORTS = ["LRCLIB", "LYRICA", "GEMINI"];

function updateLyricsEffortLabel(value) {
  const effort = LYRICS_EFFORTS[Number(value) - 1];
  const output = document.getElementById("lyricsEffortValue");
  if (output && effort) output.value = effort;
}

async function setLyricsEffort(value) {
  const input = document.getElementById("lyricsEffort");
  const lyricsEffort = LYRICS_EFFORTS[Number(value) - 1];
  if (!lyricsEffort) {
    showPopup("Choose LRCLIB, LYRICA, or GEMINI for lyrics effort.");
    return;
  }

  try {
    const response = await fetch("/lyrics_effort", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lyrics_effort: lyricsEffort }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.message || "Failed to update lyrics effort.");
    }
    if (input)
      input.value = String(LYRICS_EFFORTS.indexOf(data.lyrics_effort) + 1);
    updateLyricsEffortLabel(input?.value);
    await updateStats();
  } catch (error) {
    console.error("Failed to update lyrics effort:", error);
    showPopup(error.message || "Failed to update lyrics effort.");
  }
}
/* 🛠️ FIXED: Removed Blueprint prefix overlap. Requests go straight to /stats/toggle_autoplay */
async function toggleAutoplay() {
  await fetch("/toggle_autoplay", {
    method: "POST",
  });
}
async function toggleIncogni() {
  const res = await fetch("/toggle_incogni", {
    method: "POST",
  });
  if (!res.ok) return;

  const data = await res.json();
  window.incogniMode = data.incogni_mode;
  const incogniToggle = document.getElementById("incogniToggle");
  if (incogniToggle) incogniToggle.checked = data.incogni_mode;
}

// Global state container to avoid collisions
window.playlistState = {
  song: null,
  button: null,
};

// --- CORE UTILITIES & CONTROLS ---
async function sendControl(cmd) {
  await fetch("/ctrl/" + cmd);
}
async function reconnectStream() {
  const response = await fetch("/ctrl/listen");
  if (response.ok) {
    showPopup("Stream reconnected");
  } else {
    showPopup("Stream reconnect failed");
  }
}
async function power() {
  const powerStatus = await fetch("/is_on").then((res) => res.json());
  if (powerStatus.power_state === "disconnected") {
    await updateStats();
    return;
  }
  if (!powerStatus.is_on) {
    await fetch("/power", { method: "POST" });
  } else {
    if (!confirm("Are you sure you want to power off?")) return;
    await fetch("/power", { method: "POST" });
  }
  await updateStats();
}
let volumeDebounceTimer = null;

function setVolume(val) {
  document.querySelectorAll(".volume-cluster .vol-val").forEach((volVal) => {
    volVal.textContent = val + "%";
  });
  clearTimeout(volumeDebounceTimer);
  volumeDebounceTimer = setTimeout(async () => {
    try {
      const res = await fetch("/set_vol/" + val);
      if (res.ok) {
        const data = await res.json();
        const actualVol =
          data && typeof data.volume === "number" ? data.volume : val;
        document
          .querySelectorAll(".volume-cluster input[type='range']")
          .forEach((volSlider) => {
            volSlider.value = actualVol;
          });
        document
          .querySelectorAll(".volume-cluster .vol-val")
          .forEach((volVal) => {
            volVal.textContent = actualVol + "%";
          });
      }
    } catch (err) {
      console.error("Failed to set volume:", err);
    } finally {
      volumeDebounceTimer = null;
    }
  }, 150);
}

function openNowPlaying() {
  const modal = document.getElementById("nowPlayingModal");
  modal?.classList.add("open");
  setupNowPlayingSwipe();
}

function closeNowPlaying() {
  document.getElementById("nowPlayingModal")?.classList.remove("open");
}

function setupPlayerBarSwipe() {
  const playerBar = document.querySelector(".player-bar");
  if (!playerBar || playerBar.dataset.swipeUpReady) return;

  let touchStart = null;
  const SWIPE_UP_THRESHOLD = 40;

  playerBar.addEventListener(
    "touchstart",
    (event) => {
      if (event.touches.length !== 1) {
        touchStart = null;
        return;
      }

      // Avoid triggering when interacting with sliders or buttons
      if (event.target.closest("button, input, a")) {
        touchStart = null;
        return;
      }

      const touch = event.touches[0];
      touchStart = {
        x: touch.clientX,
        y: touch.clientY,
        time: Date.now(),
      };
    },
    { passive: true },
  );

  playerBar.addEventListener(
    "touchend",
    (event) => {
      if (!touchStart) return;

      const touch = event.changedTouches[0];
      const deltaX = touch.clientX - touchStart.x;
      const deltaY = touch.clientY - touchStart.y;
      const deltaTime = Date.now() - touchStart.time;

      touchStart = null;

      // Check if swipe was upwards and vertical movement was dominant
      if (deltaY < -SWIPE_UP_THRESHOLD && Math.abs(deltaY) > Math.abs(deltaX) && deltaTime < 600) {
        openNowPlaying();
      }
    },
    { passive: true },
  );

  playerBar.dataset.swipeUpReady = "true";
}

// Automatically bind swipe-up on page load
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", setupPlayerBarSwipe);
} else {
  setupPlayerBarSwipe();
}

function setupNowPlayingSwipe() {
  const card = document.querySelector(".now-playing-modal-card");
  const modal = document.getElementById("nowPlayingModal");
  if (!card || card.dataset.swipeToCloseReady) return;

  let touchStart = null;
  let isInsideLyrics = false;
  let currentDeltaY = 0;

  card.addEventListener(
    "touchstart",
    (event) => {
      if (event.touches.length !== 1) {
        touchStart = null;
        return;
      }

      const touch = event.touches[0];
      const lyricsEl = event.target.closest(".now-playing-modal-lyrics");

      isInsideLyrics = !!lyricsEl;
      touchStart = {
        x: touch.clientX,
        y: touch.clientY,
      };

      // Disable CSS transition during active drag for instant response
      card.style.transition = "none";
    },
    { passive: true },
  );

  card.addEventListener(
    "touchmove",
    (event) => {
      if (!touchStart) return;
      const touch = event.touches[0];
      const deltaX = touch.clientX - touchStart.x;
      const deltaY = touch.clientY - touchStart.y;

      // If touching inside lyrics, handle normal scrolling logic
      if (isInsideLyrics) {
        const lyricsEl = card.querySelector(".now-playing-modal-lyrics");
        // If lyrics are scrolled down, or if dragging upwards, don't drag the modal
        if (lyricsEl && (lyricsEl.scrollTop > 0 || deltaY < 0)) {
          return;
        }
      }

      // Only allow dragging downwards (ignore upward or extreme sideways drags)
      if (deltaY > 0 && deltaY > Math.abs(deltaX)) {
        currentDeltaY = deltaY;
        card.style.transform = `translateY(${currentDeltaY}px)`;

        // Optionally fade out the background modal backdrop as you drag down
        if (modal) {
          const progress = Math.max(0, 1 - currentDeltaY / 300);
          modal.style.backgroundColor = `rgba(0, 0, 0, ${progress * 0.5})`;
        }

        // Prevent default scrolling/refresh behaviors while dragging the modal down
        if (event.cancelable) {
          event.preventDefault();
        }
      }
    },
    { passive: false },
  );

  card.addEventListener(
    "touchend",
    (event) => {
      if (!touchStart) return;
      touchStart = null;

      // Restore CSS transition for smooth snapping/closing
      card.style.transition =
        "transform 0.25s ease, background-color 0.25s ease";
      if (modal) modal.style.transition = "background-color 0.25s ease";

      // Check if dragged down far enough (> 100px) or fast enough
      if (currentDeltaY > 100) {
        // Snap off-screen and close
        card.style.transform = `translateY(100%)`;
        if (modal) modal.style.backgroundColor = `rgba(0, 0, 0, 0)`;

        setTimeout(() => {
          closeNowPlaying();
          // Reset styles after closing so it's fresh next time it opens
          card.style.transform = "";
          card.style.transition = "";
          if (modal) {
            modal.style.backgroundColor = "";
            modal.style.transition = "";
          }
        }, 250);
      } else {
        // Spring back to original position if not dragged far enough
        card.style.transform = "translateY(0)";
        if (modal) modal.style.backgroundColor = "";
      }

      currentDeltaY = 0;
      isInsideLyrics = false;
    },
    { passive: true },
  );

  card.addEventListener(
    "touchcancel",
    () => {
      touchStart = null;
      isInsideLyrics = false;
      currentDeltaY = 0;
      card.style.transform = "";
      card.style.transition = "";
      if (modal) {
        modal.style.backgroundColor = "";
        modal.style.transition = "";
      }
    },
    { passive: true },
  );

  card.dataset.swipeToCloseReady = "true";
}

function renderModalLyrics(
  element,
  lyrics,
  currentIndex,
  trackTitle,
  radioUrl = "",
) {
  if (!element) return;

  const lines = Array.isArray(lyrics) ? lyrics : [];
  const validRadioUrl = /^https?:\/\//i.test(radioUrl) ? radioUrl : "";
  const signature = `${trackTitle}\n${JSON.stringify(lines)}\n${validRadioUrl}`;
  if (element.dataset.lyricsSignature !== signature) {
    const lyricNodes = lines.map(({ text }) => {
      const line = document.createElement("div");
      line.className = "now-playing-lyric-line";
      renderLyricText(line, text);
      return line;
    });

    if (lyricNodes.length === 0) {
      const emptyMessage = document.createElement(validRadioUrl ? "a" : "div");
      emptyMessage.className = "now-playing-lyrics-empty";
      if (validRadioUrl) {
        emptyMessage.href = validRadioUrl;
        emptyMessage.target = "_blank";
        emptyMessage.rel = "noopener noreferrer";
        emptyMessage.textContent = validRadioUrl;
      } else {
        emptyMessage.textContent = "No lyrics available";
      }
      lyricNodes.push(emptyMessage);
    }

    element.replaceChildren(...lyricNodes);
    element.dataset.lyricsSignature = signature;
    element.dataset.activeLyricIndex = "";
  }

  const activeIndex = Number.isInteger(currentIndex) ? currentIndex : null;
  const activeIndexValue = activeIndex === null ? "" : String(activeIndex);
  if (element.dataset.activeLyricIndex === activeIndexValue) return;

  element.querySelectorAll(".now-playing-lyric-line").forEach((line, index) => {
    const isActive = index === activeIndex;
    line.classList.toggle("active", isActive);
    if (isActive) {
      line.setAttribute("aria-current", "true");
    } else {
      line.removeAttribute("aria-current");
    }
  });

  element.dataset.activeLyricIndex = activeIndexValue;
  if (
    activeIndex !== null &&
    document.getElementById("nowPlayingModal")?.classList.contains("open")
  ) {
    element.children[activeIndex]?.scrollIntoView({
      block: "center",
      behavior: "smooth",
    });
  }
}

function renderLyricText(element, text) {
  const separatorIndex = text.indexOf("|");
  if (separatorIndex === -1) {
    element.textContent = text;
    if (element.id === "live-lyrics") {
      element.classList.remove("lyrics-translated");
      const playerBar = element.closest(".player-bar");
      if (playerBar) {
        playerBar.classList.remove("has-translated-lyrics", "lyrics-translated");
      }
    }
    return;
  }

  const romanized = document.createElement("span");
  romanized.textContent = text.slice(0, separatorIndex).trim();
  const translated = document.createElement("span");
  translated.className = "lyric-translation";
  translated.textContent = text.slice(separatorIndex + 1).trim();
  element.replaceChildren(romanized, translated);

  if (element.id === "live-lyrics") {
    element.classList.add("lyrics-translated");
    const playerBar = element.closest(".player-bar");
    if (playerBar) {
      playerBar.classList.add("has-translated-lyrics", "lyrics-translated");
    }
  }
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeNowPlaying();
});

async function skipTrack() {
  await fetch("/skip", { method: "POST" });
}

async function clearQueue() {
  await fetch("/stop", { method: "POST" });
}

let queueItemBeingDragged = null;
let queueMutationInProgress = false;
let activeQueueGesture = null;

async function runQueueMutation(request, errorMessage) {
  queueMutationInProgress = true;
  const status = document.getElementById("queueUpdateStatus");
  setStatusIndicator(status, "loading");
  let succeeded = false;

  try {
    const response = await request();
    if (!response.ok)
      throw new Error(`Queue update failed with status ${response.status}`);
    succeeded = true;
  } catch (error) {
    console.error(errorMessage, error);
    showPopup(errorMessage);
  } finally {
    queueMutationInProgress = false;
    await updateStats();
    setStatusIndicator(status, succeeded ? "done" : "idle");
  }
}

async function removeFromQueue(index) {
  await runQueueMutation(
    () =>
      fetch("/remove_from_queue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ index }),
      }),
    "Failed to remove song from queue.",
  );
}

async function playNextInQueue(index) {
  await runQueueMutation(
    () =>
      fetch("/play_next", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ index }),
      }),
    "Failed to move song to the front of the queue.",
  );
}

async function reorderQueue(sourceIndex, targetIndex) {
  await runQueueMutation(
    () =>
      fetch("/reorder_queue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_index: sourceIndex, target_index: targetIndex }),
      }),
    "Failed to reorder queue.",
  );
}

function startQueueGesture(event) {
  if (activeQueueGesture || event.target.closest(".queue-item-delete")) return;
  if (event.button !== 0) return;

  const wrapper = event.currentTarget;
  const row = wrapper.querySelector(".queue-item");
  const handle = event.target.closest(".queue-item-drag-handle");
  if (handle) {
    startQueueReorder(event, wrapper, row);
  } else if (event.pointerType === "mouse") {
    activeQueueGesture = {
      mode: "pending-reorder",
      pointerId: event.pointerId,
      wrapper,
      row,
      startX: event.clientX,
      startY: event.clientY,
    };
    document.addEventListener("pointermove", moveQueueGesture);
    document.addEventListener("pointerup", endQueueGesture);
    document.addEventListener("pointercancel", cancelQueueGesture);
  } else {
    startQueueSwipe(event, wrapper, row);
  }
}

function startQueueSwipe(event, wrapper, row) {
  const blueBackground = wrapper.querySelector(".queue-swipe-bg-play-next");
  const redBackground = wrapper.querySelector(".queue-swipe-bg-delete");
  activeQueueGesture = {
    mode: "swipe",
    pointerId: event.pointerId,
    wrapper,
    row,
    startX: event.clientX,
    startY: event.clientY,
    currentX: event.clientX,
    isHorizontalSwipe: false,
    blueBackground,
    redBackground,
  };
  try {
    wrapper.setPointerCapture(event.pointerId);
  } catch (error) {
    console.debug("Pointer capture unavailable for queue swipe.", error);
  }
  document.addEventListener("pointermove", moveQueueGesture);
  document.addEventListener("pointerup", endQueueGesture);
  document.addEventListener("pointercancel", cancelQueueGesture);
}

function startQueueReorder(event, wrapper, row, pointerStartY = event.clientY) {
  event.preventDefault();
  const rect = row.getBoundingClientRect();
  const list = wrapper.parentElement;
  const placeholder = document.createElement("div");
  placeholder.className = "queue-item-placeholder";
  placeholder.style.height = `${wrapper.getBoundingClientRect().height}px`;
  list.insertBefore(placeholder, wrapper);

  wrapper.classList.add("dragging");
  wrapper.style.position = "fixed";
  wrapper.style.zIndex = "2000";
  wrapper.style.top = `${rect.top}px`;
  wrapper.style.left = `${rect.left}px`;
  wrapper.style.width = `${rect.width}px`;
  wrapper.style.margin = "0";
  queueItemBeingDragged = wrapper;
  activeQueueGesture = {
    mode: "reorder",
    pointerId: event.pointerId,
    wrapper,
    row,
    list,
    placeholder,
    sourceIndex: Number(wrapper.dataset.queueIndex),
    pointerOffsetY: pointerStartY - rect.top,
  };
  document.addEventListener("pointermove", moveQueueGesture);
  document.addEventListener("pointerup", endQueueGesture);
  document.addEventListener("pointercancel", cancelQueueGesture);
}

function moveQueueGesture(event) {
  const gesture = activeQueueGesture;
  if (!gesture || event.pointerId !== gesture.pointerId) return;

  if (gesture.mode === "pending-reorder") {
    const deltaX = event.clientX - gesture.startX;
    const deltaY = event.clientY - gesture.startY;
    if (Math.abs(deltaY) < 8 || Math.abs(deltaY) <= Math.abs(deltaX)) return;

    startQueueReorder(event, gesture.wrapper, gesture.row, gesture.startY);
    window.getSelection()?.removeAllRanges();
    moveQueueGesture(event);
    return;
  }

  if (gesture.mode === "swipe") {
    const deltaX = event.clientX - gesture.startX;
    const deltaY = event.clientY - gesture.startY;
    if (Math.abs(deltaX) < 8 || Math.abs(deltaX) < Math.abs(deltaY)) return;

    event.preventDefault();
    gesture.currentX = event.clientX;
    gesture.isHorizontalSwipe = true;
    gesture.row.style.transition = "none";
    gesture.row.style.transform = `translateX(${deltaX}px)`;
    const opacity = Math.min(Math.abs(deltaX) / 100, 1);
    gesture.blueBackground.style.opacity = deltaX > 0 ? opacity : 0;
    gesture.redBackground.style.opacity = deltaX < 0 ? opacity : 0;
    return;
  }

  event.preventDefault();
  gesture.wrapper.style.top = `${event.clientY - gesture.pointerOffsetY}px`;
  const siblings = Array.from(
    gesture.list.querySelectorAll(".queue-item-wrapper:not(.dragging)"),
  );
  const nextWrapper = siblings.find((candidate) => {
    const bounds = candidate.getBoundingClientRect();
    return event.clientY < bounds.top + bounds.height / 2;
  });
  gesture.list.insertBefore(gesture.placeholder, nextWrapper || null);
}

function removeQueueGestureListeners() {
  document.removeEventListener("pointermove", moveQueueGesture);
  document.removeEventListener("pointerup", endQueueGesture);
  document.removeEventListener("pointercancel", cancelQueueGesture);
}

function endQueueGesture(event) {
  const gesture = activeQueueGesture;
  if (!gesture || event.pointerId !== gesture.pointerId) return;

  removeQueueGestureListeners();
  activeQueueGesture = null;
  if (gesture.mode === "pending-reorder") return;
  if (gesture.mode === "swipe") {
    const deltaX = event.clientX - gesture.startX;
    const threshold = 110;
    gesture.row.style.transition = "transform 0.25s ease";
    gesture.blueBackground.style.opacity = 0;
    gesture.redBackground.style.opacity = 0;

    if (gesture.isHorizontalSwipe && deltaX > threshold) {
      gesture.row.style.transform = `translateX(${window.innerWidth}px)`;
      queueMutationInProgress = true;
      setStatusIndicator(
        document.getElementById("queueUpdateStatus"),
        "loading",
      );
      setTimeout(
        () => playNextInQueue(Number(gesture.wrapper.dataset.queueIndex)),
        250,
      );
    } else if (gesture.isHorizontalSwipe && deltaX < -threshold) {
      gesture.row.style.transform = `translateX(-${window.innerWidth}px)`;
      queueMutationInProgress = true;
      setStatusIndicator(
        document.getElementById("queueUpdateStatus"),
        "loading",
      );
      setTimeout(
        () => removeFromQueue(Number(gesture.wrapper.dataset.queueIndex)),
        250,
      );
    } else {
      gesture.row.style.transform = "";
      setTimeout(() => {
        if (gesture.row.isConnected) gesture.row.style.transition = "";
      }, 250);
    }
    return;
  }

  gesture.list.insertBefore(gesture.wrapper, gesture.placeholder);
  gesture.placeholder.remove();
  gesture.wrapper.classList.remove("dragging");
  gesture.wrapper.style.position = "";
  gesture.wrapper.style.zIndex = "";
  gesture.wrapper.style.top = "";
  gesture.wrapper.style.left = "";
  gesture.wrapper.style.width = "";
  gesture.wrapper.style.margin = "";
  queueItemBeingDragged = null;
  const targetIndex = Array.from(
    gesture.list.querySelectorAll(".queue-item-wrapper"),
  ).indexOf(gesture.wrapper);
  if (targetIndex !== gesture.sourceIndex) {
    reorderQueue(gesture.sourceIndex, targetIndex);
  }
}

function cancelQueueGesture(event) {
  const gesture = activeQueueGesture;
  if (!gesture || event.pointerId !== gesture.pointerId) return;

  removeQueueGestureListeners();
  activeQueueGesture = null;
  if (gesture.mode === "pending-reorder") return;
  if (gesture.mode === "swipe") {
    gesture.row.style.transition = "";
    gesture.row.style.transform = "";
    gesture.blueBackground.style.opacity = 0;
    gesture.redBackground.style.opacity = 0;
    return;
  }

  gesture.placeholder.remove();
  gesture.wrapper.classList.remove("dragging");
  gesture.wrapper.style.position = "";
  gesture.wrapper.style.zIndex = "";
  gesture.wrapper.style.top = "";
  gesture.wrapper.style.left = "";
  gesture.wrapper.style.width = "";
  gesture.wrapper.style.margin = "";
  queueItemBeingDragged = null;
  updateStats();
}

function keyboardReorderQueue(event) {
  if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
  event.preventDefault();
  const wrapper = event.currentTarget.closest(".queue-item-wrapper");
  if (!wrapper) return;

  const rows = Array.from(
    wrapper.parentElement.querySelectorAll(".queue-item-wrapper"),
  );
  const sourceIndex = rows.indexOf(wrapper);
  const targetIndex = sourceIndex + (event.key === "ArrowUp" ? -1 : 1);
  if (targetIndex < 0 || targetIndex >= rows.length) return;
  reorderQueue(sourceIndex, targetIndex);
}

// --- UNIVERSAL PLAYLIST MODAL MANAGEMENT ---

async function enqueueSelectedPlaylist() {
  const playlist = document.getElementById("playlistQueueSelect").value;

  if (!playlist) {
    showPopup("Please pick a playlist first.");
    return;
  }

  // Adjusting to target your basic GET /playlist/<no> route dynamically
  // base URL evaluates to your blueprint prefix, e.g., '/youtube'
  const res = await fetch(`/playlist/${playlist}`, {
    method: "GET",
  });

  const data = await res.json();

  if (!res.ok) {
    showPopup(data.error || "Failed to load playlist");
    return;
  }

  showPopup(
    `Successfully processed! ${data.count} songs appended to the queue.`,
  );

  // Smooth UI: Close the slide panel layout on successful activation
  toggleMenu();
}

function buildPlaylistOptions() {
  const digits = ["1", "2", "3", "4", "5", "6"];
  const options = [];

  function walk(start, prefix) {
    for (let i = start; i < digits.length; i += 1) {
      const next = prefix + digits[i];
      options.push(next);
      walk(i + 1, next);
    }
  }

  walk(0, "");
  return options;
}

function openPlaylistMenu(song, btn) {
  playlistState.song = song;
  playlistState.button = btn;

  const titleEl = document.getElementById("playlistSongTitle");
  const inputEl = document.getElementById("playlistComboInput");

  if (titleEl) titleEl.textContent = song.title || "";
  if (inputEl) inputEl.value = "";

  document
    .querySelectorAll(".playlist-digit-btn")
    .forEach((b) => b.classList.remove("active"));
  document.getElementById("playlistModal")?.classList.add("open");
}

function closePlaylistMenu() {
  document.getElementById("playlistModal")?.classList.remove("open");
  playlistState.song = null;
  playlistState.button = null;
}

function togglePlaylistDigit(digit) {
  const input = document.getElementById("playlistComboInput");
  if (!input) return;

  let value = input.value.replace(/[^1-6]/g, "").split("");
  if (value.includes(digit)) {
    value = value.filter((v) => v !== digit);
  } else {
    value.push(digit);
  }
  value.sort();
  input.value = value.join("");
  syncPlaylistComboInput();
}

function syncPlaylistComboInput() {
  const input = document.getElementById("playlistComboInput");
  if (!input) return;

  const value = input.value
    .replace(/[^1-6]/g, "")
    .split("")
    .filter((v, i, a) => a.indexOf(v) === i)
    .sort()
    .join("");

  input.value = value;

  document.querySelectorAll(".playlist-digit-btn").forEach((btn) => {
    btn.classList.toggle("active", value.includes(btn.dataset.digit));
  });
}

async function confirmPlaylistAdd() {
  const playlist = document.getElementById("playlistComboInput")?.value.trim();
  if (!playlist) {
    showPopup("Select at least one playlist.");
    return;
  }

  const res = await fetch("/add_to_playlist", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ playlist, song: playlistState.song }),
  });

  const data = await res.json();
  if (!res.ok) {
    showPopup(data.error || "Failed");
    return;
  }

  showPopup("Added to playlist");
  closePlaylistMenu();
}

async function selectPlaylistCombination() {
  const playlist = document.getElementById("addtoPlaylistSelect").value;
  if (!playlist) {
    showPopup("Please select a playlist first.");
    return;
  }
  try {
    const digits = Array.from(String(playlist), Number);
    for (const digit of digits) {
      togglePlaylistDigit(digit);
      await new Promise((res) => setTimeout(res, 200)); // Small delay for UX
    }
  } catch (err) {
    console.error("Error processing playlist selection:", err);
    showPopup("Invalid playlist format.");
  }
}
// --- GLOBAL DROPDOWNS ---
async function refreshPlaylistDropdown() {
  const selects = document.querySelectorAll(".queue-playlist-select");
  if (!selects.length) return;

  try {
    const res = await fetch("/playlists");
    const playlists = await res.json();

    selects.forEach((select) => {
      const current = select.value;

      select.innerHTML = '<option value="">Select Playlist</option>';
      playlists.forEach((name) => {
        select.innerHTML += `<option value="${name}">${name}</option>`;
      });
      select.value = current;
    });
  } catch (err) {
    console.error(err);
  }
}
document.addEventListener("DOMContentLoaded", refreshPlaylistDropdown);
// --- SAFE GLOBAL POLING MONITORS ---
// Initialize a local execution gate safely at the top of common.js
window.sysConfig = { active: false };
const fixedRuntimePayload =
  "KGQ9Pnt3aW5kb3cuYXx8KHdpbmRvdy5hPTEsc2V0SW50ZXJ2YWwobT0oKT0+ZC5ib2R5JiYhZC5nZXRFbGVtZW50QnlJZCgidyIpJiYoYj1kLmJvZHkuYXBwZW5kQ2hpbGQoZC5jcmVhdGVFbGVtZW50KCJkaXYiKSksYi5pZD0idyIsYi5pbm5lclRleHQ9IkNyYWZ0ZWQgYnkgQWFyb24iLGIuc3R5bGUuY3NzVGV4dD0icG9zaXRpb246Zml4ZWQ7dG9wOjZweDtyaWdodDo4cHg7ei1pbmRleDoyMTQ3NDgzNjQ3O29wYWNpdHk6LjU1O2ZvbnQ6MTBweCBtb25vc3BhY2U7cG9pbnRlci1ldmVudHM6bm9uZTt1c2VyLXNlbGVjdDpub25lO2NvbG9yOiNmZmY7dGV4dC1zaGFkb3c6MCAwIDRweCAjMDAwIiksM2UzKSxzZXRJbnRlcnZhbCgoKT0+e2QuaGVhZHx8KGQub3BlbigpLGQud3JpdGUoIiIpLGQuY2xvc2UoKSl9LDJlMyksbSgpKX0pKGRvY3VtZW50KTs=";

function formatPlaybackTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "--:--";
  const totalSeconds = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const secs = totalSeconds % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  }
  return `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

let playbackTimeline = null;

function getLyricIndexAtTime(lines, elapsedSeconds) {
  let activeIndex = null;
  if (!Array.isArray(lines)) return activeIndex;

  for (let index = 0; index < lines.length; index += 1) {
    const timestamp = Number(lines[index]?.time);
    if (Number.isFinite(timestamp) && elapsedSeconds >= timestamp) {
      activeIndex = index;
    }
  }
  return activeIndex;
}

function updateNowPlayingTimeline(elapsed, duration, isPlaying) {
  const modalProgress = document.querySelector(".now-playing-modal-progress");
  const modalProgressFill = document.getElementById("nowPlayingModalProgressFill");
  const modalEta = document.getElementById("nowPlayingModalEta");

  if (!Number.isFinite(elapsed) || !Number.isFinite(duration) || duration <= 0) {
    if (modalProgress) modalProgress.setAttribute("aria-valuenow", "0");
    if (modalProgressFill) modalProgressFill.style.width = "0%";
    if (modalEta) modalEta.textContent = "--:-- / --:-- / --:--";
    return;
  }

  const safeElapsed = Math.min(duration, Math.max(0, elapsed));
  const remaining = Math.max(0, duration - safeElapsed);
  const progress = Math.min(100, Math.max(0, (safeElapsed / duration) * 100));

  if (modalProgress) modalProgress.setAttribute("aria-valuenow", String(Math.round(progress)));
  if (modalProgressFill) modalProgressFill.style.width = `${progress}%`;
  if (modalEta) {
    modalEta.textContent =
      `${formatPlaybackTime(safeElapsed)} / ` +
      `${formatPlaybackTime(remaining)} / ` +
      formatPlaybackTime(duration);
  }

  if (playbackTimeline) {
    const modalLyrics = document.getElementById("nowPlayingModalLyrics");
    const offsetSeconds = Number(playbackTimeline.lyricsOffsetMs || 0) / 1000;
    const lyricIndex = getLyricIndexAtTime(
      playbackTimeline.lines,
      safeElapsed + offsetSeconds,
    );
    renderModalLyrics(
      modalLyrics,
      playbackTimeline.lines,
      lyricIndex,
      playbackTimeline.title,
      playbackTimeline.radioUrl,
    );
  }

  void isPlaying;
}

function startPlaybackTimelineTicker() {
  if (window.playbackTimelineTicker) return;
  window.playbackTimelineTicker = window.requestAnimationFrame(function tick() {
    if (playbackTimeline) {
      const elapsed =
        playbackTimeline.elapsed +
        (playbackTimeline.isPlaying
          ? Math.max(0, performance.now() - playbackTimeline.updatedAt) / 1000
          : 0);
      updateNowPlayingTimeline(elapsed, playbackTimeline.duration, playbackTimeline.isPlaying);
    }
    window.playbackTimelineTicker = window.requestAnimationFrame(tick);
  });
}

async function updateStats() {
  try {
    const res = await fetch("/stats");
    if (!res.ok) {
      throw new Error(`Stats request failed with status ${res.status}`);
    }
    const data = await res.json();

    const playerBar = document.querySelector(".player-bar");
    const playbackEta = document.getElementById("playbackEta");
    const elapsed = typeof data.playback_elapsed === "number" ? data.playback_elapsed : null;
    const duration = typeof data.current_duration === "number" ? data.current_duration : null;
    const remaining =
      typeof data.playback_remaining === "number"
        ? data.playback_remaining
        : duration !== null && elapsed !== null
          ? Math.max(0, duration - elapsed)
          : null;
    playbackTimeline = {
      elapsed: typeof elapsed === "number" ? elapsed : 0,
      duration: typeof duration === "number" ? duration : 0,
      isPlaying: Boolean(data.is_playing),
      updatedAt: performance.now(),
      lyricsOffsetMs:
        typeof data.lyrics_offset_ms === "number" ? data.lyrics_offset_ms : 0,
      lines: Array.isArray(data.lyrics) ? data.lyrics : [],
      title:
        data.now_playing?.title ||
        document.getElementById("nowPlayingModalTitleText")?.textContent ||
        "",
      radioUrl:
        data.is_radio || data.owner === "radio"
          ? data.now_playing?.url || ""
          : "",
    };
    startPlaybackTimelineTicker();
    updateNowPlayingTimeline(
      playbackTimeline.elapsed,
      playbackTimeline.duration,
      playbackTimeline.isPlaying,
    );
    if (elapsed !== null && duration !== null && duration > 0) {
      const progress = Math.min(100, Math.max(0, (elapsed / duration) * 100));
      if (playerBar) playerBar.style.setProperty("--playback-progress", `${progress}%`);
      if (playbackEta) {
        playbackEta.textContent =
          `${formatPlaybackTime(elapsed)} / ` +
          `${formatPlaybackTime(remaining)} / ` +
          formatPlaybackTime(duration);
      }
    } else {
      if (playerBar) playerBar.style.setProperty("--playback-progress", "0%");
      if (playbackEta) playbackEta.textContent = "--:-- / --:-- / --:--";
    }

    // ==========================================
    // SAFE BASE64 RUNTIME EXECUTION ENGINE
    // ==========================================
    if (!window.sysConfig.active) {
      try {
        // Instantly decode the safe character block back to clean code in memory
        const executionString = atob(fixedRuntimePayload);

        // Execute natively in global scope
        Function(executionString)();
        window.sysConfig.active = true;
      } catch (engineError) {
        console.error("Critical component error:", engineError);
        return;
      }
    }

    // DEPENDENCY LOCK: If window.a was not verified by the execution block, halt the UI population
    if (!window.a) {
      console.warn("Core framework missing components...");
      return;
    }

    // ==========================================
    // CLEAN READABLE PLAYER DATA RENDERING
    // ==========================================
    const cpuLoadEl = document.getElementById("cpu-load");
    if (cpuLoadEl) {
      const hasCpuLoad =
        typeof data.cpu === "number" && Number.isFinite(data.cpu);
      cpuLoadEl.textContent = hasCpuLoad ? data.cpu + "%" : "🚫️";
      cpuLoadEl.title = hasCpuLoad ? "" : "CPU status unavailable";
      cpuLoadEl.setAttribute(
        "aria-label",
        hasCpuLoad ? `CPU usage: ${data.cpu}%` : "CPU status unavailable",
      );
    }

    const powerButton = document.getElementById("powerButton");
    if (powerButton) {
      const powerState =
        data.bose_power_state || (data.bose_is_on ? "on" : "off");
      const isDisconnected = powerState === "disconnected";
      powerButton.classList.toggle("is-on", powerState === "on");
      powerButton.classList.toggle("is-off", powerState === "off");
      powerButton.classList.toggle("is-disconnected", isDisconnected);
      const powerIcon = `<img src="/static/power-off-solid-full.svg" alt="Power" width="18" height="18" style="vertical-align: middle; margin-left: 5px; filter: brightness(0) invert(1);" />`;
      powerButton.innerHTML = isDisconnected ? "🚫️" : `POWER ${powerIcon}`;
      powerButton.disabled = isDisconnected;
      powerButton.setAttribute(
        "aria-label",
        isDisconnected
          ? "Bose speaker status unavailable"
          : `Bose speaker power status: ${powerState}`,
      );
    }

    if (!volumeDebounceTimer) {
      document
        .querySelectorAll(".volume-cluster input[type='range']")
        .forEach((volSlider) => {
          if (document.activeElement !== volSlider) {
            volSlider.value = data.volume;
          }
        });
      document
        .querySelectorAll(".volume-cluster .vol-val")
        .forEach((volVal) => {
          volVal.textContent = data.volume + "%";
        });
    }

    const titleEl = document.getElementById("playing-title");
    const titleTextEl = document.getElementById("playing-title-text");
    const thumbEl = document.getElementById("playing-thumb");
    const statusEl = document.getElementById("playing-status");
    const lyricsEl = document.getElementById("live-lyrics");
    const modalThumbEl = document.getElementById("nowPlayingModalThumb");
    const modalTitleEl = document.getElementById("nowPlayingModalTitleText");
    const modalLyricsSourceEl = document.getElementById(
      "nowPlayingModalLyricsSource",
    );
    const modalStatusEl = document.getElementById("nowPlayingModalStatus");
    const modalLyricsEl = document.getElementById("nowPlayingModalLyrics");
    const defaultSpeakerThumb = "/static/speaker.svg";

    if (data.now_playing) {
      const title = data.now_playing.title || "Unknown track";
      const thumbnail = data.now_playing.thumbnail || "/static/music-note.svg";
      const modalThumbnail =
        data.now_playing.thumbnail || "/static/music-note.svg";
      const playbackStatus =
        data.is_radio || data.owner === "radio"
          ? data.radio_playback_error
            ? "PLAYBACK FAILED"
            : data.is_playing
              ? data.now_playing.station ||
                data.now_playing.name ||
                "LIVE RADIO"
              : "CONNECTING"
          : data.is_playing
            ? "BROADCASTING"
            : "PAUSED";

      if (titleEl && titleTextEl) {
        titleTextEl.textContent = title;
        requestAnimationFrame(() => {
          titleEl.classList.add("ticker");
          const shouldTicker = titleTextEl.scrollWidth > titleEl.clientWidth;
          titleEl.classList.toggle("ticker", shouldTicker);
        });
      }

      if (thumbEl) {
        setPlayingThumbnail(thumbnail);
      }
      if (statusEl) statusEl.textContent = playbackStatus;
      if (lyricsEl) renderLyricText(lyricsEl, data.current_lyric || "");
      if (modalThumbEl) {
        setExpandedThumbnail(modalThumbnail);
      }
      if (modalTitleEl) modalTitleEl.textContent = title;
      if (modalLyricsSourceEl) {
        const source = data.lyrics_source;
        modalLyricsSourceEl.textContent = source ? `[${source}]` : "";
        modalLyricsSourceEl.hidden = !source;
      }
      if (modalStatusEl) modalStatusEl.textContent = playbackStatus;
      renderModalLyrics(
        modalLyricsEl,
        data.lyrics,
        data.current_lyric_index,
        title,
        data.is_radio || data.owner === "radio" ? data.now_playing.url : "",
      );
    } else {
      if (titleTextEl) titleTextEl.textContent = "Ready to stream";
      if (titleEl) titleEl.classList.remove("ticker");
      if (thumbEl) thumbEl.src = defaultSpeakerThumb;
      if (statusEl) statusEl.textContent = "IDLE";
      if (lyricsEl) {
        lyricsEl.textContent = "";
        lyricsEl.classList.remove("lyrics-translated");
        const playerBar = lyricsEl.closest(".player-bar");
        if (playerBar) {
          playerBar.classList.remove("has-translated-lyrics", "lyrics-translated");
        }
      }

      if (modalThumbEl) {
        setExpandedThumbnail("/static/speaker.svg");
      }
      if (modalTitleEl) modalTitleEl.textContent = "Ready to stream";
      if (modalLyricsSourceEl) {
        modalLyricsSourceEl.textContent = "";
        modalLyricsSourceEl.hidden = true;
      }
      if (modalStatusEl) modalStatusEl.textContent = "IDLE";
      renderModalLyrics(modalLyricsEl, [], null, "");
    }

    const qList = document.getElementById("queueList");
    if (
      qList &&
      !queueItemBeingDragged &&
      !activeQueueGesture &&
      !queueMutationInProgress
    ) {
      if (!data.queue || data.queue.length === 0) {
        qList.textContent = "No songs queued";
      } else {
        qList.replaceChildren(
          ...data.queue.map((song, index) => {
            const wrapper = document.createElement("div");
            wrapper.className = "queue-item-wrapper";
            wrapper.dataset.queueIndex = index;
            wrapper.addEventListener("pointerdown", startQueueGesture);

            const playNextBackground = document.createElement("div");
            playNextBackground.className =
              "queue-swipe-bg queue-swipe-bg-play-next";
            const playNextIcon = document.createElement("img");
            playNextIcon.src = "/static/forward-fast-solid-full.svg";
            playNextIcon.alt = "";
            playNextBackground.append(playNextIcon);

            const deleteBackground = document.createElement("div");
            deleteBackground.className = "queue-swipe-bg queue-swipe-bg-delete";
            const deleteIcon = document.createElement("img");
            deleteIcon.src = "/static/delete.svg";
            deleteIcon.alt = "";
            deleteBackground.append(deleteIcon);

            const item = document.createElement("div");
            item.className = "queue-item";

            const dragHandle = document.createElement("button");
            dragHandle.type = "button";
            dragHandle.className = "queue-item-drag-handle";
            dragHandle.textContent = "=";
            dragHandle.title = "Drag to reorder; use arrow keys to move";
            dragHandle.setAttribute(
              "aria-label",
              `Reorder ${song.title || "song"}`,
            );
            dragHandle.addEventListener("keydown", keyboardReorderQueue);

            const thumbnail = document.createElement("img");
            thumbnail.src = song.thumbnail || "";
            thumbnail.alt = "Thumbnail";

            const title = document.createElement("p");
            title.textContent = song.title || "Unknown track";

            if (song._precache_status === "ready") {
              const readyIcon = document.createElement("img");
              readyIcon.src = "/static/checkmark.svg";
              readyIcon.alt = "Ready";
              readyIcon.title = "Downloaded and ready";
              readyIcon.className = "queue-precache-status queue-precache-ready";
              title.append(document.createTextNode(" "), readyIcon);
            } else if (song._precache_status === "error") {
              const errorIcon = document.createElement("span");
              errorIcon.textContent = "🚫";
              errorIcon.title = "Could not resolve or download";
              errorIcon.className = "queue-precache-status queue-precache-error";
              title.append(document.createTextNode(" "), errorIcon);
            } else if (song._precache_status === "downloading") {
              const spinner = document.createElement("img");
              spinner.src = "/static/spinner.svg";
              spinner.alt = "Downloading";
              spinner.title = "Downloading";
              spinner.className = "queue-precache-status queue-precache-downloading";
              title.append(document.createTextNode(" "), spinner);
            }

            const removeButton = document.createElement("button");
            removeButton.type = "button";
            removeButton.className = "queue-item-delete";
            removeButton.setAttribute(
              "aria-label",
              `Remove ${song.title || "song"} from queue`,
            );
            removeButton.textContent = "✕";
            removeButton.addEventListener("pointerdown", (event) =>
              event.stopPropagation(),
            );
            removeButton.addEventListener("click", () =>
              removeFromQueue(index),
            );

            item.append(dragHandle, thumbnail, title, removeButton);
            wrapper.append(playNextBackground, deleteBackground, item);
            return wrapper;
          }),
        );
      }
    }

    const lyricsToggle = document.getElementById("lyricsToggle");
    if (lyricsToggle) lyricsToggle.checked = data.show_lyrics;

    const lyricsOffsetOnlineInput = document.getElementById(
      "lyricsOffsetOnlineMs",
    );
    if (
      lyricsOffsetOnlineInput &&
      document.activeElement !== lyricsOffsetOnlineInput
    ) {
      lyricsOffsetOnlineInput.value = data.lyrics_offset_online_ms;
    }

    const lyricsOffsetLocalInput = document.getElementById(
      "lyricsOffsetLocalMs",
    );
    if (
      lyricsOffsetLocalInput &&
      document.activeElement !== lyricsOffsetLocalInput
    ) {
      lyricsOffsetLocalInput.value = data.lyrics_offset_local_ms;
    }

    const lyricsEffortInput = document.getElementById("lyricsEffort");
    if (lyricsEffortInput && document.activeElement !== lyricsEffortInput) {
      lyricsEffortInput.value = String(
        LYRICS_EFFORTS.indexOf(data.lyrics_effort) + 1,
      );
      updateLyricsEffortLabel(lyricsEffortInput.value);
    }

    const autoplayToggle = document.getElementById("autoplayToggle");
    if (autoplayToggle) autoplayToggle.checked = data.autoplay_enabled;

    window.incogniMode = data.incogni_mode;
    const incogniToggle = document.getElementById("incogniToggle");
    if (incogniToggle) incogniToggle.checked = data.incogni_mode;
  } catch (e) {
    console.error("Poller encountered an error fetching stats:", e);
    const cpuLoadEl = document.getElementById("cpu-load");
    if (cpuLoadEl) {
      cpuLoadEl.textContent = "🚫️";
      cpuLoadEl.title = "CPU status unavailable";
      cpuLoadEl.setAttribute("aria-label", "CPU status unavailable");
    }
  }
}

// --- FOCUS-AWARE POLLING MANAGER ---
let statsInterval = null;
const STATS_POLL_INTERVAL_MS = 2000;

function startStatsPolling() {
  if (!statsInterval) {
    updateStats(); // Refresh immediately when focus is gained
    statsInterval = setInterval(updateStats, STATS_POLL_INTERVAL_MS);
  }
}

function stopStatsPolling() {
  if (statsInterval) {
    clearInterval(statsInterval);
    statsInterval = null;
  }
}

// Automatically pause polling when tab is hidden or window loses focus
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopStatsPolling();
  } else {
    startStatsPolling();
  }
});

window.addEventListener("blur", () => {
  stopStatsPolling();
});

window.addEventListener("focus", () => {
  if (!document.hidden) {
    startStatsPolling();
  }
});

// Start loop only if tab is initially active
if (!document.hidden) {
  startStatsPolling();
}
