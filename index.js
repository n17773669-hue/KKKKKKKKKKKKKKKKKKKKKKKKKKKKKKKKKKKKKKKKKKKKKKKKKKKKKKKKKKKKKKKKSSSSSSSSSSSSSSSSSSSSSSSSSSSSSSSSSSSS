require('dotenv').config();
const {
    Client,
    GatewayIntentBits,
    REST,
    Routes,
    SlashCommandBuilder,
    PermissionFlagsBits,
    ModalBuilder,
    TextInputBuilder,
    TextInputStyle,
    ActionRowBuilder,
    ButtonBuilder,
    ButtonStyle,
    EmbedBuilder,
    MessageFlags
} = require('discord.js');
const axios = require('axios');
const WebSocket = require('ws');
const fs = require('fs');
const path = require('path');

// ==============================================================================
// CẤU HÌNH HỆ THỐNG & LƯU TRỮ BỀN VỮNG
// ==============================================================================
const API_BASE = 'https://discord.com/api/v9';
const TOKEN_FILE = path.join(__dirname, 'tokens.json');
const FINGERPRINT_FILE = path.join(__dirname, 'user_fingerprints.json');
const SETUP_CONFIG_FILE = path.join(__dirname, 'server_setups.json');
const MAX_CONCURRENT_QUESTS = 5;

const DEFAULT_THUMBNAIL = process.env.THUMBNAIL_URL || 'https://cdn.discordapp.com/emojis/1571114469130633316.png';

const PC_GAMES_110 = [
    "Roblox", "Geometry Dash", "Grand Theft Auto V", "Minecraft", "League of Legends",
    "Cyberpunk 2077", "Valorant", "Counter-Strike 2", "Dota 2", "Apex Legends",
    "Fortnite", "Rocket League", "Terraria", "Stardew Valley", "Among Us",
    "Genshin Impact", "Honkai: Star Rail", "Elden Ring", "Baldur's Gate 3", "Palworld",
    "Monster Hunter: World", "The Witcher 3: Wild Hunt", "Red Dead Redemption 2", "Forza Horizon 5", "Call of Duty: Warzone",
    "Overwatch 2", "Dead Cells", "Hades", "Hollow Knight", "Cuphead",
    "Rust", "ARK: Survival Evolved", "Subnautica", "The Forest", "Sons Of The Forest",
    "Phasmophobia", "Lethal Company", "Content Warning", "Fall Guys", "Team Fortress 2",
    "Left 4 Dead 2", "Portal 2", "Half-Life 2", "Garry's Mod", "Euro Truck Simulator 2",
    "American Truck Simulator", "Cities: Skylines", "Civilization VI", "Hearts of Iron IV", "Stellaris",
    "Europa Universalis IV", "Crusader Kings III", "Factorio", "Satisfactory", "RimWorld",
    "Project Zomboid", "Don't Starve Together", "Valheim", "V Rising", "Enshrouded",
    "Deep Rock Galactic", "Helldivers 2", "Warframe", "Destiny 2", "Path of Exile",
    "Diablo IV", "Lost Ark", "Black Desert", "Final Fantasy XIV", "World of Warcraft",
    "osu!", "Muse Dash", "A Dance of Fire and Ice", "Beat Saber", "Friday Night Funkin'",
    "Sekiro: Shadows Die Twice", "Dark Souls III", "NieR:Automata", "Devil May Cry 5", "Resident Evil 4",
    "Resident Evil Village", "Silent Hill 2", "Dead by Daylight", "Dying Light 2", "Outlast",
    "God of War", "Marvel's Spider-Man Remastered", "Ghost of Tsushima", "Horizon Zero Dawn", "The Last of Us Part I",
    "Hogwarts Legacy", "Starfield", "No Man's Sky", "Sea of Thieves", "Grounded",
    "It Takes Two", "A Way Out", "Portal", "Undertale", "Deltarune",
    "OMORI", "Celeste", "Ori and the Will of the Wisps", "Little Nightmares II", "Limbo",
    "Inside", "Stray", "Sifu", "Hades II", "Manor Lords"
];

const SUPPORTED_TASKS = [
    "WATCH_VIDEO", "PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP",
    "PLAY_ACTIVITY", "WATCH_VIDEO_ON_MOBILE"
];

// 11 Profiles thiết bị chuẩn (Sẽ khóa cố định 1 profile cho mỗi User để chống Reset Token)
const DEVICE_PROFILES = [
    {
        os: "Windows", os_version: "10.0.26100", os_arch: "x64", app_arch: "x64",
        browser: "Discord Client", client_version: "1.0.9175", browser_version: "32.2.7", native_build_number: 59498,
        ua: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9175 Chrome/128.0.6613.186 Electron/32.2.7 Safari/537.36"
    },
    {
        os: "Windows", os_version: "10.0.22631", os_arch: "x64", app_arch: "x64",
        browser: "Discord Client", client_version: "1.0.9168", browser_version: "32.2.5", native_build_number: 59210,
        ua: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9168 Chrome/128.0.6613.162 Electron/32.2.5 Safari/537.36"
    },
    {
        os: "Windows", os_version: "10", os_arch: "x64", app_arch: "x64",
        browser: "Chrome", client_version: "130.0.0.0", browser_version: "130.0.0.0", native_build_number: 59498,
        ua: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    },
    {
        os: "Mac OS X", os_version: "15.1.0", os_arch: "arm64", app_arch: "arm64",
        browser: "Discord Client", client_version: "1.0.9175", browser_version: "32.2.7", native_build_number: 59498,
        ua: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9175 Chrome/128.0.6613.186 Electron/32.2.7 Safari/537.36"
    },
    {
        os: "Linux", os_version: "6.8.0-45-generic", os_arch: "x64", app_arch: "x64",
        browser: "Discord Client", client_version: "0.0.72", browser_version: "32.2.7", native_build_number: 59498,
        ua: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) discord/0.0.72 Chrome/128.0.6613.186 Electron/32.2.7 Safari/537.36"
    }
];

const HYPESQUAD_HOUSES = {
    1: { name: "HypeSquad Bravery", emoji: "<:hypesquad_2:1571114614975107144>", desc: "Dũng cảm & Nhiệt huyết" },
    2: { name: "HypeSquad Brilliance", emoji: "<:hypesquad_1:1571114521601245224>", desc: "Sáng suốt & Kỷ luật" },
    3: { name: "HypeSquad Balance", emoji: "<:hypesquad:1571114469130633316>", desc: "Cân bằng & Hài hòa" }
};

