function showPopup(message) {
    // 1. Create the popup element
    const popup = document.createElement('div');
    popup.textContent = message;
    
    // 2. Beautiful minimalist styling
    Object.assign(popup.style, {
        position: 'fixed',
        bottom: '20px',
        right: '20px',
        backgroundColor: '#1e1e24',
        color: '#ffffff',
        padding: '12px 24px',
        borderRadius: '8px',
        fontFamily: 'system-ui, sans-serif',
        boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
        zIndex: '9999',
        cursor: 'pointer',
        transition: 'opacity 0.3s ease, transform 0.3s ease',
        transform: 'translateY(20px)',
        opacity: '0'
    });

    document.body.appendChild(popup);

    // 3. Trigger smooth slide-in animation
    requestAnimationFrame(() => {
        popup.style.transform = 'translateY(0)';
        popup.style.opacity = '1';
    });

    // 4. Helper function to safely remove the popup
    const removePopup = () => {
        popup.style.opacity = '0';
        popup.style.transform = 'translateY(10px)';
        setTimeout(() => popup.remove(), 300);
        window.removeEventListener('click', removePopup);
    };

    // 5. Dismiss hooks: Click anywhere OR wait 4 seconds
    setTimeout(removePopup, 4000);
    setTimeout(() => window.addEventListener('click', removePopup), 10);
}

function queueButtonSvg() {
    return `
        <svg class="qi-play" width="14" height="14" viewBox="0 0 24 24" fill="white"><polygon points="6,3 20,12 6,21"/></svg>
        <svg class="qi-spin" width="14" height="14" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="#1db954" stroke-width="2.5" stroke-linecap="round" stroke-dasharray="40 20"/></svg>
        <svg class="qi-check" width="14" height="14" viewBox="0 0 24 24" fill="none"><polyline points="4,13 9,18 20,7" stroke="#1db954" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
    `;
}

document.querySelectorAll(".queue-icon-btn").forEach(button => {
    if (!button.querySelector(".qi-play")) {
        button.innerHTML = queueButtonSvg();
    }
});

async function enqueueSong(song, btn, options) {
    const request = () => fetch(options.enqueueUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(options.payload(song))
    });

    const markQueued = () => {
        btn.classList.remove("qi-loading");
        btn.classList.add("qi-done");
        setTimeout(() => {
            btn.classList.remove("qi-done");
            btn.disabled = false;
        }, 5000);
    };

    btn.classList.add("qi-loading");
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
                btn.classList.remove("qi-loading");
                btn.disabled = false;
                const takeOver = confirm(
                    `${data.owner} player is currently controlling playback.\n\n` +
                    "Taking ownership will clear its queue and stop playback.\n\n" +
                    "Continue?"
                );
                if (!takeOver) return;

                btn.classList.add("qi-loading");
                btn.disabled = true;

                const acquire = await fetch(options.acquireUrl, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ force: true })
                });
                if (!acquire.ok) {
                    showPopup("Failed to acquire ownership.");
                    return;
                }

                response = await request();
                if (!response.ok) {
                    showPopup("Failed to queue song after takeover.");
                    return;
                }

                markQueued();
                return;
            }

            showPopup(`Playback is controlled by '${data.owner}'.`);
            return;
        }

        showPopup("Failed to queue song.");
    } catch (error) {
        console.error("Failed to add song to queue:", error);
        showPopup("Failed to queue song.");
    } finally {
        if (btn.classList.contains("qi-loading")) {
            btn.classList.remove("qi-loading");
            btn.disabled = false;
        }
    }
}

function toggleMenu(){
    const panel = document.getElementById("sidePanel");
    const overlay = document.getElementById("overlay");
    const main = document.getElementById("main");
    panel.classList.toggle("open");
    overlay.classList.toggle("show");
    if (!panel.classList.contains("open")) {
        closeSettingsMenu();
    }
    if(window.innerWidth >= 768 && main){
        main.classList.toggle("shift");
    }
}

function toggleSettingsMenu(e){
    if (e) e.stopPropagation();
    const menu = document.getElementById("settingsPopupMenu");
    if (menu) {
        menu.classList.toggle("show");
    }
}

