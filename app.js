// Cricket AI Live Broadcaster - Frontend Client
(function () {
  let ws = null;
  let isAudioMuted = false;
  const audioPlayer = document.getElementById("liveAudio");

  // DOM Elements
  const btnStart = document.getElementById("btnStart");
  const btnStop = document.getElementById("btnStop");
  const btnAudioToggle = document.getElementById("btnAudioToggle");
  const audioIcon = document.getElementById("audioIcon");
  const audioText = document.getElementById("audioText");
  const statusBadge = document.getElementById("statusBadge");
  const statusText = document.getElementById("statusText");
  const wsBadge = document.getElementById("wsBadge");
  const wsText = document.getElementById("wsText");

  const matchTitle = document.getElementById("matchTitle");
  const matchVenue = document.getElementById("matchVenue");
  const battingTeam = document.getElementById("battingTeam");
  const teamScore = document.getElementById("teamScore");
  const oversCount = document.getElementById("oversCount");
  const crrBadge = document.getElementById("crrBadge");
  const oppScore = document.getElementById("oppScore");
  const strikerName = document.getElementById("strikerName");
  const strikerFigures = document.getElementById("strikerFigures");
  const strikerMeta = document.getElementById("strikerMeta");
  const nonStrikerName = document.getElementById("nonStrikerName");
  const nonStrikerFigures = document.getElementById("nonStrikerFigures");
  const nonStrikerMeta = document.getElementById("nonStrikerMeta");
  const bowlerName = document.getElementById("bowlerName");
  const bowlerFigures = document.getElementById("bowlerFigures");
  const bowlerEcon = document.getElementById("bowlerEcon");
  const partnershipText = document.getElementById("partnershipText");
  const lastWktText = document.getElementById("lastWktText");
  const thisOverContainer = document.getElementById("thisOverContainer");
  const alertBanner = document.getElementById("alertBanner");

  // New Alert Banner & Preset Selector Elements
  const systemAlert = document.getElementById("systemAlert");
  const systemAlertText = document.getElementById("systemAlertText");
  const btnDismissAlert = document.getElementById("btnDismissAlert");
  const presetMatchSelect = document.getElementById("presetMatchSelect");
  const btnRefreshMatches = document.getElementById("btnRefreshMatches");
  const matchUrlInput = document.getElementById("matchUrl");
  const resolveBadge = document.getElementById("resolveBadge");
  const resolveBadgeText = document.getElementById("resolveBadgeText");
  const voiceEngineSelect = document.getElementById("voiceEngineSelect");
  const sarvamKeyGroup = document.getElementById("sarvamKeyGroup");
  const sarvamApiKeyInput = document.getElementById("sarvamApiKey");
  const voiceStudioInfo = document.getElementById("voiceStudioInfo");

  const onAirBadge = document.getElementById("onAirBadge");
  const onAirText = document.getElementById("onAirText");
  const waveform = document.getElementById("waveform");
  const speakingText = document.getElementById("speakingText");
  const playerStateText = document.getElementById("playerStateText");

  const feedList = document.getElementById("feedList");
  const feedCount = document.getElementById("feedCount");

  let totalBalls = 0;
  let isRunning = false;

  function showAlert(msg) {
    if (systemAlert && systemAlertText) {
      systemAlertText.textContent = msg;
      systemAlert.classList.remove("hidden");
    }
  }

  function hideAlert() {
    if (systemAlert) {
      systemAlert.classList.add("hidden");
    }
  }

  function showResolveNote(note) {
    if (resolveBadge && resolveBadgeText && note) {
      resolveBadgeText.textContent = note;
      resolveBadge.classList.remove("hidden");
    }
  }

  function hideResolveNote() {
    if (resolveBadge) {
      resolveBadge.classList.add("hidden");
    }
  }

  if (btnDismissAlert) {
    btnDismissAlert.addEventListener("click", hideAlert);
  }

  const geminiApiKeyInput = document.getElementById("geminiApiKey");
  if (geminiApiKeyInput) {
    try {
      const savedGeminiKey = localStorage.getItem("geminiApiKey");
      if (savedGeminiKey) geminiApiKeyInput.value = savedGeminiKey;
    } catch (_) {}
  }

  // Voice Engine Selector: show only the settings needed by the selected provider.
  if (voiceEngineSelect) {
    try {
      const savedKey = localStorage.getItem("sarvamApiKey");
      if (savedKey && sarvamApiKeyInput) sarvamApiKeyInput.value = savedKey;
    } catch (_) {}

    const updateVoiceProviderFields = () => {
      if (sarvamKeyGroup) {
        sarvamKeyGroup.classList.toggle("hidden", voiceEngineSelect.value !== "sarvam");
      }
      if (voiceStudioInfo) {
        voiceStudioInfo.classList.toggle("hidden", voiceEngineSelect.value !== "voicestudio");
      }
    };
    voiceEngineSelect.addEventListener("change", updateVoiceProviderFields);
    updateVoiceProviderFields();
  }

  // 1. WebSocket Connection
  function connectWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      wsBadge.className = "badge-conn connected";
      wsText.textContent = "LIVE CONNECTED";
      syncLatestStatus(true);
    };

    ws.onclose = () => {
      wsBadge.className = "badge-conn disconnected";
      wsText.textContent = "DISCONNECTED";
      setTimeout(connectWebSocket, 3000);
    };

    ws.onerror = (err) => {
      console.error("WebSocket error:", err);
      ws.close();
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleMessage(msg);
      } catch (e) {
        console.error("Error parsing WS message:", e);
      }
    };
  }

  // 2. Dispatch WebSocket Message
  function handleMessage(msg) {
    switch (msg.type) {
      case "status":
        updateEngineStatus(msg.data);
        break;

      case "match_state":
        updateScoreboard(msg.data);
        break;

      case "commentary_preview":
        handleCommentaryPreview(msg.data);
        break;

      case "commentary":
        handleCommentary(msg.data);
        break;

      case "tts_error": {
        const errorKey = commentaryEventId(msg.data || {});
        if (msg.data && msg.data.message && !spokenEventIds.has(errorKey)) {
          playerStateText.textContent = "Madhur TTS delayed for ball " + (msg.data.over || "") + ": " + msg.data.message;
        }
        break;
      }

      case "feed_item":
        addFeedItem(msg.data);
        break;

      case "match_resolved":
        if (msg.data && msg.data.resolved_url) {
          matchUrlInput.value = msg.data.resolved_url;
        }
        if (msg.data && msg.data.note) {
          showResolveNote(msg.data.note);
        }
        break;

      case "error":
        showAlert(msg.data.message || msg.data);
        updateEngineStatus({ is_running: false });
        break;
    }
  }

  function updateEngineStatus(data) {
    isRunning = !!data.is_running;
    if (data.is_running) {
      statusBadge.className = "badge-status streaming";
      if (data.has_youtube) {
        statusText.textContent = "LIVE: YOUTUBE & WEB";
      } else {
        statusText.textContent = "STREAMING LIVE";
      }
      btnStart.disabled = true;
      btnStop.disabled = false;
    } else {
      statusBadge.className = "badge-status idle";
      statusText.textContent = "IDLE";
      btnStart.disabled = false;
      btnStop.disabled = true;
    }
    // Re-evaluate the on-air badge for the new running state (shows the
    // "waiting for next ball" loader as soon as a broadcast starts).
    setTalkingState(false);
    syncScreenWakeLock();
  }

  function updateScoreboard(data) {
    if (data.title) matchTitle.textContent = data.title;
    if (data.venue) matchVenue.textContent = data.venue;
    if (data.batting_team) battingTeam.textContent = data.batting_team;
    if (data.total_runs !== undefined && data.total_wickets !== undefined) {
      teamScore.textContent = `${data.total_runs}/${data.total_wickets}`;
    }
    if (data.overs) oversCount.textContent = `OVERS: ${data.overs}`;
    if (data.crr && crrBadge) crrBadge.textContent = `CRR: ${data.crr}`;
    if (oppScore) {
      if (data.team2_score) {
        oppScore.textContent = data.team2_score;
        oppScore.style.display = "inline-block";
      } else {
        oppScore.textContent = "";
        oppScore.style.display = "none";
      }
    }

    if (data.striker) strikerName.textContent = data.striker;
    if (data.striker_runs !== undefined) strikerFigures.textContent = `${data.striker_runs} (${data.striker_balls || 0})`;
    if (strikerMeta) {
      strikerMeta.textContent = `4s: ${data.striker_fours ?? 0} • 6s: ${data.striker_sixes ?? 0} • SR: ${data.striker_sr || '0.00'}`;
    }

    if (data.non_striker) nonStrikerName.textContent = data.non_striker;
    if (data.non_striker_runs !== undefined) nonStrikerFigures.textContent = `${data.non_striker_runs} (${data.non_striker_balls || 0})`;
    if (nonStrikerMeta) {
      nonStrikerMeta.textContent = `4s: ${data.non_striker_fours ?? 0} • 6s: ${data.non_striker_sixes ?? 0} • SR: ${data.non_striker_sr || '0.00'}`;
    }

    if (data.bowler) bowlerName.textContent = data.bowler;
    if (data.bowler_figures) bowlerFigures.textContent = data.bowler_figures;
    if (bowlerEcon) bowlerEcon.textContent = `Econ: ${data.bowler_econ || '0.00'}`;

    if (partnershipText && data.partnership) partnershipText.textContent = `${data.partnership} runs`;
    if (lastWktText && data.last_wicket) lastWktText.textContent = data.last_wicket;

    // Render this over ball bubbles
    if (thisOverContainer && data.this_over_balls && Array.isArray(data.this_over_balls)) {
      thisOverContainer.innerHTML = "";
      data.this_over_balls.forEach((b) => {
        const bubble = document.createElement("span");
        let cls = "ball-bubble";
        if (b === "4") cls += " four";
        else if (b === "6") cls += " six";
        else if (b.toUpperCase().includes("W")) cls += " wicket";
        bubble.className = cls;
        bubble.textContent = b;
        thisOverContainer.appendChild(bubble);
      });
    }

    const bannerText = data.alert || data.status || "";
    if (bannerText) {
      alertBanner.textContent = bannerText;
      alertBanner.classList.remove("hidden");
    } else {
      alertBanner.classList.add("hidden");
    }
  }


  const SILENT_AUDIO = "data:audio/wav;base64,UklGRigAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAAABkYXRhAgAAAAEA";
  let isAudioUnlocked = false;

  // A running stream is server-owned; browser visibility must never stop it.
  // Keep the visible screen awake where supported and re-sync cached live score
  // when a background tab becomes active again.
  let screenWakeLock = null;
  let statusSyncInFlight = false;
  let lastStatusSyncAt = 0;

  async function syncScreenWakeLock() {
    const shouldHold = isRunning && document.visibilityState === "visible";
    if (!shouldHold) {
      if (screenWakeLock) {
        try { await screenWakeLock.release(); } catch (_) {}
        screenWakeLock = null;
      }
      return;
    }
    if (!("wakeLock" in navigator) || screenWakeLock) return;
    try {
      screenWakeLock = await navigator.wakeLock.request("screen");
      screenWakeLock.addEventListener("release", () => {
        screenWakeLock = null;
      }, { once: true });
    } catch (e) {
      // Wake Lock may be denied by the browser or OS; background server polling
      // continues independently as long as the broadcast is running.
      console.debug("[Preet Sports] Screen wake lock unavailable:", e?.message || e);
    }
  }

  async function syncLatestStatus(force = false) {
    if (statusSyncInFlight) return;
    const now = Date.now();
    // Hidden-page timers are often throttled. When allowed to run, keep a
    // modest heartbeat while broadcasting; on foreground, always force a sync.
    if (!force && document.visibilityState !== "visible" && now - lastStatusSyncAt < 15000) return;
    if (!force && !isRunning) return;
    statusSyncInFlight = true;
    lastStatusSyncAt = now;
    try {
      const resp = await fetch("/api/status", {
        cache: "no-store",
        headers: { "Cache-Control": "no-cache" }
      });
      if (!resp.ok) return;
      const data = await resp.json();
      if (data.match_state && Object.keys(data.match_state).length) {
        updateScoreboard(data.match_state);
      }
      // Avoid resetting the current on-air animation/audio status on every poll.
      if (!!data.is_running !== isRunning) {
        updateEngineStatus(data);
      }
      await syncScreenWakeLock();
    } catch (e) {
      console.debug("[Preet Sports] Status resync deferred:", e?.message || e);
    } finally {
      statusSyncInFlight = false;
    }
  }

  function unlockAudio() {
    if (isAudioUnlocked) return;
    try {
      if (audioPlayer) {
        audioPlayer.src = SILENT_AUDIO;
        const p = audioPlayer.play();
        if (p !== undefined) {
          p.then(() => {
            audioPlayer.pause();
            audioPlayer.currentTime = 0;
            isAudioUnlocked = true;
          }).catch(() => {});
        }
      }
    } catch (_) {}
  }

  // Unlock audio on first user interaction anywhere on the page
  document.addEventListener("click", unlockAudio, { once: true });
  document.addEventListener("touchstart", unlockAudio, { once: true });

  let commentaryTimeout = null;
  let instantFallbackTimer = null;
  let currentPreviewEventId = "";
  let activeBrowserUtterance = null;
  let activePlaybackEventId = "";
  let pendingBallEvent = null;
  const pendingOverSummaries = [];
  const spokenEventIds = new Set();
  const browserFallbackEventIds = new Set();

  function commentaryEventId(data) {
    return String(
      (data && data.event_id) ||
      (data && data.over ? (data.over + "|" + (data.circle_value || data.badge || "ball")) : "") ||
      (data && data.text ? data.text.slice(0, 80) : "live-ball")
    );
  }

  function rememberEvent(set, key) {
    if (!key) return;
    set.add(key);
    while (set.size > 80) set.delete(set.values().next().value);
  }

  function cancelBrowserSpeech() {
    try {
      if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    } catch (_) {}
    activeBrowserUtterance = null;
  }

  function speakInstantBrowserFallback(data, key) {
    if (!data || !key || isAudioMuted || spokenEventIds.has(key)) return false;
    if (!("speechSynthesis" in window) || typeof SpeechSynthesisUtterance === "undefined") {
      playerStateText.textContent = "Madhur audio is still preparing; this browser has no instant speech fallback.";
      return false;
    }

    try {
      const synth = window.speechSynthesis;
      const utterance = new SpeechSynthesisUtterance(String(data.speech_text || data.text || ""));
      const voices = synth.getVoices ? synth.getVoices() : [];
      const hindiVoices = voices.filter(v => /^hi(?:-|$)/i.test(v.lang || ""));
      const preferred =
        voices.find(v => /madhur/i.test(v.name || "")) ||
        hindiVoices.find(v => /google|microsoft|natural/i.test(v.name || "")) ||
        hindiVoices[0] ||
        voices.find(v => /^en-IN/i.test(v.lang || "")) ||
        null;

      if (preferred) {
        utterance.voice = preferred;
        utterance.lang = preferred.lang || "hi-IN";
      } else {
        utterance.lang = /[\u0900-\u097F]/.test(utterance.text) ? "hi-IN" : "en-IN";
      }

      const badge = String(data.badge || "").toUpperCase();
      utterance.rate = /SIX|WICKET/.test(badge) ? 1.16 : /FOUR/.test(badge) ? 1.12 : 1.08;
      utterance.pitch = /SIX|WICKET/.test(badge) ? 1.12 : 1.02;
      utterance.volume = 1;

      // Claim this ball before scheduling local speech; any late MP3 is suppressed.
      rememberEvent(spokenEventIds, key);
      rememberEvent(browserFallbackEventIds, key);
      activeBrowserUtterance = utterance;
      activeBrowserUtterance.__preetEventKey = key;
      activePlaybackEventId = key;

      utterance.onstart = () => {
        if (currentPreviewEventId !== key) return;
        onAirBadge.className = "on-air-badge active";
        onAirText.textContent = (data.badge || "LIVE BALL") + " • FAST VOICE";
        waveform.className = "waveform active";
        playerStateText.textContent =
          (preferred && /madhur/i.test(preferred.name || ""))
            ? "⚡ Instant local Madhur voice"
            : "⚡ Instant browser voice (" + (preferred ? preferred.name : utterance.lang) + "); remote Madhur was delayed";
      };
      utterance.onend = () => {
        if (activeBrowserUtterance === utterance) activeBrowserUtterance = null;
        finishPlayback(key);
      };
      utterance.onerror = () => {
        if (activeBrowserUtterance === utterance) activeBrowserUtterance = null;
        playerStateText.textContent = "Browser voice fallback failed; check browser audio permissions."; 
        finishPlayback(key);
      };

      synth.speak(utterance);
      return true;
    } catch (e) {
      console.warn("[Preet Sports] Instant voice fallback failed:", e);
      return false;
    }
  }

  function eventVersion(data) {
    const value = Number(data && data.version);
    return Number.isFinite(value) ? value : 0;
  }

  function isOverSummary(data) {
    return !!data && (data.kind === "over_summary" || /OVER COMPLETE/i.test(String(data.badge || "")));
  }

  function playbackIsActive() {
    return !!activePlaybackEventId || !!activeBrowserUtterance ||
      !!(audioPlayer && !audioPlayer.paused && !audioPlayer.ended);
  }

  function enqueueLatestBall(kind, data) {
    if (!data) return;
    const key = commentaryEventId(data);
    const version = eventVersion(data);
    const candidate = { kind, data, key, version };
    if (!pendingBallEvent || version > pendingBallEvent.version ||
        (version === pendingBallEvent.version && key === pendingBallEvent.key && kind === "commentary" && pendingBallEvent.kind === "preview") ||
        (version === 0 && pendingBallEvent.version === 0 && key !== pendingBallEvent.key)) {
      pendingBallEvent = candidate;
    }
  }

  function enqueueOverSummary(data) {
    if (!data) return;
    const key = commentaryEventId(data);
    if (spokenEventIds.has(key) || pendingOverSummaries.some(item => commentaryEventId(item) === key)) return;
    pendingOverSummaries.push(data);
  }

  function finishPlayback(key) {
    if (activePlaybackEventId !== key) return;
    activePlaybackEventId = "";
    if (activeBrowserUtterance && activeBrowserUtterance.__preetEventKey === key) activeBrowserUtterance = null;
    if (commentaryTimeout) { clearTimeout(commentaryTimeout); commentaryTimeout = null; }
    if (currentPreviewEventId === key) setTalkingState(false);
    drainPendingPlayback();
  }

  function drainPendingPlayback() {
    if (playbackIsActive()) return;
    if (pendingOverSummaries.length) {
      handleCommentary(pendingOverSummaries.shift());
      return;
    }
    if (!pendingBallEvent) return;
    const pending = pendingBallEvent;
    pendingBallEvent = null;
    if (pending.kind === "commentary") handleCommentary(pending.data);
    else handleCommentaryPreview(pending.data);
  }

  function circleOnlyText(data) {
    // Keep the studio display focused on the actual CREX circle, not a long text transcript.
    const over = String((data && data.over) || "").trim();
    const token = String((data && (data.circle_value ?? data.runs)) || "").trim().toUpperCase();
    if (!token) return over ? ("BALL " + over) : "WAITING FOR CIRCLE";
    return (over ? ("BALL " + over + "  •  ") : "") + "CIRCLE: " + token;
  }

  function handleCommentaryPreview(data) {
    if (!data) return;
    const key = commentaryEventId(data);
    if (spokenEventIds.has(key)) return;
    if (playbackIsActive() && activePlaybackEventId !== key) {
      enqueueLatestBall("preview", data);
      playerStateText.textContent = "Finishing the current announcement; newest ball is waiting.";
      return;
    }
    if (pendingOverSummaries.length && !playbackIsActive()) {
      enqueueLatestBall("preview", data);
      drainPendingPlayback();
      return;
    }
    if (instantFallbackTimer) { clearTimeout(instantFallbackTimer); instantFallbackTimer = null; }
    if (commentaryTimeout) { clearTimeout(commentaryTimeout); commentaryTimeout = null; }
    currentPreviewEventId = key;
    speakingText.textContent = '"' + circleOnlyText(data) + '"';
    onAirBadge.className = "on-air-badge waiting";
    onAirText.textContent = (data.badge || "NEW BALL") + " • PREPARING AUDIO";
    waveform.className = "waveform";
    playerStateText.textContent = "Preparing Madhur audio for " + (data.over || "the latest ball") + " [" + (data.circle_value || "?") + "]...";
    const waitMs = Number(data.voice_fallback_after_ms) || 1300;
    if (!isAudioMuted) {
      instantFallbackTimer = setTimeout(() => {
        instantFallbackTimer = null;
        if (spokenEventIds.has(key)) return;
        if (playbackIsActive() && activePlaybackEventId !== key) { enqueueLatestBall('preview', data); return; }
        if (currentPreviewEventId === key) speakInstantBrowserFallback(data, key);
      }, Math.max(500, Math.min(waitMs, 1800)));
    }
  }

  function handleCommentary(data) {
    if (!data) return;
    const key = commentaryEventId(data);
    const overSummary = isOverSummary(data);
    if (instantFallbackTimer && key === currentPreviewEventId) { clearTimeout(instantFallbackTimer); instantFallbackTimer = null; }
    if (overSummary) {
      if (spokenEventIds.has(key)) return;
      if (playbackIsActive() && activePlaybackEventId !== key) { enqueueOverSummary(data); return; }
    } else {
      if (browserFallbackEventIds.has(key) || spokenEventIds.has(key)) return;
      if (playbackIsActive() && activePlaybackEventId !== key) { enqueueLatestBall('commentary', data); return; }
      if (pendingOverSummaries.length && !playbackIsActive()) { enqueueLatestBall('commentary', data); drainPendingPlayback(); return; }
    }
    if (commentaryTimeout) { clearTimeout(commentaryTimeout); commentaryTimeout = null; }
    currentPreviewEventId = key;
    rememberEvent(spokenEventIds, key);
    speakingText.textContent = '"' + (overSummary ? (data.text || "") : circleOnlyText(data)) + '"';
    setTalkingState(true, data.badge);
    if (data.audio_url && !isAudioMuted && audioPlayer) {
      cancelBrowserSpeech();
      audioPlayer.pause();
      audioPlayer.currentTime = 0;
      audioPlayer.muted = false;
      audioPlayer.src = data.audio_url;
      activePlaybackEventId = key;
      audioPlayer.onended = () => finishPlayback(key);
      audioPlayer.onerror = () => finishPlayback(key);
      const playPromise = audioPlayer.play();
      if (playPromise !== undefined) {
        playPromise.then(() => {
          isAudioUnlocked = true;
          playerStateText.textContent = "🎙️ On-Air Madhur: " + (data.badge || "Live Commentary") +
            (data.duration ? " (" + data.duration.toFixed(1) + "s)" : "");
        }).catch((e) => {
          console.warn("Remote audio could not play; using instant browser voice:", e);
          if (activePlaybackEventId === key) activePlaybackEventId = "";
          audioPlayer.onended = null;
          audioPlayer.onerror = null;
          spokenEventIds.delete(key);
          speakInstantBrowserFallback(data, key);
        });
      }
    } else if (!isAudioMuted) {
      spokenEventIds.delete(key);
      const started = speakInstantBrowserFallback(data, key);
      if (!started) { setTalkingState(false); drainPendingPlayback(); }
    } else {
      activePlaybackEventId = "";
      setTalkingState(false);
      drainPendingPlayback();
    }
  }
  function resetStudioToIdle() {
    onAirBadge.className = "on-air-badge";
    onAirText.textContent = "STUDIO READY";
    speakingText.textContent = '"Start a broadcast to hear live ball-by-ball commentary here."';
    playerStateText.textContent = "Voice synthesizer online (auto-play enabled)";
  }

  function setTalkingState(isTalking, badge = null) {
    if (isTalking) {
      onAirBadge.className = "on-air-badge active";
      onAirText.textContent = badge ? `${badge} • ON AIR` : "ON AIR (COMMENTATOR)";
      waveform.className = "waveform active";
    } else if (isRunning) {
      onAirBadge.className = "on-air-badge waiting";
      onAirText.textContent = "WAITING FOR NEXT BALL...";
      waveform.className = "waveform";
      if (!speakingText.textContent || speakingText.textContent.includes("Start a broadcast")) {
        speakingText.textContent = '"Waiting for the next ball\'s commentary..."';
      }
      playerStateText.textContent = "⏳ Listening for the next ball...";
    } else {
      onAirBadge.className = "on-air-badge";
      onAirText.textContent = "STUDIO READY";
      waveform.className = "waveform";
      playerStateText.textContent = "Voice synthesizer online (auto-play enabled)";
    }
  }

  const seenFeedKeys = new Set();

  function addFeedItem(item) {
    if (!item || item.over === undefined || item.over === null) return;

    const overStr = String(item.over).trim();
    // Exclude studio/break/meta entries from the ball-by-ball delivery timeline
    if (["pre-match", "result", "summary", "preview", "post-match", "break", "stumps", "update", "live"].includes(overStr.toLowerCase())) {
      return;
    }

    // Strict deduplication by over number and delivery details
    const itemKey = `${overStr}_${item.runs}_${(item.matchup || '').trim()}`;
    if (seenFeedKeys.has(itemKey)) {
      return;
    }
    seenFeedKeys.add(itemKey);

    const empty = feedList.querySelector(".feed-empty");
    if (empty) empty.remove();

    totalBalls++;
    feedCount.textContent = `${totalBalls} Balls`;

    const el = document.createElement("div");
    el.className = "feed-item";

    let badgeClass = "feed-runs-badge";
    if (item.runs === "4") badgeClass += " four";
    else if (item.runs === "6") badgeClass += " six";
    else if (String(item.runs).toUpperCase().includes("W")) badgeClass += " wicket";

    const eventBadgeHtml = item.badge ? `<span class="feed-event-badge">${item.badge}</span>` : "";

    el.innerHTML = `
      <div class="feed-item-top">
        <div style="display: flex; align-items: center; gap: 8px;">
          <span class="feed-over-badge">OVER ${item.over}</span>
          ${eventBadgeHtml}
        </div>
        <span class="${badgeClass}">${item.runs} RUN${item.runs === "1" ? "" : "S"}</span>
      </div>
      <div class="feed-matchup">${item.matchup || ""}</div>
      <div class="feed-commentary">${item.commentary}</div>
    `;

    feedList.prepend(el);
  }


  // 3. User Controls & Active Match Selector
  async function loadActiveMatches() {
    if (!presetMatchSelect) return;
    try {
      presetMatchSelect.innerHTML = '<option value="">⚡ Fetching live matches from CREX...</option>';
      const resp = await fetch("/api/live-matches");
      const data = await resp.json();
      if (data.ok && Array.isArray(data.matches) && data.matches.length > 0) {
        presetMatchSelect.innerHTML = '<option value="">-- Select an active match from CREX --</option>';
        data.matches.forEach((m) => {
          const opt = document.createElement("option");
          opt.value = m.url;
          opt.textContent = m.title;
          presetMatchSelect.appendChild(opt);
        });
      } else {
        presetMatchSelect.innerHTML = '<option value="">No live matches currently listed (paste URL below)</option>';
      }
    } catch (err) {
      console.warn("Failed to load active matches:", err);
      presetMatchSelect.innerHTML = '<option value="">Unable to fetch match list (paste URL below)</option>';
    }
  }

  if (presetMatchSelect) {
    presetMatchSelect.addEventListener("change", (e) => {
      const selected = e.target.value;
      if (selected) {
        matchUrlInput.value = selected;
        hideAlert();
      }
    });
  }

  if (btnRefreshMatches) {
    btnRefreshMatches.addEventListener("click", () => {
      loadActiveMatches();
    });
  }

  // Real-time Input Match Auto-Resolver
  let resolveDebounceTimer = null;
  matchUrlInput.addEventListener("input", () => {
    hideResolveNote();
    hideAlert();
    clearTimeout(resolveDebounceTimer);
    const val = matchUrlInput.value.trim();
    if (val.length < 3) return;

    resolveDebounceTimer = setTimeout(async () => {
      try {
        const resp = await fetch("/api/resolve", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: val }),
        });
        const res = await resp.json();
        if (res.ok && res.note) {
          showResolveNote(res.note);
        }
      } catch (_) {}
    }, 450);
  });

  btnStart.addEventListener("click", async () => {
    hideAlert();
    const url = matchUrlInput.value.trim();
    const lang = document.getElementById("langSelect").value;
    const key = document.getElementById("streamKey").value.trim();
    const ttsProvider = voiceEngineSelect ? voiceEngineSelect.value : "edge";
    const sarvamApiKey = sarvamApiKeyInput ? sarvamApiKeyInput.value.trim() : "";
    const geminiApiKey = geminiApiKeyInput ? geminiApiKeyInput.value.trim() : "";

    // Prime HTML5 audio element on user click to unlock browser autoplay policy
    try {
      if (audioPlayer) {
        audioPlayer.play().then(() => audioPlayer.pause()).catch(() => {});
      }
    } catch (_) {}

    // Always send start attempts to the server so it can report the exact phase/reason.
    // Otherwise client-side validation leaves /api/status stuck at start_debug=not_started.
    if (!url) {
      showAlert("No match URL is entered. Sending a diagnostic request to the server.");
    }
    if (ttsProvider === "sarvam" && !sarvamApiKey) {
      showAlert("Sarvam API key is missing. Sending the request so the server can record the reason.");
    }
    console.info("[Preet Sports] Start button clicked; sending /api/start", {
      hasUrl: Boolean(url), lang, ttsProvider
    });

    try {
      if (ttsProvider === "sarvam" && sarvamApiKey) {
        localStorage.setItem("sarvamApiKey", sarvamApiKey);
      }
      if (geminiApiKey) {
        localStorage.setItem("geminiApiKey", geminiApiKey);
      }
    } catch (_) {}

    btnStart.disabled = true;
    statusText.textContent = "CONNECTING FEED...";

    // Show a loader right away — don't wait for the first WS message to
    // tell the user something is happening.
    onAirBadge.className = "on-air-badge waiting";
    onAirText.textContent = "CONNECTING TO MATCH FEED...";
    speakingText.textContent = '"Connecting to the live match feed — this can take a few seconds..."';
    playerStateText.textContent = "⏳ Fetching the latest match data...";

    // Reset client ball feed for new stream
    seenFeedKeys.clear();
    totalBalls = 0;
    feedCount.textContent = "0 Balls";
    feedList.innerHTML = '<div class="feed-empty"><div class="feed-empty-icon">🏏</div><p>Waiting for live ball updates from match feed...</p></div>';

    try {
      const resp = await fetch("/api/start", {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url,
          lang,
          stream_key: key,
          tts_provider: ttsProvider,
          sarvam_api_key: sarvamApiKey,
          gemini_api_key: geminiApiKey
        }),
      });
      const res = await resp.json();
      console.info("[Preet Sports] /api/start response", resp.status, res);
      if (!res.ok) {
        playerStateText.textContent = "Start rejected by server (HTTP " + resp.status + "): " + (res.error || "Unknown error");
        showAlert("Cannot start broadcast: " + (res.error || "Match feed not found."));
        btnStart.disabled = false;
        statusText.textContent = "IDLE";
        resetStudioToIdle();
      } else {
        hideAlert();
        playerStateText.textContent = "Start request accepted; waiting for live feed…";
        if (res.resolved_url) {
          matchUrlInput.value = res.resolved_url;
        }
        if (res.note) {
          showResolveNote(res.note);
        }
      }
    } catch (e) {
      showAlert("Failed to connect to backend server: " + e.message);
      btnStart.disabled = false;
      statusText.textContent = "IDLE";
      resetStudioToIdle();
    }
  });

  btnStop.addEventListener("click", async () => {
    btnStop.disabled = true;
    if (instantFallbackTimer) {
      clearTimeout(instantFallbackTimer);
      instantFallbackTimer = null;
    }
    cancelBrowserSpeech();
    activePlaybackEventId = "";
    pendingBallEvent = null;
    pendingOverSummaries.length = 0;
    if (audioPlayer) {
      try { audioPlayer.pause(); audioPlayer.currentTime = 0; } catch (_) {}
    }
    try {
      await fetch("/api/stop", { method: "POST" });
    } catch (e) {
      console.error(e);
    }
  });

  btnAudioToggle.addEventListener("click", () => {
    isAudioMuted = !isAudioMuted;
    if (isAudioMuted) {
      if (instantFallbackTimer) {
        clearTimeout(instantFallbackTimer);
        instantFallbackTimer = null;
      }
      cancelBrowserSpeech();
      activePlaybackEventId = "";
      pendingBallEvent = null;
      pendingOverSummaries.length = 0;
      if (audioPlayer) {
        audioPlayer.pause();
        audioPlayer.muted = true;
      }
      audioIcon.textContent = "🔇";
      audioText.textContent = "Unmute Audio";
      playerStateText.textContent = "Audio muted by user";
    } else {
      if (audioPlayer) audioPlayer.muted = false;
      audioIcon.textContent = "🔊";
      audioText.textContent = "Mute Audio";
      playerStateText.textContent = "Audio unmuted; next new ball will play (no replay).";
      // Do not call play() here: unmuting must not replay the previous ball.
    }
  });

  // 4. Initial Fetch
  async function loadInitialData() {
    try {
      const res = await fetch("/api/status");
      const data = await res.json();
      updateEngineStatus(data);
      if (data.match_state) updateScoreboard(data.match_state);
    } catch (e) {
      console.log("Could not load initial data:", e);
    }
  }

  // Re-sync score from the server's live cache every 2 seconds while the
  // broadcast runs. This doesn't scrape CREX from the browser and survives
  // missed/stalled WebSocket updates by replacing them with the latest snapshot.
  setInterval(() => {
    if (isRunning) syncLatestStatus(false);
  }, 2000);

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      syncLatestStatus(true);
      if (!ws || ws.readyState === WebSocket.CLOSED) connectWebSocket();
      syncScreenWakeLock();
    } else {
      syncScreenWakeLock();
    }
  });
  window.addEventListener("focus", () => syncLatestStatus(true));

  // Run
  connectWebSocket();
  loadInitialData();
  loadActiveMatches();
})();