const activeQuests = new Map();
const activeGameSessions = new Map();
let cachedBuildNumber = 504649;

// ==============================================================================
// HÀM TIỆN ÍCH JSON & ANTI-BAN FINGERPRINT
// ==============================================================================
function readJson(filePath, fallback = {}) {
    try {
        if (fs.existsSync(filePath)) {
            return JSON.parse(fs.readFileSync(filePath, 'utf8'));
        }
    } catch (e) {}
    return fallback;
}

function writeJson(filePath, data) {
    fs.writeFileSync(filePath, JSON.stringify(data, null, 4), 'utf8');
}

function getSavedToken(userId) {
    const tokens = readJson(TOKEN_FILE);
    return tokens[String(userId)] || null;
}

function saveToken(userId, token) {
    const tokens = readJson(TOKEN_FILE);
    tokens[String(userId)] = token.trim();
    writeJson(TOKEN_FILE, tokens);
}

function removeToken(userId) {
    const tokens = readJson(TOKEN_FILE);
    if (tokens[String(userId)]) {
        delete tokens[String(userId)];
        writeJson(TOKEN_FILE, tokens);
    }
}

// Khóa cố định 1 thiết bị cho mỗi User để Discord không nghi ngờ đổi máy -> Chống Reset Token
function getUserDeviceProfile(userId) {
    const fps = readJson(FINGERPRINT_FILE);
    const uid = String(userId);
    if (fps[uid] !== undefined && DEVICE_PROFILES[fps[uid]]) {
        return DEVICE_PROFILES[fps[uid]];
    }
    const idx = Math.floor(Math.random() * DEVICE_PROFILES.length);
    fps[uid] = idx;
    writeJson(FINGERPRINT_FILE, fps);
    return DEVICE_PROFILES[idx];
}

async function fetchLatestBuildNumber() {
    try {
        const ua = DEVICE_PROFILES[0].ua;
        const res = await axios.get('https://discord.com/app', { headers: { 'User-Agent': ua }, timeout: 10000 });
        const matches = [...res.data.matchAll(/\/assets\/([a-f0-9]+)\.js/g)].map(m => m[1]);
        for (const hash of matches.slice(-5)) {
            try {
                const jsRes = await axios.get(`https://discord.com/assets/${hash}.js`, { headers: { 'User-Agent': ua }, timeout: 10000 });
                const m = jsRes.data.match(/buildNumber["\s:]+["\s]*(\d{5,7})/);
                if (m) {
                    cachedBuildNumber = parseInt(m[1], 10);
                    console.log(`[Anti-Ban] Đã đồng bộ Discord Build Number mới nhất: ${cachedBuildNumber}`);
                    return cachedBuildNumber;
                }
            } catch (e) {}
        }
    } catch (e) {}
    return cachedBuildNumber;
}

function makeSuperProperties(profile) {
    const obj = {
        os: profile.os,
        browser: profile.browser,
        release_channel: "stable",
        client_version: profile.client_version,
        os_version: profile.os_version,
        os_arch: profile.os_arch,
        app_arch: profile.app_arch,
        system_locale: "vi-VN",
        browser_user_agent: profile.ua,
        browser_version: profile.browser_version,
        client_build_number: cachedBuildNumber || profile.native_build_number,
        native_build_number: profile.native_build_number,
        client_event_source: null
    };
    return Buffer.from(JSON.stringify(obj)).toString('base64');
}

class DiscordUserAPI {
    constructor(token, userId) {
        this.token = token;
        this.profile = getUserDeviceProfile(userId);
        this.client = axios.create({
            baseURL: API_BASE,
            timeout: 15000,
            headers: {
                "Authorization": token,
                "Content-Type": "application/json",
                "Accept": "*/*",
                "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
                "User-Agent": this.profile.ua,
                "X-Super-Properties": makeSuperProperties(this.profile),
                "X-Discord-Locale": "vi",
                "X-Discord-Timezone": "Asia/Ho_Chi_Minh",
                "Origin": "https://discord.com",
                "Referer": "https://discord.com/channels/@me"
            },
            validateStatus: () => true
        });
    }

    async get(url) { return this.client.get(url); }
    async post(url, data = {}) { return this.client.post(url, data); }
}

const sleep = (ms) => new Promise(res => setTimeout(res, ms));
const randRange = (min, max) => Math.random() * (max - min) + min;

// ==============================================================================
// MODULE 2: QUEST PARSERS
// ==============================================================================
function getTaskConfig(q) {
    const cfg = q?.config || {};
    return cfg.taskConfig || cfg.task_config || cfg.taskConfigV2 || cfg.task_config_v2 || null;
}

function getQuestName(q) {
    const cfg = q?.config || {};
    const msgs = cfg.messages || {};
    return (msgs.questName || msgs.quest_name || msgs.gameTitle || msgs.game_title || cfg.application?.name || `Quest#${q.id}`).trim();
}

function isCompletable(q) {
    const expires = q?.config?.expiresAt || q?.config?.expires_at;
    if (expires && new Date(expires) <= new Date()) return false;
    const tc = getTaskConfig(q);
    if (!tc || !tc.tasks) return false;
    return SUPPORTED_TASKS.some(t => tc.tasks[t] != null);
}

function isEnrolled(q) {
    const us = q?.userStatus || q?.user_status || {};
    return Boolean(us.enrolledAt || us.enrolled_at);
}

function isCompleted(q) {
    const us = q?.userStatus || q?.user_status || {};
    return Boolean(us.completedAt || us.completed_at);
}

function getTaskType(q) {
    const tc = getTaskConfig(q);
    if (!tc || !tc.tasks) return null;
    return SUPPORTED_TASKS.find(t => tc.tasks[t] != null) || null;
}

function getSecondsNeeded(q) {
    const tc = getTaskConfig(q);
    const t = getTaskType(q);
    return (tc && t && tc.tasks[t]?.target) ? tc.tasks[t].target : 0;
}

function getSecondsDone(q) {
    const t = getTaskType(q);
    const us = q?.userStatus || q?.user_status || {};
    return (t && us.progress?.[t]?.value) ? us.progress[t].value : 0;
}