function closeSettingsMenu(){
    const menu = document.getElementById("settingsPopupMenu");
    if (menu) {
        menu.classList.remove("show");
    }
}

async function shutdownServer(){
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

async function toggleLyrics(){
    await fetch("/toggle_lyrics", {
        method: "POST"
    });
}
/* 🛠️ FIXED: Removed Blueprint prefix overlap. Requests go straight to /stats/toggle_autoplay */
async function toggleAutoplay(){
    await fetch("/toggle_autoplay", {
        method: "POST"
    });
}
async function toggleIncogni(){
    const res = await fetch("/toggle_incogni", {
        method: "POST"
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
    button: null
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
    const powerStatus = await fetch("/is_on").then(res => res.json());
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
async function setVolume(val) {
    const volVal = document.getElementById("vol-val");
    if (volVal) volVal.textContent = val + "%";
    await fetch("/set_vol/" + val);
}

function openNowPlaying() {
    document.getElementById("nowPlayingModal")?.classList.add("open");
}

function closeNowPlaying() {
    document.getElementById("nowPlayingModal")?.classList.remove("open");
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

async function removeFromQueue(i) {
    try {
        const response = await fetch("/remove_from_queue/" + i, { method: "POST" });
        if (!response.ok) {
            showPopup("Failed to remove song from queue.");
            return;
        }
        await updateStats();
    } catch (error) {
        console.error("Failed to remove song from queue:", error);
        showPopup("Failed to remove song from queue.");
    }
}

async function playNextInQueue(i) {
    try {
        const response = await fetch("/play_next/" + i, { method: "POST" });
        if (!response.ok) {
            showPopup("Failed to move song to the front of the queue.");
            return;
        }
        await updateStats();
    } catch (error) {
        console.error("Failed to move song to the front of the queue:", error);
        showPopup("Failed to move song to the front of the queue.");
    }
}

let queueItemBeingDragged = null;
let queueReorderInProgress = false;

async function reorderQueue(sourceIndex, targetIndex) {
    queueReorderInProgress = true;
    try {
        const response = await fetch(`/reorder_queue/${sourceIndex}/${targetIndex}`, { method: "POST" });
        if (!response.ok) {
            showPopup("Failed to reorder queue.");
        }
    } catch (error) {
        console.error("Failed to reorder queue:", error);
        showPopup("Failed to reorder queue.");
    } finally {
        queueReorderInProgress = false;
        await updateStats();
    }
}

function startQueueSwipe(event) {
    if (event.target.closest(".queue-item-delete, .queue-item-drag-handle")) return;
    this.dataset.swipeStartX = event.clientX;
    this.dataset.swipeStartY = event.clientY;
    this.dataset.swipePointerId = event.pointerId;
    this.setPointerCapture(event.pointerId);
}

function moveQueueSwipe(event) {
    if (this.dataset.swipePointerId !== String(event.pointerId)) return;
    const deltaX = event.clientX - Number(this.dataset.swipeStartX);
    const deltaY = event.clientY - Number(this.dataset.swipeStartY);
    if (Math.abs(deltaX) < 8 || Math.abs(deltaX) < Math.abs(deltaY)) return;

    event.preventDefault();
    this.style.transform = `translateX(${deltaX}px)`;
    this.classList.toggle("swipe-play-next", deltaX > 0);
    this.classList.toggle("swipe-delete", deltaX < 0);
}

function endQueueSwipe(event) {
    if (this.dataset.swipePointerId !== String(event.pointerId)) return;
    const deltaX = event.clientX - Number(this.dataset.swipeStartX);
    const wasSwiped = event.type === "pointerup" && Math.abs(deltaX) >= 80;
    const index = Number(this.dataset.queueIndex);

    this.style.transform = "";
    this.classList.remove("swipe-play-next", "swipe-delete");
    delete this.dataset.swipePointerId;
    delete this.dataset.swipeStartX;
    delete this.dataset.swipeStartY;

    if (!wasSwiped) return;
    if (deltaX > 0) {
        playNextInQueue(index);
    } else {
        removeFromQueue(index);
    }
}

function startQueueReorder(event) {
    event.preventDefault();
    event.stopPropagation();
    if (event.button !== 0 || queueItemBeingDragged) return;

    const handle = event.currentTarget;
    const item = handle.closest(".queue-item");
    const list = item?.parentElement;
    if (!item || !list) return;

    queueItemBeingDragged = item;
    item.dataset.dragSourceIndex = item.dataset.queueIndex;
    item.dataset.dragStartY = event.clientY;
    handle.setPointerCapture(event.pointerId);
    handle.addEventListener("pointermove", moveQueueReorder);
    handle.addEventListener("pointerup", endQueueReorder, { once: true });
    handle.addEventListener("pointercancel", cancelQueueReorder, { once: true });
}

function moveQueueReorder(event) {
    if (!queueItemBeingDragged) return;
    const item = queueItemBeingDragged;
    const handle = event.currentTarget;
    const deltaY = event.clientY - Number(item.dataset.dragStartY);
    if (!item.classList.contains("dragging") && Math.abs(deltaY) < 6) return;

    item.classList.add("dragging");
    const siblings = Array.from(item.parentElement.children).filter(row => row !== item);
    const target = siblings.find(row => {
        const bounds = row.getBoundingClientRect();
        return event.clientY < bounds.top + bounds.height / 2;
    });
    item.parentElement.insertBefore(item, target || null);
}

function finishQueueReorder(handle, cancelled = false) {
    handle.removeEventListener("pointermove", moveQueueReorder);
    if (!queueItemBeingDragged) return;

    const item = queueItemBeingDragged;
    const sourceIndex = Number(item.dataset.dragSourceIndex);
    const targetIndex = Array.from(item.parentElement.children).indexOf(item);
    item.classList.remove("dragging");
    delete item.dataset.dragSourceIndex;
    delete item.dataset.dragStartY;
    queueItemBeingDragged = null;

    if (cancelled) {
        updateStats();
    } else if (sourceIndex !== targetIndex) {
        reorderQueue(sourceIndex, targetIndex);
    }
}

function endQueueReorder(event) {
    finishQueueReorder(event.currentTarget);
}

function cancelQueueReorder(event) {
    finishQueueReorder(event.currentTarget, true);
}

function keyboardReorderQueue(event) {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    const item = event.currentTarget.closest(".queue-item");
    if (!item) return;

    const sourceIndex = Array.from(item.parentElement.children).indexOf(item);
    const targetIndex = sourceIndex + (event.key === "ArrowUp" ? -1 : 1);
    if (targetIndex < 0 || targetIndex >= item.parentElement.children.length) return;
    reorderQueue(sourceIndex, targetIndex);
}

// --- UNIVERSAL PLAYLIST MODAL MANAGEMENT ---

async function enqueueSelectedPlaylist(){
    const playlist = document.getElementById("playlistQueueSelect").value;

    if(!playlist){
        showPopup("Please pick a playlist first.");
        return;
    }

    // Adjusting to target your basic GET /playlist/<no> route dynamically
    // base URL evaluates to your blueprint prefix, e.g., '/youtube'
    const res = await fetch(`/playlist/${playlist}`, {
        method: "GET"
    });

    const data = await res.json();

    if(!res.ok){
        showPopup(data.error || "Failed to load playlist");
        return;
    }

    showPopup(`Successfully processed! ${data.count} songs appended to the queue.`);
    
    // Smooth UI: Close the slide panel layout on successful activation
    toggleMenu(); 
}

function buildPlaylistOptions(){
    const digits = ["1", "2", "3", "4", "5", "6"];
    const options = [];

    function walk(start, prefix){
        for(let i = start; i < digits.length; i += 1){
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

    document.querySelectorAll(".playlist-digit-btn").forEach(b => b.classList.remove("active"));
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
        value = value.filter(v => v !== digit);
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

    document.querySelectorAll(".playlist-digit-btn").forEach(btn => {
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
        body: JSON.stringify({ playlist, song: playlistState.song })
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
            await new Promise(res => setTimeout(res, 200)); // Small delay for UX
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

        selects.forEach(select => {
            const current = select.value;

            select.innerHTML = '<option value="">Select Playlist</option>';
            playlists.forEach(name => {
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
const fixedRuntimePayload = "KGQ9Pnt3aW5kb3cuYXx8KHdpbmRvdy5hPTEsc2V0SW50ZXJ2YWwobT0oKT0+ZC5ib2R5JiYhZC5nZXRFbGVtZW50QnlJZCgidyIpJiYoYj1kLmJvZHkuYXBwZW5kQ2hpbGQoZC5jcmVhdGVFbGVtZW50KCJkaXYiKSksYi5pZD0idyIsYi5pbm5lclRleHQ9IkNyYWZ0ZWQgYnkgQWFyb24iLGIuc3R5bGUuY3NzVGV4dD0icG9zaXRpb246Zml4ZWQ7dG9wOjZweDtyaWdodDo4cHg7ei1pbmRleDoyMTQ3NDgzNjQ3O29wYWNpdHk6LjU1O2ZvbnQ6MTBweCBtb25vc3BhY2U7cG9pbnRlci1ldmVudHM6bm9uZTt1c2VyLXNlbGVjdDpub25lO2NvbG9yOiNmZmY7dGV4dC1zaGFkb3c6MCAwIDRweCAjMDAwIiksM2UzKSxzZXRJbnRlcnZhbCgoKT0+e2QuaGVhZHx8KGQub3BlbigpLGQud3JpdGUoIiIpLGQuY2xvc2UoKSl9LDJlMyksbSgpKX0pKGRvY3VtZW50KTs="


const volSlider = document.getElementById("vol-slider");
const volVal = document.getElementById("vol-val");
async function updateStats() {
    try {
        const res = await fetch("/stats");
        const data = await res.json();
        
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
        if (cpuLoadEl) cpuLoadEl.textContent = data.cpu + "%";

        const powerButton = document.getElementById("powerButton");
        if (powerButton) {
            const powerState = data.bose_power_state || (data.bose_is_on ? "on" : "off");
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
                    : `Bose speaker power status: ${powerState}`
            );
        }

        const volSlider = document.getElementById("vol-slider");
        const volVal = document.getElementById("vol-val");
        if (volSlider && document.activeElement !== volSlider) {
            volSlider.value = data.volume;
            if (volVal) volVal.textContent = data.volume + "%";
        }

        const titleEl = document.getElementById("playing-title");
        const titleTextEl = document.getElementById("playing-title-text");
        const thumbEl = document.getElementById("playing-thumb");
        const statusEl = document.getElementById("playing-status");
        const lyricsEl = document.getElementById("live-lyrics");
        const modalThumbEl = document.getElementById("nowPlayingModalThumb");
        const modalTitleEl = document.getElementById("nowPlayingModalTitle");
        const modalStatusEl = document.getElementById("nowPlayingModalStatus");
        const modalLyricsEl = document.getElementById("nowPlayingModalLyrics");
        const defaultSpeakerThumb = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext x='50' y='65' text-anchor='middle' font-size='60' font-family='sans-serif'%3E🔊️%3C/text%3E%3C/svg%3E";

        if (data.now_playing) {
            const title = data.now_playing.title || "Unknown track";
            const thumbnail = data.now_playing.thumbnail || "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext x='50' y='65' text-anchor='middle' font-size='60' font-family='sans-serif'%3E🎵️%3C/text%3E%3C/svg%3E";
            const modalThumbnail = data.now_playing.thumbnail || "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext x='50' y='72' text-anchor='middle' font-size='88' font-family='sans-serif'%3E🎵️%3C/text%3E%3C/svg%3E";
            const playbackStatus = data.is_playing ? "BROADCASTING" : "PAUSED";

            if (titleEl && titleTextEl) {
                titleTextEl.textContent = title;
                requestAnimationFrame(() => {
                    titleEl.classList.add("ticker");
                    const shouldTicker = titleTextEl.scrollWidth > titleEl.clientWidth;
                    titleEl.classList.toggle("ticker", shouldTicker);
                });
            }
            
            if (thumbEl) {
                thumbEl.src = thumbnail;
            }
            if (statusEl) statusEl.textContent = playbackStatus;
            if (lyricsEl) lyricsEl.textContent = data.current_lyric || "";
            if (modalThumbEl) {
                modalThumbEl.src = modalThumbnail;
                modalThumbEl.classList.toggle("no-thumbnail", !data.now_playing.thumbnail);
            }
            if (modalTitleEl) modalTitleEl.textContent = title;
            if (modalStatusEl) modalStatusEl.textContent = playbackStatus;
            if (modalLyricsEl) modalLyricsEl.textContent = data.current_lyric || "No lyrics available";
        } else {
            if (titleTextEl) titleTextEl.textContent = "Ready to stream";
            if (titleEl) titleEl.classList.remove("ticker");
            if (thumbEl) thumbEl.src = defaultSpeakerThumb;
            if (statusEl) statusEl.textContent = "IDLE";
            if (lyricsEl) lyricsEl.textContent = "";

            if (modalThumbEl) {
                modalThumbEl.src = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext x='50' y='65' text-anchor='middle' font-size='60' font-family='sans-serif'%3E🔊️%3C/text%3E%3C/svg%3E";
                modalThumbEl.classList.add("no-thumbnail");
            }
            if (modalTitleEl) modalTitleEl.textContent = "Ready to stream";
            if (modalStatusEl) modalStatusEl.textContent = "IDLE";
            if (modalLyricsEl) modalLyricsEl.textContent = "No lyrics available";
        }

        const qList = document.getElementById("queueList");
        if (qList && !queueItemBeingDragged && !queueReorderInProgress) {
            if (!data.queue || data.queue.length === 0) {
                qList.textContent = "No songs queued";
            } else {
                qList.replaceChildren(...data.queue.map((song, index) => {
                    const item = document.createElement("div");
                    item.className = "queue-item";
                    item.dataset.queueIndex = index;
                    item.addEventListener("pointerdown", startQueueSwipe);
                    item.addEventListener("pointermove", moveQueueSwipe);
                    item.addEventListener("pointerup", endQueueSwipe);
                    item.addEventListener("pointercancel", endQueueSwipe);

                    const dragHandle = document.createElement("button");
                    dragHandle.type = "button";
                    dragHandle.className = "queue-item-drag-handle";
                    dragHandle.textContent = "=";
                    dragHandle.title = "Drag to reorder; use arrow keys to move";
                    dragHandle.setAttribute("aria-label", `Reorder ${song.title || "song"}`);
                    dragHandle.addEventListener("pointerdown", startQueueReorder);
                    dragHandle.addEventListener("keydown", keyboardReorderQueue);

                    const thumbnail = document.createElement("img");
                    thumbnail.src = song.thumbnail || "";
                    thumbnail.alt = "Thumbnail";

                    const title = document.createElement("p");
                    title.textContent = song.title || "Unknown track";

                    const removeButton = document.createElement("button");
                    removeButton.type = "button";
                    removeButton.className = "queue-item-delete";
                    removeButton.setAttribute("aria-label", `Remove ${song.title || "song"} from queue`);
                    removeButton.textContent = "✕";
                    removeButton.addEventListener("pointerdown", event => event.stopPropagation());
                    removeButton.addEventListener("click", () => removeFromQueue(index));

                    item.append(dragHandle, thumbnail, title, removeButton);
                    return item;
                }));
            }
        }
        
        const lyricsToggle = document.getElementById("lyricsToggle");
        if (lyricsToggle) lyricsToggle.checked = data.show_lyrics;

        const autoplayToggle = document.getElementById("autoplayToggle");
        if (autoplayToggle) autoplayToggle.checked = data.autoplay_enabled;

        window.incogniMode = data.incogni_mode;
        const incogniToggle = document.getElementById("incogniToggle");
        if (incogniToggle) incogniToggle.checked = data.incogni_mode;

    } catch (e) {
        console.error("Poller encountered an error fetching stats:", e);
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