function parseQuestOrbs(q) {
    const cfg = q?.config || {};
    const rwCfg = cfg.rewardsConfig || cfg.rewards_config || {};
    const rewards = rwCfg.rewards || cfg.rewards || [];
    for (const rw of rewards) {
        if (rw.orb_quantity) return rw.orb_quantity;
        if (rw.amount && [0, "ORBS", "ORB"].includes(rw.type)) return rw.amount;
        const rName = String(rw.messages?.name || rw.messages?.title || "");
        const nums = rName.match(/\d+/);
        if (nums && (rName.toLowerCase().includes("orb") || rw.type === 0)) return parseInt(nums[0], 10);
    }
    const t = getTaskType(q);
    return ["WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"].includes(t) ? 200 : 700;
}

// ==============================================================================
// MODULE 3: BADGE & GAME FARMER (WEBSOCKET ANTI-DISCONNECT & AUTO-RESUME)
// ==============================================================================
class BadgeFarmSession {
    constructor(userId, token, maxGames, targetHours) {
        this.userId = userId;
        this.token = token;
        this.profile = getUserDeviceProfile(userId);
        this.maxGames = Math.max(1, Math.min(maxGames, 100, PC_GAMES_110.length));
        this.targetHours = Math.max(1, Math.min(targetHours, 5000));
        this.startTime = Date.now();
        this.isRunning = true;
        this.isPaused = false;
        this.currentGame = "Đang khởi tạo...";
        this.gamesRotated = 0;
        this.pool = this._shuffle([...PC_GAMES_110.slice(0, this.maxGames)]);
        this.ws = null;
        this._hbTimer = null;
        this._rotTimer = null;
        this._mySid = null;
        this._seq = null;

        if (this.maxGames >= 60) {
            this.minInterval = 150000; this.maxInterval = 270000; this.speedLabel = "⚡ Nhanh (2.5 - 4.5p/game)";
        } else if (this.maxGames >= 20) {
            this.minInterval = 300000; this.maxInterval = 480000; this.speedLabel = "⚖️ Vừa phải (5 - 8p/game)";
        } else {
            this.minInterval = 600000; this.maxInterval = 900000; this.speedLabel = "🐢 Chậm & Sâu (10 - 15p/game)";
        }
    }

    _shuffle(arr) {
        for (let i = arr.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [arr[i], arr[j]] = [arr[j], arr[i]];
        }
        return arr;
    }

    nextGame() {
        if (this.pool.length === 0) {
            this.pool = this._shuffle([...PC_GAMES_110.slice(0, this.maxGames)]);
        }
        this.currentGame = this.pool.pop();
        this.gamesRotated += 1;
        return this.currentGame;
    }

    sendPresence(game) {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
        const activities = game ? [{ name: game, type: 0, timestamps: { start: Date.now() } }] : [];
        const payload = {
            op: 3,
            d: {
                since: game ? null : Date.now(),
                activities,
                status: "online",
                afk: false
            }
        };
        try { this.ws.send(JSON.stringify(payload)); } catch (e) {}
    }

    start() {
        if (!this.isRunning) return;
        if ((Date.now() - this.startTime) >= this.targetHours * 3600 * 1000) {
            this.stop();
            return;
        }

        const ws = new WebSocket("wss://gateway.discord.gg/?v=9&encoding=json");
        this.ws = ws;

        ws.on('message', (raw) => {
            if (!this.isRunning) return;
            let data;
            try { data = JSON.parse(raw); } catch (e) { return; }
            if (data.s) this._seq = data.s;

            if (data.op === 10) {
                const hbInterval = data.d.heartbeat_interval;
                // Identify với Device Fingerprint cố định
                ws.send(JSON.stringify({
                    op: 2,
                    d: {
                        token: this.token,
                        properties: {
                            os: this.profile.os,
                            browser: this.profile.browser,
                            device: ""
                        },
                        presence: { status: "online", activities: [], afk: false }
                    }
                }));

                clearInterval(this._hbTimer);
                this._hbTimer = setInterval(() => {
                    if (ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify({ op: 1, d: this._seq }));
                    }
                }, hbInterval);

                clearInterval(this._rotTimer);
                const scheduleNextRotation = () => {
                    if (!this.isRunning) return;
                    const waitMs = Math.floor(randRange(this.minInterval, this.maxInterval));
                    this._rotTimer = setTimeout(() => {
                        if (this.isRunning && !this.isPaused) {
                            this.sendPresence(this.nextGame());
                        }
                        scheduleNextRotation();
                    }, waitMs);
                };
                scheduleNextRotation();
            } else if (data.op === 7 || data.op === 9) {
                // Gateway yêu cầu Reconnect -> Đóng sạch để tự nối lại an toàn, không spam
                ws.close();
            } else if (data.t === "READY") {
                this._mySid = data.d?.session_id;
                if (!this.isPaused) {
                    this.sendPresence(this.nextGame());
                }
            } else if (data.t === "SESSIONS_REPLACE") {
                const sessions = data.d || [];
                const realActive = sessions.some(s => ["online", "idle", "dnd"].includes(s.status) && s.session_id !== this._mySid);
                if (realActive && !this.isPaused) {
                    this.isPaused = true;
                    this.sendPresence(null);
                } else if (!realActive && this.isPaused) {
                    this.isPaused = false;
                    this.sendPresence(this.currentGame);
                }
            }
        });

        ws.on('close', (code) => {
            clearInterval(this._hbTimer);
            clearTimeout(this._rotTimer);
            if (code === 4004) {
                // Token bị thu hồi thật -> Dừng hẳn để tránh spam
                removeToken(this.userId);
                this.isRunning = false;
                activeGameSessions.delete(this.userId);
                return;
            }
            if (this.isRunning) {
                // Chờ ngẫu nhiên 8-15 giây trước khi nối lại chống bị Discord flag
                setTimeout(() => this.start(), Math.floor(randRange(8000, 15000)));
            }
        });

        ws.on('error', () => {});
    }

    stop() {
        this.isRunning = false;
        clearInterval(this._hbTimer);
        clearTimeout(this._rotTimer);
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            try { this.ws.close(1000); } catch (e) {}
        }
    }
}

// ==============================================================================
// MODULE 4: AUTO-QUEST ENGINE (CHẠY SONG SONG & CHỐNG RATE LIMIT)
// ==============================================================================
function createQuestStatusEmbed(userId) {
    const data = activeQuests.get(userId);
    if (!data) {
        return new EmbedBuilder()
            .setTitle("🛡️ TIẾN TRÌNH AUTO-QUEST")
            .setDescription("Hiện tại bạn **không có tiến trình cày Quest** nào đang chạy.")
            .setColor(0x2B2D31);
    }

    const quests = data.quests;
    const total = quests.length;
    const completed = quests.filter(q => q.done_seconds >= q.duration).length;
    const avgProgress = total > 0
        ? Math.floor(quests.reduce((acc, q) => acc + (Math.min(q.done_seconds, q.duration) / Math.max(q.duration, 1)) * 100, 0) / total)
        : 0;
    const bar = "█".repeat(Math.floor(avgProgress / 10)) + "░".repeat(10 - Math.floor(avgProgress / 10));

    const embed = new EmbedBuilder()
        .setTitle("🚀 TIẾN TRÌNH AUTO-QUEST — ĐANG CÀY NGẦM")
        .setColor(0x2ECC71)
        .addFields(
            { name: "👤 Tài khoản", value: `**${data.userName}** • 🔮 Tổng dự kiến: **${data.totalOrbs} Orbs**`, inline: false },
            { name: "📊 Tổng quan tiến độ", value: `\`[${bar}]\` **${avgProgress}%** (${completed}/${total} nhiệm vụ hoàn tất)`, inline: false }
        )
        .setFooter({ text: `Đồng bộ lúc ${new Date().toLocaleTimeString('vi-VN')} • Anti-Ban Fixed Fingerprint` });

    quests.forEach((q, idx) => {
        const icon = String(q.type).includes("VIDEO") ? "🎬" : "🎮";
        const dur = Math.max(q.duration, 1);
        const done = Math.min(q.done_seconds, dur);
        const pct = Math.min(100, Math.floor((done / dur) * 100));
        const filled = Math.floor(pct / 20);
        const qBar = "█".repeat(filled) + "░".repeat(5 - filled);

        const doneM = Math.floor(done / 60), doneS = Math.floor(done % 60);
        const totM = Math.floor(dur / 60), totS = Math.floor(dur % 60);
        const statusTag = done >= dur ? "✅ Xong" : "⏳ Cày";

        embed.addFields({
            name: `#${idx + 1} ${q.title.slice(0, 24)} (${statusTag})`,
            value: `${icon} \`${q.type}\` • 🔮 **${q.orb} Orbs**\n\`[${qBar}]\` **${pct}%** (\`${doneM}p${String(doneS).padStart(2, '0')}s/${totM}p${String(totS).padStart(2, '0')}s\`)`,
            inline: true
        });
    });

    return embed;
}

function buildQuestControlRow(userId) {
    return new ActionRowBuilder().addComponents(
        new ButtonBuilder()
            .setCustomId(`qctrl:refresh:${userId}`)
            .setLabel("Làm mới tiến trình")
            .setEmoji("🔄")
            .setStyle(ButtonStyle.Secondary),
        new ButtonBuilder()
            .setCustomId(`qctrl:cancel:${userId}`)
            .setLabel("Hủy cày Quest")
            .setEmoji("🛑")
            .setStyle(ButtonStyle.Danger)
    );
}

async function runSingleQuestAsync(pQuest, api, userId) {
    const qid = pQuest.id;
    const taskType = pQuest.type;
    const secondsNeeded = pQuest.duration;
    let secondsDone = pQuest.done_seconds;

    if (["WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"].includes(taskType)) {
        const speed = randRange(5.5, 7.5);
        while (secondsDone < secondsNeeded && activeQuests.has(userId)) {
            const timestamp = secondsDone + speed;
            const r = await api.post(`/quests/${qid}/video-progress`, {
                timestamp: Math.min(secondsNeeded, timestamp + randRange(0.1, 0.9))
            });

            if (r.status === 401) {
                removeToken(userId);
                activeQuests.delete(userId);
                return;
            } else if (r.status === 200) {
                if (r.data?.completed_at) {
                    pQuest.done_seconds = secondsNeeded;
                    return;
                }
                secondsDone = Math.min(secondsNeeded, timestamp);
                pQuest.done_seconds = secondsDone;
            } else if (r.status === 429) {
                const retry = r.data?.retry_after || 5;
                await sleep((retry + randRange(2, 4)) * 1000);
                continue;
            }
            if (timestamp >= secondsNeeded) break;
            await sleep(randRange(900, 1400));
        }
        if (activeQuests.has(userId)) {
            await api.post(`/quests/${qid}/video-progress`, { timestamp: secondsNeeded });
        }
    } else if (["PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP", "PLAY_ACTIVITY"].includes(taskType)) {
        const pid = Math.floor(randRange(1000, 30000));
        const streamKey = taskType === "PLAY_ACTIVITY" ? "call:0:1" : `call:0:${pid}`;

        while (secondsDone < secondsNeeded && activeQuests.has(userId)) {
            const r = await api.post(`/quests/${qid}/heartbeat`, { stream_key: streamKey, terminal: false });
            if (r.status === 401) {
                removeToken(userId);
                activeQuests.delete(userId);
                return;
            } else if (r.status === 200) {
                const prog = r.data?.progress;
                if (prog && prog[taskType]) {
                    secondsDone = prog[taskType].value ?? secondsDone;
                }
                pQuest.done_seconds = secondsDone;
                if (r.data?.completed_at || secondsDone >= secondsNeeded) {
                    pQuest.done_seconds = secondsNeeded;
                    return;
                }
            } else if (r.status === 429) {
                const retry = r.data?.retry_after || 10;
                await sleep((retry + randRange(3, 5)) * 1000);
                continue;
            }
            await sleep(randRange(18000, 24000));
        }
        if (activeQuests.has(userId)) {
            await api.post(`/quests/${qid}/heartbeat`, { stream_key: streamKey, terminal: true });
        }
    }
    pQuest.done_seconds = secondsNeeded;
}

async function runQuestFarmingBackground(client, userId) {
    const data = activeQuests.get(userId);
    if (!data) return;

    while (activeQuests.has(userId)) {
        const remaining = data.quests.filter(q => q.done_seconds < q.duration);
        if (remaining.length === 0) break;

        const batch = remaining.slice(0, MAX_CONCURRENT_QUESTS);
        await Promise.allSettled(batch.map(q => runSingleQuestAsync(q, data.api, userId)));
        await sleep(randRange(2000, 4000));
    }

    if (activeQuests.has(userId)) {
        try {
            const user = await client.users.fetch(userId);
            await user.send(`🎉 **Chúc mừng ${data.userName}!** Đã cày hoàn thành toàn bộ nhiệm vụ Quest an toàn.`);
        } catch (e) {}
        activeQuests.delete(userId);
    }
}

async function startOrShowAutoQuest(interaction, userToken) {
    const userId = interaction.user.id;

    if (activeQuests.has(userId)) {
        const payload = {
            content: "⚠️ **Bạn đang có một phiên cày Quest chạy ngầm!** Dưới đây là tiến trình hiện tại:",
            embeds: [createQuestStatusEmbed(userId)],
            components: [buildQuestControlRow(userId)],
            ephemeral: true
        };
        return interaction.deferred || interaction.replied
            ? interaction.followUp(payload)
            : interaction.reply(payload);
    }

    if (!interaction.deferred && !interaction.replied) {
        await interaction.deferReply({ ephemeral: true });
    }

    const api = new DiscordUserAPI(userToken, userId);
    const meRes = await api.get('/users/@me');
    if (meRes.status !== 200) {
        removeToken(userId);
        return interaction.followUp({ content: "❌ **Token đã hết hạn hoặc không hợp lệ!** Vui lòng cập nhật lại Token.", ephemeral: true });
    }

    const displayName = meRes.data.global_name || meRes.data.username;
    let qRes = await api.get('/quests/@me');
    if (qRes.status !== 200) {
        return interaction.followUp({ content: "❌ Không thể tải danh sách Quest lúc này.", ephemeral: true });
    }

    let rawQuests = Array.isArray(qRes.data) ? qRes.data : (qRes.data.quests || []);
    const unaccepted = rawQuests.filter(q => !isEnrolled(q) && !isCompleted(q) && isCompletable(q));

    for (const q of unaccepted) {
        await api.post(`/quests/${q.id}/enroll`, {
            location: 11,
            is_targeted: false,
            metadata_raw: null,
            metadata_sealed: null
        });
        await sleep(randRange(800, 1400));
    }

    if (unaccepted.length > 0) {
        qRes = await api.get('/quests/@me');
        if (qRes.status === 200) {
            rawQuests = Array.isArray(qRes.data) ? qRes.data : (qRes.data.quests || []);
        }
    }

    const actionable = rawQuests.filter(q => isEnrolled(q) && !isCompleted(q) && isCompletable(q));
    if (actionable.length === 0) {
        return interaction.followUp({ content: "⚠️ **Tài khoản hiện không có Quest khả dụng nào hoặc đã làm xong hết!**", ephemeral: true });
    }

    const parsedQuests = actionable.map(q => ({
        id: q.id,
        title: getQuestName(q),
        type: getTaskType(q),
        orb: parseQuestOrbs(q),
        duration: getSecondsNeeded(q),
        done_seconds: getSecondsDone(q)
    }));

    saveToken(userId, userToken);
    activeQuests.set(userId, {
        userName: displayName,
        token: userToken,
        api,
        quests: parsedQuests,
        totalOrbs: parsedQuests.reduce((s, q) => s + q.orb, 0)
    });

    await interaction.followUp({
        content: `🚀 **Đã nhận ${parsedQuests.length} Quest(s) và bắt đầu cày ngầm!**`,
        embeds: [createQuestStatusEmbed(userId)],
        components: [buildQuestControlRow(userId)],
        ephemeral: true
    });

    runQuestFarmingBackground(interaction.client, userId);
}

// ==============================================================================
// MODULE 5: GIAO DIỆN COMPONENTS V2 CAO CẤP (SECTION + THUMBNAIL + BUTTONS TRONG KHUNG)
// ==============================================================================
const SETUP_PRESETS = {
    all: {
        label: "🌟 Tất Cả Chức Năng (Full Menu)",
        features: ["autoquest", "progress", "cancel_quest", "badge", "hypesquad", "token"],
        lines: [
            "**1. AutoQuest** — Tự động nhận & cày Discord Quest",
            "**2. Tiến Trình** — Theo dõi phần trăm Quest real-time",
            "**3. Hủy Quest** — Dừng ngay phiên cày Quest đang chạy",
            "**4. Badge Farmer** — Farm badge Game Time / 100 Games",
            "**5. HypeSquad** — Đổi nhà <:hypesquad_2:1571114614975107144> <:hypesquad_1:1571114521601245224> <:hypesquad:1571114469130633316> tức thì"
        ]
    },
    quest_only: {
        label: "🤖 Chuyên Cày Quest (AutoQuest + Tiến Trình + Hủy + Token)",
        features: ["autoquest", "progress", "cancel_quest", "token"],
        lines: [
            "**1. AutoQuest** — Tự động nhận & cày Discord Quest",
            "**2. Tiến Trình** — Xem tiến độ & thời gian còn lại",
            "**3. Hủy Quest** — Hủy tiến trình đang cày tức thì",
            "**4. Token** — Lưu hoặc cập nhật User Token bảo mật"
        ]
    },
    quest_badge: {
        label: "🎮 Quest & Badge Farmer (AutoQuest + Tiến Trình + Hủy + Badge)",
        features: ["autoquest", "progress", "cancel_quest", "badge"],
        lines: [
            "**1. AutoQuest** — Tự động nhận & cày Discord Quest",
            "**2. Tiến Trình** — Theo dõi tiến độ Quest real-time",
            "**3. Hủy Quest** — Dừng phiên cày Quest đang chạy",
            "**4. Badge Farmer** — Farm badge Game Time / Đa dạng game"
        ]
    },
    social_badge: {
        label: "🏠 Badge & HypeSquad (Badge Farmer + HypeSquad + Token)",
        features: ["badge", "hypesquad", "token"],
        lines: [
            "**1. Badge Farmer** — Cày tới 100 game / 5000 giờ",
            "**2. HypeSquad** — Đổi nhà <:hypesquad_2:1571114614975107144> <:hypesquad_1:1571114521601245224> <:hypesquad:1571114469130633316> nhanh",
            "**3. Token** — Quản lý User Token an toàn"
        ]
    }
};

function buildComponentsV2Dashboard(presetKey = "all", customThumbUrl = null) {
    const preset = SETUP_PRESETS[presetKey] || SETUP_PRESETS.all;
    const thumbUrl = customThumbUrl || DEFAULT_THUMBNAIL;

    const buttonConfigs = {
        autoquest: { type: 2, style: 2, label: "AutoQuest", custom_id: `v2:${presetKey}:autoquest` },
        progress: { type: 2, style: 2, label: "Tiến Trình", custom_id: `v2:${presetKey}:progress` },
        cancel_quest: { type: 2, style: 2, label: "Hủy Quest", custom_id: `v2:${presetKey}:cancel_quest` },
        badge: { type: 2, style: 2, label: "Badge Farmer", custom_id: `v2:${presetKey}:badge` },
        hypesquad: {
            type: 2, style: 2, label: "HypeSquad",
            emoji: { id: "1571114469130633316", name: "hypesquad" },
            custom_id: `v2:${presetKey}:hypesquad`
        },
        token: { type: 2, style: 2, label: "Token", custom_id: `v2:${presetKey}:token` }
    };

    const buttons = preset.features.map(f => buttonConfigs[f]).filter(Boolean);
    const actionRows = [];
    for (let i = 0; i < buttons.length; i += 5) {
        actionRows.push({
            type: 1, // ActionRow
            components: buttons.slice(i, i + 5)
        });
    }

    // Cấu trúc Discord Components V2: Container (17) -> Section (9) + Thumbnail (11) + Separator (14) + ActionRow (1)
    return [
        {
            type: 17, // Container
            accent_color: 0xFFFFFF, // Viền trắng sang trọng giống ảnh mẫu
            components: [
                {
                    type: 9, // Section (Chữ bên trái + Ảnh Thumbnail bên phải)
                    components: [
                        {
                            type: 10, // TextDisplay
                            content: `## 🪽 TỔNG HỢP CHỨC NĂNG 🪽\n**Danh sách chức năng:**\n${preset.lines.join("\n")}`
                        }
                    ],
                    accessory: {
                        type: 11, // Thumbnail GIF/PNG bên góc phải
                        media: { url: thumbUrl }
                    }
                },
                {
                    type: 14, // Separator
                    divider: true,
                    spacing: 1
                },
                ...actionRows
            ]
        }
    ];
}

// ==============================================================================
// MODULE 6: KHỞI TẠO BOT & XỬ LÝ TƯƠNG TÁC (STATELESS ROUTING)
// ==============================================================================
const client = new Client({
    intents: [GatewayIntentBits.Guilds]
});

client.once('ready', async () => {
    console.log(`[+] Bot đã đăng nhập thành công: ${client.user.tag}`);
    await fetchLatestBuildNumber();

    const commands = [
        new SlashCommandBuilder()
            .setName('setup_menu')
            .setDescription('[ADMIN] Gửi bảng điều khiển chức năng Components V2 cao cấp')
            .setDefaultMemberPermissions(PermissionFlagsBits.Administrator)
            .addStringOption(opt =>
                opt.setName('che_do')
                    .setDescription('Chọn loại menu chức năng phù hợp cho server này')
                    .setRequired(true)
                    .addChoices(
                        { name: '🌟 Tất Cả Chức Năng (Full Menu)', value: 'all' },
                        { name: '🤖 Chuyên Cày Quest (AutoQuest + Tiến Trình + Hủy + Token)', value: 'quest_only' },
                        { name: '🎮 Quest & Badge Farmer (AutoQuest + Tiến Trình + Hủy + Badge)', value: 'quest_badge' },
                        { name: '🏠 Badge & HypeSquad (Badge Farmer + HypeSquad + Token)', value: 'social_badge' }
                    )
            )
            .addStringOption(opt =>
                opt.setName('anh_gif')
                    .setDescription('Link ảnh GIF/PNG góc phải khung (để trống nếu dùng mặc định)')
                    .setRequired(false)
            ),
        new SlashCommandBuilder()
            .setName('tien_trinh')
            .setDescription('[QUEST] Xem tiến trình cày Quest hiện tại của bạn'),
        new SlashCommandBuilder()
            .setName('huy_quest')
            .setDescription('[QUEST] Hủy ngay tiến trình cày Quest đang chạy')
    ].map(cmd => cmd.toJSON());

    const rest = new REST({ version: '10' }).setToken(process.env.BOT_TOKEN);
    try {
        await rest.put(Routes.applicationCommands(process.env.CLIENT_ID || client.user.id), { body: commands });
        console.log('[+] Đã đồng bộ Slash Commands toàn cầu!');
    } catch (err) {
        console.error('[-] Lỗi đồng bộ Slash Commands:', err);
    }
});

client.on('interactionCreate', async (interaction) => {
    try {
        // 1. XỬ LÝ SLASH COMMANDS
        if (interaction.isChatInputCommand()) {
            const uid = interaction.user.id;

            if (interaction.commandName === 'setup_menu') {
                const presetKey = interaction.options.getString('che_do');
                const customGif = interaction.options.getString('anh_gif');
                const componentsV2 = buildComponentsV2Dashboard(presetKey, customGif);

                // Gửi tin nhắn sử dụng cờ IsComponentsV2 (32768)
                const sent = await interaction.channel.send({
                    flags: MessageFlags.IsComponentsV2,
                    components: componentsV2
                });

                if (interaction.guildId) {
                    const setups = readJson(SETUP_CONFIG_FILE);
                    setups[interaction.guildId] = {
                        channelId: interaction.channelId,
                        messageId: sent.id,
                        preset: presetKey,
                        updatedAt: new Date().toISOString()
                    };
                    writeJson(SETUP_CONFIG_FILE, setups);
                }

                return interaction.reply({
                    content: `✅ Đã thiết lập bảng **${SETUP_PRESETS[presetKey].label}** thành công!`,
                    ephemeral: true
                });
            }

            if (interaction.commandName === 'tien_trinh') {
                if (!activeQuests.has(uid)) {
                    return interaction.reply({ content: "ℹ️ Bạn hiện **không có tiến trình cày Quest nào** đang chạy.", ephemeral: true });
                }
                return interaction.reply({
                    embeds: [createQuestStatusEmbed(uid)],
                    components: [buildQuestControlRow(uid)],
                    ephemeral: true
                });
            }

            if (interaction.commandName === 'huy_quest') {
                if (activeQuests.delete(uid)) {
                    return interaction.reply({ content: "🛑 **Đã hủy tiến trình cày Quest thành công!**", ephemeral: true });
                }
                return interaction.reply({ content: "ℹ️ Không có tiến trình cày Quest nào đang chạy.", ephemeral: true });
            }
        }

        // 2. XỬ LÝ BUTTONS TRÊN DASHBOARD V2 & BẢNG ĐIỀU KHIỂN
        if (interaction.isButton()) {
            const cid = interaction.customId;
            const uid = interaction.user.id;

            // Nút trên Menu chính (v2:<preset>:<action>)
            if (cid.startsWith('v2:')) {
                const action = cid.split(':')[2];
                const savedToken = getSavedToken(uid);

                if (action === 'autoquest') {
                    if (!savedToken) {
                        const modal = new ModalBuilder()
                            .setCustomId('modal:token:autoquest')
                            .setTitle('🔐 CẬP NHẬT USER TOKEN');
                        modal.addComponents(
                            new ActionRowBuilder().addComponents(
                                new TextInputBuilder()
                                    .setCustomId('token_val')
                                    .setLabel('User Token Discord')
                                    .setPlaceholder('Dán token vào đây...')
                                    .setStyle(TextInputStyle.Short)
                                    .setRequired(true)
                            )
                        );
                        return interaction.showModal(modal);
                    }
                    return startOrShowAutoQuest(interaction, savedToken);
                }

                if (action === 'progress') {
                    if (!activeQuests.has(uid)) {
                        return interaction.reply({
                            content: "ℹ️ Bạn hiện **không có tiến trình cày Quest nào** đang chạy. Bấm **AutoQuest** để bắt đầu!",
                            ephemeral: true
                        });
                    }
                    return interaction.reply({
                        embeds: [createQuestStatusEmbed(uid)],
                        components: [buildQuestControlRow(uid)],
                        ephemeral: true
                    });
                }

                if (action === 'cancel_quest') {
                    if (activeQuests.delete(uid)) {
                        return interaction.reply({ content: "🛑 **Đã hủy tiến trình cày Quest thành công!**", ephemeral: true });
                    }
                    return interaction.reply({ content: "ℹ️ Hiện không có tiến trình cày Quest nào đang chạy.", ephemeral: true });
                }

                if (action === 'badge') {
                    const modal = new ModalBuilder()
                        .setCustomId('modal:badge')
                        .setTitle('🎮 Cày Game & Badge Tùy Chỉnh');
                    modal.addComponents(
                        new ActionRowBuilder().addComponents(
                            new TextInputBuilder()
                                .setCustomId('token_val')
                                .setLabel('Discord User Token')
                                .setPlaceholder('Dán token (để trống nếu đã lưu)...')
                                .setStyle(TextInputStyle.Short)
                                .setRequired(false)
                        ),
                        new ActionRowBuilder().addComponents(
                            new TextInputBuilder()
                                .setCustomId('games_val')
                                .setLabel('Số game muốn cày (1 - 100)')
                                .setValue('100')
                                .setStyle(TextInputStyle.Short)
                                .setRequired(true)
                        ),
                        new ActionRowBuilder().addComponents(
                            new TextInputBuilder()
                                .setCustomId('hours_val')
                                .setLabel('Số giờ muốn cày (1 - 5000)')
                                .setValue('1000')
                                .setStyle(TextInputStyle.Short)
                                .setRequired(true)
                        )
                    );
                    return interaction.showModal(modal);
                }

                if (action === 'hypesquad') {
                    if (!savedToken) {
                        const modal = new ModalBuilder()
                            .setCustomId('modal:token:save')
                            .setTitle('🔐 CẬP NHẬT USER TOKEN');
                        modal.addComponents(
                            new ActionRowBuilder().addComponents(
                                new TextInputBuilder()
                                    .setCustomId('token_val')
                                    .setLabel('User Token Discord')
                                    .setPlaceholder('Dán token vào đây...')
                                    .setStyle(TextInputStyle.Short)
                                    .setRequired(true)
                            )
                        );
                        return interaction.showModal(modal);
                    }

                    const embed = new EmbedBuilder()
                        .setTitle("🏡 CHỌN NHÀ HYPESQUAD")
                        .setDescription(
                            "Chọn nhà HypeSquad bạn muốn nhận huy hiệu bên dưới:\n" +
                            "• <:hypesquad_2:1571114614975107144> **Bravery** — Dũng cảm & Nhiệt huyết\n" +
                            "• <:hypesquad_1:1571114521601245224> **Brilliance** — Sáng suốt & Kỷ luật\n" +
                            "• <:hypesquad:1571114469130633316> **Balance** — Cân bằng & Hài hòa"
                        )
                        .setColor(0x2B2D31);

                    const row = new ActionRowBuilder().addComponents(
                        new ButtonBuilder().setCustomId(`hs:1:${uid}`).setLabel("Bravery").setEmoji("1571114614975107144").setStyle(ButtonStyle.Secondary),
                        new ButtonBuilder().setCustomId(`hs:2:${uid}`).setLabel("Brilliance").setEmoji("1571114521601245224").setStyle(ButtonStyle.Secondary),
                        new ButtonBuilder().setCustomId(`hs:3:${uid}`).setLabel("Balance").setEmoji("1571114469130633316").setStyle(ButtonStyle.Secondary)
                    );
                    return interaction.reply({ embeds: [embed], components: [row], ephemeral: true });
                }

                if (action === 'token') {
                    const modal = new ModalBuilder()
                        .setCustomId('modal:token:save')
                        .setTitle('🔐 CẬP NHẬT USER TOKEN');
                    modal.addComponents(
                        new ActionRowBuilder().addComponents(
                            new TextInputBuilder()
                                .setCustomId('token_val')
                                .setLabel('User Token Discord')
                                .setPlaceholder('Dán token vào đây...')
                                .setStyle(TextInputStyle.Short)
                                .setRequired(true)
                        )
                    );
                    return interaction.showModal(modal);
                }
            }

            // Nút điều khiển Quest (qctrl:refresh:<uid> | qctrl:cancel:<uid>)
            if (cid.startsWith('qctrl:')) {
                const [, subAction, ownerId] = cid.split(':');
                if (uid !== ownerId) {
                    return interaction.reply({ content: "❌ Bảng điều khiển này không phải của bạn.", ephemeral: true });
                }
                if (subAction === 'refresh') {
                    if (!activeQuests.has(uid)) {
                        return interaction.update({ content: "✅ **Phiên cày Quest đã kết thúc hoặc đã được hủy.**", embeds: [], components: [] });
                    }
                    return interaction.update({ embeds: [createQuestStatusEmbed(uid)], components: [buildQuestControlRow(uid)] });
                }
                if (subAction === 'cancel') {
                    if (activeQuests.delete(uid)) {
                        return interaction.update({ content: "🛑 **Đã hủy tiến trình cày Quest thành công!**", embeds: [], components: [] });
                    }
                    return interaction.update({ content: "ℹ️ Không có tiến trình cày Quest nào đang chạy.", embeds: [], components: [] });
                }
            }

            // Nút chọn HypeSquad (hs:<houseId>:<uid>)
            if (cid.startsWith('hs:')) {
                const [, houseIdStr, ownerId] = cid.split(':');
                if (uid !== ownerId) {
                    return interaction.reply({ content: "❌ Bảng chọn này không phải của bạn.", ephemeral: true });
                }
                const savedToken = getSavedToken(uid);
                if (!savedToken) {
                    return interaction.update({ content: "❌ Không tìm thấy Token đã lưu!", embeds: [], components: [] });
                }
                const houseId = parseInt(houseIdStr, 10);
                const house = HYPESQUAD_HOUSES[houseId];
                const api = new DiscordUserAPI(savedToken, uid);
                const res = await api.post('/hypesquad/online', { house_id: houseId });

                if ([200, 204].includes(res.status)) {
                    const embed = new EmbedBuilder()
                        .setTitle("🎉 ĐỔI NHÀ HYPESQUAD THÀNH CÔNG")
                        .setDescription(`Tài khoản của bạn đã chuyển sang nhà ${house.emoji} **${house.name}**!`)
                        .setColor(0x2B2D31);
                    return interaction.update({ embeds: [embed], components: [] });
                }
                return interaction.update({ content: "❌ Lỗi khi đổi HypeSquad (Token hết hạn hoặc bị giới hạn)!", embeds: [], components: [] });
            }

            // Nút theo dõi Badge Farmer (badgeprog:view:<uid> | badgeprog:stop:<uid>)
            if (cid.startsWith('badgeprog:')) {
                const [, subAction, ownerId] = cid.split(':');
                if (uid !== ownerId) {
                    return interaction.reply({ content: "❌ Bảng này không phải của bạn.", ephemeral: true });
                }
                const ses = activeGameSessions.get(uid);
                if (subAction === 'view') {
                    if (!ses || !ses.isRunning) {
                        return interaction.reply({ content: "❌ Tiến trình cày Game/Badge đã dừng!", ephemeral: true });
                    }
                    const elapsedH = ((Date.now() - ses.startTime) / 3600000).toFixed(2);
                    const embed = new EmbedBuilder()
                        .setTitle("🎮 TRẠNG THÁI BADGE & GAME FARMER")
                        .setColor(0x2B2D31)
                        .addFields(
                            { name: "Trạng thái", value: ses.isPaused ? "⏸️ Đang tạm dừng (Bạn đang online)" : "🟢 Đang cày ngầm", inline: false },
                            { name: "Game hiện tại", value: `\`${ses.currentGame}\``, inline: true },
                            { name: "Tốc độ xoay game", value: ses.speedLabel, inline: true },
                            { name: "Số game đã xoay", value: `**${ses.gamesRotated}** / ${ses.maxGames} game`, inline: true },
                            { name: "Thời gian chạy", value: `⏱️ **${elapsedH} / ${ses.targetHours} giờ**`, inline: true }
                        );
                    return interaction.reply({ embeds: [embed], ephemeral: true });
                }
                if (subAction === 'stop') {
                    if (ses) {
                        ses.stop();
                        activeGameSessions.delete(uid);
                        return interaction.update({ content: "🛑 **Đã dừng hoàn toàn tiến trình cày Game/Badge!**", embeds: [], components: [] });
                    }
                    return interaction.reply({ content: "❌ Không tìm thấy tiến trình cày game.", ephemeral: true });
                }
            }
        }

        // 3. XỬ LÝ MODALS
        if (interaction.isModalSubmit()) {
            const uid = interaction.user.id;

            if (interaction.customId.startsWith('modal:token:')) {
                const mode = interaction.customId.split(':')[2];
                const token = interaction.fields.getTextInputValue('token_val').trim();
                await interaction.deferReply({ ephemeral: true });

                const api = new DiscordUserAPI(token, uid);
                const res = await api.get('/users/@me');
                if (res.status === 200) {
                    saveToken(uid, token);
                    if (mode === 'autoquest') {
                        return startOrShowAutoQuest(interaction, token);
                    }
                    return interaction.followUp({ content: "✅ **Lưu Token thành công!** Bạn có thể sử dụng các chức năng ngay.", ephemeral: true });
                }
                return interaction.followUp({ content: "❌ **Token không hợp lệ hoặc đã hết hạn!**", ephemeral: true });
            }

            if (interaction.customId === 'modal:badge') {
                const rawTok = interaction.fields.getTextInputValue('token_val').trim();
                const token = rawTok || getSavedToken(uid);
                if (!token) {
                    return interaction.reply({ content: "❌ **Bạn chưa nhập Token hoặc chưa lưu Token trước đó!**", ephemeral: true });
                }

                const maxG = Math.max(1, Math.min(parseInt(interaction.fields.getTextInputValue('games_val'), 10) || 100, 100));
                const maxH = Math.max(1, Math.min(parseInt(interaction.fields.getTextInputValue('hours_val'), 10) || 1000, 5000));

                await interaction.deferReply({ ephemeral: true });
                const api = new DiscordUserAPI(token, uid);
                const res = await api.get('/users/@me');
                if (res.status !== 200) {
                    return interaction.followUp({ content: "❌ **Token không hợp lệ hoặc đã hết hạn!**", ephemeral: true });
                }

                saveToken(uid, token);
                const oldSes = activeGameSessions.get(uid);
                if (oldSes) oldSes.stop();

                const session = new BadgeFarmSession(uid, token, maxG, maxH);
                activeGameSessions.set(uid, session);
                session.start();

                const embed = new EmbedBuilder()
                    .setTitle("🎮 ĐÃ KÍCH HOẠT CÀY GAME & BADGE")
                    .setDescription(
                        `🟢 Đang treo **${maxG} game** trong tối đa **${maxH} giờ**.\n` +
                        `⚙️ **Chế độ xoay vòng tự động:** ${session.speedLabel}`
                    )
                    .setColor(0x2B2D31);

                const row = new ActionRowBuilder().addComponents(
                    new ButtonBuilder().setCustomId(`badgeprog:view:${uid}`).setLabel("Xem Tiến Trình").setEmoji("📊").setStyle(ButtonStyle.Secondary),
                    new ButtonBuilder().setCustomId(`badgeprog:stop:${uid}`).setLabel("Hủy Cày Game").setEmoji("⏹️").setStyle(ButtonStyle.Danger)
                );

                return interaction.followUp({ embeds: [embed], components: [row], ephemeral: true });
            }
        }
    } catch (err) {
        console.error('[-] Lỗi xử lý Interaction:', err);
    }
});

// Chống sập tiến trình Node.js khi gặp lỗi mạng bất ngờ
process.on('unhandledRejection', (err) => console.error('[UnhandledRejection]', err));
process.on('uncaughtException', (err) => console.error('[UncaughtException]', err));

client.login(process.env.BOT_TOKEN);
