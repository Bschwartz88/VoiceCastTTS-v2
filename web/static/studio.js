let appConfig = {};
let speakerLabelRules = null;
let sampleScriptText = "";
let activePreviewAudio = null;
let autoDetectTimer = null;
let nextCharIndex = 4;
let currentWaveformPeaks = null;
let cachedPeaks = null;
let animFrameId = null;

// A drawn icon from the sprite in index.html. Hidden from screen readers: the button's words name it.
function icon(name) {
  return `<svg class="icon" aria-hidden="true" focusable="false"><use href="#i-${name}"></use></svg>`;
}

let narratorConfig = {
  voice: "bm_fable",
  speed: 0.95
};

let characters = [
  { id: "char_1", name: "Speaker 1", prevName: "Speaker 1", gender: "Female", voice: "af_sky", speed: 1.05 },
  { id: "char_2", name: "Speaker 2", prevName: "Speaker 2", gender: "Male", voice: "am_eric", speed: 1.00 },
  { id: "char_3", name: "Speaker 3", prevName: "Speaker 3", gender: "Male", voice: "am_liam", speed: 1.00 }
];

async function establishSession() {
  const fragment = new URLSearchParams(location.hash.slice(1));
  const token = fragment.get('session');
  if (!token) return; // An already authenticated tab can reload using its HttpOnly cookie.
  history.replaceState(null, '', location.pathname + location.search);
  const response = await fetch('/api/session', {
    method: 'POST', headers: {Authorization: `Bearer ${token}`}
  });
  if (!response.ok) throw new Error('Please reopen VoiceCastTTS v2 from its desktop launcher.');
}

async function init() {
  try {
    await establishSession();
    const res = await fetch('/api/config');
    if (!res.ok) throw new Error('Please reopen VoiceCastTTS v2 from its desktop launcher.');
    const data = await res.json();
    appConfig = data.config;
    speakerLabelRules = data.speaker_label_rules;
    aiPrograms = data.ai_programs || [];
    sampleScriptText = data.sample_script || "";
    if (data.user_settings) userSettings = data.user_settings;
    lastSavedSettings = JSON.parse(JSON.stringify(userSettings));
    // The server copy of the theme wins (it survives cleared browser data and port changes).
    if (userSettings.theme && userSettings.theme !== currentThemeChoice()) window.applyTheme(userSettings.theme);
    updateThemeToggle();
    applyAiEnabled();
    applyAiLength();
    applyAiAutoAudio();
    applyAiAutoSplit();
    updateAiPlaceholder();
    applyInboxEnabled();

    narratorConfig = defaultNarrator();

    // Every new session starts with an empty script; "Reset Sample" loads the demo.
    document.getElementById('scriptInput').value = '';
    updateWordCount();

    populateNarratorDropdown();
    setSpeed('Narrator', narratorConfig.speed);

    // The script starts empty, so show the default cast (Speaker 1-3) ready to use.
    renderCharacterCards();
    renderDynamicToolbarTags();

    const mainAudio = document.getElementById('mainAudio');
    mainAudio.loop = false;
    mainAudio.addEventListener('play', () => {
      stopLiveListening();
      updatePlayButton(true);
      startWaveformPlaybackLoop();
    });
    mainAudio.addEventListener('pause', () => {
      updatePlayButton(false);
      stopWaveformPlaybackLoop();
    });
    mainAudio.addEventListener('ended', () => {
      updatePlayButton(false);
      stopWaveformPlaybackLoop();
      drawWaveform(0);
      const curEl = document.getElementById('currentTimeDisplay');
      if (curEl) curEl.innerText = '00:00';
      updateTranscriptHighlight(-1);
    });
    mainAudio.addEventListener('loadedmetadata', () => {
      const totEl = document.getElementById('totalTimeDisplay');
      if (totEl && mainAudio.duration) {
        totEl.innerText = formatTime(mainAudio.duration);
      }
    });
    mainAudio.addEventListener('timeupdate', () => {
      if (!animFrameId && mainAudio.duration) {
        drawWaveform(mainAudio.currentTime / mainAudio.duration);
        const curEl = document.getElementById('currentTimeDisplay');
        if (curEl) curEl.innerText = formatTime(mainAudio.currentTime);
        updateTranscriptHighlight(mainAudio.currentTime);
      }
      updatePositionSlider();
    });

    window.addEventListener('resize', () => {
      if (currentWaveformPeaks) {
        computePeaks();
        const audio = document.getElementById('mainAudio');
        const prog = (audio && audio.duration) ? (audio.currentTime / audio.duration) : 0;
        drawWaveform(prog);
      }
    });

    // Script input listener for word count and debounced auto-detection
    const scriptInput = document.getElementById('scriptInput');
    scriptInput.addEventListener('input', () => {
      updateWordCount();
      clearTimeout(autoDetectTimer);
      autoDetectTimer = setTimeout(() => {
        autoDetectSpeakers(false);
      }, 350);
    });

    scriptInput.addEventListener('paste', () => {
      setTimeout(() => {
        updateWordCount();
        autoDetectSpeakers(false);
      }, 50);
    });

  } catch (err) {
    console.error("Failed to load config:", err);
    showStatus('Open v2 from its desktop launcher', 'The connection could not be authorized. Close this tab and use the VoiceCastTTS v2 desktop icon.', 'error');
  }
}

function populateNarratorDropdown() {
  const select = document.getElementById('voice_Narrator');
  if (!select) return;
  populateDropdownWithOptions(select, narratorConfig.voice);
}

function populateDropdownWithOptions(selectEl, selectedValue) {
  selectEl.innerHTML = '';
  const voices = appConfig.available_popular_voices || [];
  const categories = {};
  voices.forEach(v => {
    const cat = v.category || "🎙️ Additional Voices";
    if (!categories[cat]) categories[cat] = [];
    categories[cat].push(v);
  });

  for (const [catName, catVoices] of Object.entries(categories)) {
    const group = document.createElement('optgroup');
    group.label = catName;
    catVoices.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v.id;
      opt.textContent = `${v.id} (${v.accent} ${v.gender} — ${v.tone})`;
      group.appendChild(opt);
    });
    selectEl.appendChild(group);
  }
  selectEl.value = selectedValue || voices[0]?.id || 'af_heart';
  attachVoicePicker(selectEl);
}

/* Voice Picker: favorites (★) and category folders that can be closed.
   The hidden <select> keeps holding the chosen voice, so the rest of the page is unchanged.
   Both lists are saved on the server (user_settings.json) so they last between sessions. */
const FAVORITES_FOLDER = '★ Favorites';
const NAMES_FOLDER = '✎ My names';

// How a folder is shown: its icon and its name without the symbol. The stored names
// ('★ Favorites', '✎ My names', '🎙️ Additional Voices') stay as they are in the settings.
function folderLabel(name) {
  if (name === FAVORITES_FOLDER) return { icon: 'star-filled', text: 'Favorites' };
  if (name === NAMES_FOLDER) return { icon: 'pencil', text: 'My names' };
  const text = name.replace(/^[^\p{L}\p{N}]+/u, '').trim();
  return { icon: text !== name ? 'mic' : '', text };
}
let userSettings = { favorite_voices: [], closed_voice_folders: [], theme: 'system',
  ai_writer_enabled: false, llm_model: '', llm_base_url: '', llm_timeout_seconds: 180, llm_instructions: {},
  llm_length: 'medium', llm_auto_audio: false, llm_auto_split: false, import_links_enabled: false, inbox_enabled: false, llm_model_prompts: {}, voice_nicknames: {}, default_narrator: {}, voice_folders: {} };
let openPicker = null; // { selectEl, panel, button }

function voiceLabel(v) {
  return `${v.id} (${v.accent} ${v.gender} — ${v.tone})`;
}

function tidyNickname(text) {
  return (text || '').trim().replace(/\s+/g, ' ');
}

function voiceNickname(id) {
  return (userSettings.voice_nicknames || {})[id] || '';
}

function voiceTitle(v) {
  const nick = voiceNickname(v.id);
  return nick ? `${nick}: ${voiceLabel(v)}` : voiceLabel(v);
}

// A voice's label: the nickname in normal text and "id · accent gender" in small text,
// or today's full label when the voice has no nickname.
function fillVoiceLabel(el, v) {
  el.textContent = '';
  const nick = voiceNickname(v.id);
  const main = document.createElement('span');
  main.className = 'vp-main';
  main.textContent = nick || voiceLabel(v);
  el.appendChild(main);
  if (nick) {
    const small = document.createElement('span');
    small.className = 'vp-small';
    small.textContent = `${v.id} · ${v.accent} ${v.gender}`;
    el.appendChild(small);
  }
}

// The voice that already has this nickname (ignoring capitals), other than exceptId.
function nicknameOwner(nickname, exceptId) {
  const wanted = tidyNickname(nickname).toLowerCase();
  const names = userSettings.voice_nicknames || {};
  return Object.keys(names).find(id => id !== exceptId && names[id].toLowerCase() === wanted) || null;
}

// The voice whose nickname is this speaker's name, if that voice is still in the list.
function voiceForSpeakerName(name) {
  const id = nicknameOwner(name, null);
  const voices = appConfig?.available_popular_voices || [];
  return id && voices.some(v => v.id === id) ? id : null;
}

// Saves a nickname (an empty one removes it). Returns '' or a message when the name is taken.
function setVoiceNickname(id, text) {
  const nick = tidyNickname(text).slice(0, 30);
  if (!userSettings.voice_nicknames) userSettings.voice_nicknames = {};
  if (nick) {
    const owner = nicknameOwner(nick, id);
    if (owner) return `${nick} is already the name of ${owner}.`;
    userSettings.voice_nicknames[id] = nick;
  } else {
    delete userSettings.voice_nicknames[id];
  }
  refreshAllVoicePickerButtons();
  saveUserSettings();
  return '';
}

// Turns a voice row into a nickname box: Enter or leaving saves, Esc cancels.
// A box still showing a "taken" message is left alone when the user clicks away: nothing is saved.
function startNicknameEdit(selectEl, panel, row, v) {
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'vp-nick-input';
  input.maxLength = 30;
  input.value = voiceNickname(v.id);
  input.setAttribute('aria-label', `Nickname for ${v.id}`);
  const note = document.createElement('div');
  note.className = 'vp-nick-note';
  note.setAttribute('role', 'status');
  let done = false;
  const finish = (save) => {
    if (done) return;
    if (save) {
      const problem = setVoiceNickname(v.id, input.value);
      if (problem) { note.textContent = problem; return; }
    }
    done = true;
    buildVoicePickerPanel(selectEl, panel);
  };
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); finish(true); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(false); }
  });
  input.addEventListener('input', () => { note.textContent = ''; });  // a changed name may be free now
  input.addEventListener('blur', () => { if (!note.textContent) finish(true); });
  input.addEventListener('click', (e) => e.stopPropagation());
  row.querySelector('.vp-choose').replaceWith(input);
  row.insertAdjacentElement('afterend', note);
  input.focus();
  input.select();
}

// Your own folders (voice_folders in user_settings.json): a voice you move shows there instead of its usual folder.
const FOLDER_NAME_TAKEN = 'That folder name is taken by VoiceCast; pick another.';

function voiceFolder(id) {
  return (userSettings.voice_folders || {})[id] || '';
}

// Folder names compare without their leading symbol and case: "children & youth" is "Children & Youth".
function folderKey(name) {
  return folderLabel(name).text.toLowerCase();
}

function usualFolderOf(v) {
  return v.category || "🎙️ Additional Voices";
}

// folderKey -> the usual folder's name, so a folder you name like a usual one joins it.
function usualFolders() {
  const usual = {};
  (appConfig?.available_popular_voices || []).forEach(v => {
    const key = folderKey(usualFolderOf(v));
    if (!usual[key]) usual[key] = usualFolderOf(v);
  });
  return usual;
}

// Your folders that hold at least one voice that still exists (not the usual folders).
function ownFolderNames() {
  const usual = usualFolders();
  const known = new Set((appConfig?.available_popular_voices || []).map(v => v.id));
  const names = Object.entries(userSettings.voice_folders || {})
    .filter(([id, name]) => known.has(id) && !usual[folderKey(name)]).map(([, name]) => name);
  return [...new Set(names)].sort((a, b) => a.localeCompare(b));
}

// Resolves to a problem to show, or '' when saved. An empty name moves the voice back to its usual folder.
// A name that matches a folder already there in another case ("tier 2") goes into that folder.
// When the settings can't be saved, the move is undone and the reason returned.
async function setVoiceFolder(id, name) {
  let folder = (name || '').split(/\s+/).filter(Boolean).join(' ').slice(0, 30);
  if (folder && ['favorites', 'my names'].includes(folderKey(folder))) return FOLDER_NAME_TAKEN;
  if (folder) {
    const same = usualFolders()[folderKey(folder)]
      || Object.values(userSettings.voice_folders || {}).find(f => folderKey(f) === folderKey(folder));
    if (same) folder = same;
  }
  if (!userSettings.voice_folders) userSettings.voice_folders = {};
  const before = { ...userSettings.voice_folders };
  delete userSettings.voice_folders[id];  // a voice moved (again) goes to the end of its new folder's order
  if (folder) userSettings.voice_folders[id] = folder;
  const result = await saveUserSettings();
  if (result.success) return '';
  // Back to what is saved, not just this call's copy: another move may have failed at the same time.
  userSettings.voice_folders = { ...(lastSavedSettings?.voice_folders || before) };
  return `Not moved: ${result.error || 'the settings could not be saved.'}`;
}

// Shows a problem under a voice's row in the list (the list was just rebuilt).
function showFolderProblem(panel, v, text) {
  const row = panel.querySelector(`.vp-folder:not(.vp-folder-fav):not(.vp-folder-names) .vp-move[data-voice="${CSS.escape(v.id)}"]`)?.closest('.vp-row');
  if (!row) return;
  const note = document.createElement('div');
  note.className = 'vp-nick-note';
  note.setAttribute('role', 'status');
  note.textContent = text;
  row.insertAdjacentElement('afterend', note);
}

// After a move: rebuild the list, put the keyboard back on the moved voice's folder button and say where it went.
function showVoiceMoved(selectEl, panel, v) {
  buildVoicePickerPanel(selectEl, panel);
  const button = panel.querySelector(`.vp-folder:not(.vp-folder-fav):not(.vp-folder-names) .vp-move[data-voice="${CSS.escape(v.id)}"]`);
  if (button) {
    button.focus();
    button.scrollIntoView({ block: 'nearest' });
  }
  const folder = voiceFolder(v.id);
  announce(folder ? `${v.id} moved to ${folder}.` : `${v.id} is back in its usual folder.`);
}

// A small menu under a voice: your folders, New folder…, and Back to its usual folder.
function openFolderMenu(selectEl, panel, row, v) {
  panel.querySelectorAll('.vp-menu').forEach(el => el.remove());
  const menu = document.createElement('div');
  menu.className = 'vp-menu';
  menu.setAttribute('role', 'group');
  menu.setAttribute('aria-label', `Move ${v.id} to`);
  const add = (text, cls, onPick, disabled = false) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = cls;
    b.textContent = text;
    b.disabled = disabled;
    b.addEventListener('click', (e) => {
      e.stopPropagation();
      onPick();
    });
    menu.appendChild(b);
    return b;
  };
  const current = voiceFolder(v.id);
  ownFolderNames().forEach(name => {
    const b = add(name === current ? `${name} ✓` : name, 'vp-menu-folder', async () => {
      const problem = await setVoiceFolder(v.id, name);
      showVoiceMoved(selectEl, panel, v);
      if (problem) showFolderProblem(panel, v, problem);
    }, name === current);
    b.dataset.folder = name;
  });
  add('New folder…', 'vp-new-folder', () => startNewFolder(selectEl, panel, menu, v));
  if (current) {
    add('Back to its usual folder', 'vp-back', async () => {
      const problem = await setVoiceFolder(v.id, '');
      showVoiceMoved(selectEl, panel, v);
      if (problem) showFolderProblem(panel, v, problem);
    });
  }
  menu.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      menu.remove();
      row.querySelector('.vp-move').focus();
    }
  });
  row.insertAdjacentElement('afterend', menu);
  menu.querySelector('button:not(:disabled)')?.focus();
}

// New folder…: a name box like the nickname box (Enter saves, Esc cancels).
function startNewFolder(selectEl, panel, menu, v) {
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'vp-nick-input vp-folder-input';
  input.maxLength = 30;
  input.placeholder = 'Folder name, e.g. Tier 2';
  input.setAttribute('aria-label', `New folder for ${v.id}`);
  const note = document.createElement('div');
  note.className = 'vp-nick-note';
  note.setAttribute('role', 'status');
  input.addEventListener('keydown', async (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      if (!input.value.trim()) return;
      const problem = await setVoiceFolder(v.id, input.value);
      if (problem) { note.textContent = problem; return; }
      showVoiceMoved(selectEl, panel, v);
    } else if (e.key === 'Escape') {
    stopLiveListening();
      e.preventDefault();
      e.stopPropagation();
      showVoiceMoved(selectEl, panel, v);
    }
  });
  input.addEventListener('input', () => { note.textContent = ''; });
  input.addEventListener('click', (e) => e.stopPropagation());
  menu.replaceChildren(input, note);
  input.focus();
}

function isFavoriteVoice(id) {
  return userSettings.favorite_voices.includes(id);
}

function isFolderClosed(name) {
  return userSettings.closed_voice_folders.includes(name);
}

function toggleListItem(list, item) {
  const i = list.indexOf(item);
  if (i === -1) list.push(item); else list.splice(i, 1);
}

// Saves run one at a time, and a click during a save queues one more with the latest
// state, so an older save can never land after a newer one.
let settingsSaving = false;
let settingsSaveAgain = false;
// Resolves once no settings save is running (so a request can't overtake a save just made).
async function settingsSaved() {
  while (settingsSaving) await new Promise(resolve => setTimeout(resolve, 50));
}

let lastSettingsResult = { success: true };
let lastSavedSettings = null;  // the settings as last saved (or loaded): a failed change goes back to these

// Returns {success, error} for the latest save, so the instructions editor can show refusals.
async function saveUserSettings() {
  if (settingsSaving) { settingsSaveAgain = true; await settingsSaved(); return lastSettingsResult; }
  settingsSaving = true;
  let result = { success: true };
  do {
    settingsSaveAgain = false;
    try {
      const sent = JSON.stringify(userSettings);
      const res = await fetch('/api/user_settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: sent
      });
      const data = await res.json();
      result = data.success ? { success: true } : { success: false, error: data.error || 'Not saved.' };
      if (data.success) lastSavedSettings = JSON.parse(sent);
    } catch (err) {
      result = { success: false, error: 'The studio server is not responding.' };
    }
    if (!result.success) console.warn('Could not save settings:', result.error);
  } while (settingsSaveAgain);
  settingsSaving = false;
  lastSettingsResult = result;
  return result;
}

// Shows the chosen voice on the picker button. Called whenever the hidden select's value changes.
function refreshVoicePickerButton(selectEl) {
  const button = selectEl._voicePickerButton;
  if (!button) return;
  const voices = appConfig?.available_popular_voices || [];
  const v = voices.find(x => x.id === selectEl.value);
  const label = button.querySelector('.vp-label');
  if (v) fillVoiceLabel(label, v); else label.textContent = selectEl.value || 'Choose a voice';
  button.querySelector('.vp-fav').innerHTML = v && isFavoriteVoice(v.id) ? icon('star-filled') : '';
}

function refreshAllVoicePickerButtons() {
  document.querySelectorAll('select.voice-native').forEach(refreshVoicePickerButton);
}

function attachVoicePicker(selectEl) {
  if (!selectEl._voicePickerButton) {
    selectEl.classList.add('voice-native');
    // Code elsewhere sets select.value directly; keep the button in step with it.
    const desc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
    Object.defineProperty(selectEl, 'value', {
      get() { return desc.get.call(this); },
      set(v) { desc.set.call(this, v); refreshVoicePickerButton(this); },
      configurable: true
    });

    const picker = document.createElement('div');
    picker.className = 'voice-picker';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'voice-picker-btn';
    button.id = `${selectEl.id}_picker`;
    button.setAttribute('aria-haspopup', 'listbox');
    button.setAttribute('aria-expanded', 'false');
    button.title = 'Choose a voice. Click the star to add a favorite; click a folder to open or close it.';
    button.innerHTML = `<span class="vp-fav"></span><span class="vp-label"></span><span class="vp-caret">${icon('chevron-down')}</span>`;
    button.addEventListener('click', (e) => {
      e.stopPropagation();
      if (openPicker && openPicker.selectEl === selectEl) closeVoicePicker();
      else openVoicePicker(selectEl);
    });
    picker.appendChild(button);
    selectEl.insertAdjacentElement('afterend', picker);
    selectEl._voicePickerButton = button;
  }
  refreshVoicePickerButton(selectEl);
}

// The words of a text for searching: "bm_george" gives "bm_george", "bm" and "george".
function searchWords(text) {
  const lower = text.toLowerCase();
  return [...new Set([...lower.split(/[^\p{L}\p{N}_]+/u), ...lower.split(/[^\p{L}\p{N}]+/u)])].filter(Boolean);
}

// True when every typed word starts a word of the voice's nickname, id, description or folder name
// (so "skip" finds "Skipper" and "male" doesn't find "Female").
function voiceMatches(v, folderName, words) {
  if (!words.length) return true;
  const own = searchWords([voiceNickname(v.id), v.id, v.accent, v.gender, v.tone, folderName].join(' '));
  return words.every(w => own.some(word => word.startsWith(w)));
}

function buildVoicePickerPanel(selectEl, panel) {
  let search = panel.querySelector('.vp-search');
  if (!search) {
    search = document.createElement('input');
    search.type = 'text';
    search.className = 'vp-search';
    search.placeholder = 'Find a voice…';
    search.setAttribute('aria-label', 'Find a voice');
    search.addEventListener('input', () => buildVoicePickerPanel(selectEl, panel));
    search.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && search.value) {  // an empty search lets Esc close the list
        e.preventDefault();
        e.stopPropagation();
        search.value = '';
        buildVoicePickerPanel(selectEl, panel);
      }
    });
    const newList = document.createElement('div');
    newList.className = 'vp-list';
    panel.append(search, newList);
  }
  const list = panel.querySelector('.vp-list');
  list.innerHTML = '';
  const query = search.value.trim();
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const voices = appConfig?.available_popular_voices || [];
  const named = voices.filter(v => voiceNickname(v.id))
    .sort((a, b) => voiceNickname(a.id).toLowerCase().localeCompare(voiceNickname(b.id).toLowerCase()));
  const folders = [[FAVORITES_FOLDER, voices.filter(v => isFavoriteVoice(v.id))], [NAMES_FOLDER, named]];
  const usual = usualFolders();
  const byCategory = {};
  const own = {};  // your own folders, shown after the usual ones
  voices.forEach(v => {
    const moved = voiceFolder(v.id);
    const cat = moved ? usual[folderKey(moved)] : usualFolderOf(v);
    if (!cat) { (own[moved] = own[moved] || []).push(v); return; }
    if (!byCategory[cat]) { byCategory[cat] = []; folders.push([cat, byCategory[cat]]); }
    byCategory[cat].push(v);
  });
  const ownNames = Object.keys(own).sort((a, b) => a.localeCompare(b));
  ownNames.forEach(name => folders.push([name, own[name]]));

  folders.forEach(([name, folderVoices]) => {
    if (words.length) {
      folderVoices = folderVoices.filter(v => voiceMatches(v, name, words));
      if (!folderVoices.length) return;
    }
    const folder = document.createElement('div');
    folder.className = 'vp-folder' + (name === FAVORITES_FOLDER ? ' vp-folder-fav' : '')
      + (name === NAMES_FOLDER ? ' vp-folder-names' : '') + (ownNames.includes(name) ? ' vp-folder-own' : '');
    const closed = !words.length && isFolderClosed(name);
    const head = document.createElement('button');
    head.type = 'button';
    head.className = 'vp-folder-head';
    head.dataset.folder = name;
    head.setAttribute('aria-expanded', String(!closed));
    head.title = closed ? 'Open this folder' : 'Close this folder to hide its voices';
    head.innerHTML = `<span class="vp-arrow">${icon(closed ? 'chevron-right' : 'chevron-down')}</span><span class="vp-name"></span><span class="vp-count"></span>`;
    const shown = ownNames.includes(name) ? { icon: '', text: name } : folderLabel(name);  // your own names as typed
    const nameEl = head.querySelector('.vp-name');
    nameEl.innerHTML = shown.icon ? icon(shown.icon) : '';
    nameEl.append(shown.text);
    head.querySelector('.vp-count').textContent = folderVoices.length;
    head.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleListItem(userSettings.closed_voice_folders, name);
      buildVoicePickerPanel(selectEl, panel);
      saveUserSettings();
    });
    folder.appendChild(head);

    if (!closed) {
      if (!words.length && folderVoices.length === 0 && (name === FAVORITES_FOLDER || name === NAMES_FOLDER)) {
        const empty = document.createElement('div');
        empty.className = 'vp-empty';
        empty.textContent = name === FAVORITES_FOLDER ? 'Click the star next to any voice to add it here.'
                                                      : 'Click the pencil next to any voice to give it a name.';
        folder.appendChild(empty);
      }
      folderVoices.forEach(v => {
        const row = document.createElement('div');
        row.className = 'vp-row' + (v.id === selectEl.value ? ' vp-selected' : '');
        row.setAttribute('role', 'option');
        row.setAttribute('aria-selected', String(v.id === selectEl.value));

        const fav = isFavoriteVoice(v.id);
        const star = document.createElement('button');
        star.type = 'button';
        star.className = 'vp-star' + (fav ? ' vp-on' : '');
        star.dataset.voice = v.id;
        star.innerHTML = icon(fav ? 'star-filled' : 'star');
        star.title = fav ? 'Remove from favorites' : 'Add to favorites';
        star.setAttribute('aria-label', `${star.title}: ${v.id}`);
        star.setAttribute('aria-pressed', String(fav));
        star.addEventListener('click', (e) => {
          e.stopPropagation();
          toggleListItem(userSettings.favorite_voices, v.id);
          buildVoicePickerPanel(selectEl, panel);
          refreshAllVoicePickerButtons();
          saveUserSettings();
        });

        const nick = document.createElement('button');
        nick.type = 'button';
        nick.className = 'vp-nick' + (voiceNickname(v.id) ? ' vp-on' : '');
        nick.dataset.voice = v.id;
        nick.innerHTML = icon('pencil');
        nick.title = voiceNickname(v.id) ? 'Change the nickname' : 'Give this voice a nickname';
        nick.setAttribute('aria-label', `${nick.title}: ${v.id}`);
        nick.addEventListener('click', (e) => {
          e.stopPropagation();
          startNicknameEdit(selectEl, panel, row, v);
        });

        const move = document.createElement('button');
        move.type = 'button';
        move.className = 'vp-move' + (voiceFolder(v.id) ? ' vp-on' : '');
        move.dataset.voice = v.id;
        move.innerHTML = icon('folder');
        move.title = voiceFolder(v.id) ? `In your folder "${voiceFolder(v.id)}": move it` : 'Move to a folder';
        move.setAttribute('aria-label', `Move ${v.id} to a folder`);
        move.setAttribute('aria-haspopup', 'menu');
        move.addEventListener('click', (e) => {
          e.stopPropagation();
          openFolderMenu(selectEl, panel, row, v);
        });

        const choose = document.createElement('button');
        choose.type = 'button';
        choose.className = 'vp-choose';
        choose.dataset.voice = v.id;
        fillVoiceLabel(choose, v);
        choose.title = voiceTitle(v);
        choose.addEventListener('click', (e) => {
          e.stopPropagation();
          selectEl.value = v.id;
          selectEl.dispatchEvent(new Event('change', { bubbles: true }));
          closeVoicePicker();
          selectEl._voicePickerButton.focus();
        });

        row.append(star, nick, move, choose);
        folder.appendChild(row);
      });
    }
    list.appendChild(folder);
  });

  if (words.length && !list.children.length) {
    const none = document.createElement('div');
    none.className = 'vp-empty';
    none.textContent = `No voices match "${query}".`;
    list.appendChild(none);
  }
}

function positionVoicePicker() {
  if (!openPicker) return;
  // The speaker cards are rebuilt as the script changes; drop a list whose card is gone.
  if (!openPicker.button.isConnected) { closeVoicePicker(); return; }
  const rect = openPicker.button.getBoundingClientRect();
  const panel = openPicker.panel;
  const width = Math.min(Math.max(rect.width, 470), window.innerWidth - 16);
  panel.style.width = `${width}px`;
  panel.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - width - 8))}px`;
  const below = window.innerHeight - rect.bottom - 8;
  const above = rect.top - 8;
  if (below >= 240 || below >= above) {
    panel.style.top = `${rect.bottom + 4}px`;
    panel.style.bottom = '';
    panel.style.maxHeight = `${Math.min(380, below - 4)}px`;
  } else {
    panel.style.top = '';
    panel.style.bottom = `${window.innerHeight - rect.top + 4}px`;
    panel.style.maxHeight = `${Math.min(380, above - 4)}px`;
  }
}

function openVoicePicker(selectEl) {
  closeVoicePicker();
  const button = selectEl._voicePickerButton;
  const panel = document.createElement('div');
  panel.className = 'voice-picker-panel';
  panel.id = 'voicePickerPanel';
  panel.setAttribute('role', 'listbox');
  panel.addEventListener('click', (e) => e.stopPropagation());
  buildVoicePickerPanel(selectEl, panel);
  document.body.appendChild(panel);
  button.setAttribute('aria-expanded', 'true');
  openPicker = { selectEl, panel, button };
  positionVoicePicker();
  const selected = panel.querySelector('.vp-selected');
  if (selected) selected.scrollIntoView({ block: 'nearest' });
  panel.querySelector('.vp-search').focus({ preventScroll: true });
}

function closeVoicePicker() {
  if (!openPicker) return;
  openPicker.panel.remove();
  openPicker.button.setAttribute('aria-expanded', 'false');
  openPicker = null;
}

document.addEventListener('click', closeVoicePicker);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && openPicker) {
    e.preventDefault();
    const button = openPicker.button;
    closeVoicePicker();
    button.focus();
  }
});
window.addEventListener('resize', positionVoicePicker);
// Keep the list under its button while the page scrolls (but not when the list itself scrolls).
window.addEventListener('scroll', (e) => {
  if (openPicker && !openPicker.panel.contains(e.target)) positionVoicePicker();
}, true);

/* Speed Control System */
function setSpeed(speakerKey, speedValue) {
  let num = parseFloat(speedValue);
  if (isNaN(num)) num = 1.0;
  num = Math.max(0.50, Math.min(2.00, Math.round(num * 100) / 100));

  if (speakerKey === 'Narrator') {
    narratorConfig.speed = num;
  } else {
    const char = characters.find(c => c.id === speakerKey);
    if (char) char.speed = num;
  }

  const formattedVal = num.toFixed(2);
  const displayVal = (num.toFixed(2).replace(/\.?0+$/, '') || num.toFixed(2)) + 'x';

  // Update badge
  const badge = document.getElementById(`speedBadge_${speakerKey}`);
  if (badge) badge.innerText = displayVal;

  // Update slider
  const slider = document.getElementById(`speedSlider_${speakerKey}`);
  if (slider && parseFloat(slider.value) !== num) slider.value = num;

  // Update numeric input (only if user is not actively typing in it)
  const numInput = document.getElementById(`speedNum_${speakerKey}`);
  if (numInput && document.activeElement !== numInput) {
    numInput.value = formattedVal;
  }

  // Update active preset buttons
  const presetBar = document.getElementById(`speedPresets_${speakerKey}`);
  if (presetBar) {
    presetBar.querySelectorAll('.speed-preset-btn').forEach(btn => {
      const bVal = parseFloat(btn.getAttribute('data-val'));
      if (Math.abs(bVal - num) < 0.01) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    });
  }
  if (speakerKey === 'Narrator') updateNarratorDefaultBtn();
}

function stepSpeed(speakerKey, delta) {
  const current = getSpeed(speakerKey);
  setSpeed(speakerKey, current + delta);
  const numInput = document.getElementById(`speedNum_${speakerKey}`);
  if (numInput) numInput.value = getSpeed(speakerKey).toFixed(2);
}

function onSpeedNumInput(speakerKey, val) {
  if (val === "" || isNaN(val)) return;
  setSpeed(speakerKey, val);
}

function formatSpeedNum(speakerKey) {
  const numInput = document.getElementById(`speedNum_${speakerKey}`);
  if (numInput) {
    numInput.value = getSpeed(speakerKey).toFixed(2);
  }
}

function getSpeed(speakerKey) {
  if (speakerKey === 'Narrator') return narratorConfig.speed || 0.95;
  const char = characters.find(c => c.id === speakerKey);
  return char ? (char.speed || 1.0) : 1.0;
}

function onNarratorVoiceChange() {
  const select = document.getElementById('voice_Narrator');
  if (select) narratorConfig.voice = select.value;
  updateNarratorDefaultBtn();
}

// The Narrator for new scripts: your saved default if its voice still exists, else voice_config.json's, else bm_fable.
function defaultNarrator() {
  const known = new Set((appConfig?.available_popular_voices || []).map(v => v.id));
  const own = userSettings.default_narrator || {};
  if (own.voice && known.has(own.voice)) return { voice: own.voice, speed: own.speed };
  const cfg = appConfig?.default_voices?.Narrator;
  if (cfg?.voice && known.has(cfg.voice)) return { voice: cfg.voice, speed: parseFloat(cfg.speed) || 0.95 };
  return { voice: 'bm_fable', speed: 0.95 };
}

// "Make default", or "Default ✓" (and off) when the Narrator already is the default.
function updateNarratorDefaultBtn() {
  const btn = document.getElementById('narratorDefaultBtn');
  if (!btn) return;
  const d = defaultNarrator();
  const same = d.voice === narratorConfig.voice && Math.abs(d.speed - narratorConfig.speed) < 0.001;
  btn.textContent = same ? 'Default ✓' : 'Make default';
  btn.disabled = same;
}

async function makeNarratorDefault() {
  const before = userSettings.default_narrator;
  userSettings.default_narrator = { voice: narratorConfig.voice, speed: narratorConfig.speed };
  updateNarratorDefaultBtn();
  const result = await saveUserSettings();
  if (!result.success) {
    userSettings.default_narrator = before;
    updateNarratorDefaultBtn();
    showStatus('Default Narrator not saved', result.error || 'The settings could not be saved.');
    return;
  }
  const name = voiceNickname(narratorConfig.voice) || narratorConfig.voice;
  showStatus(`Default Narrator: ${name} at ${narratorConfig.speed.toFixed(2)}x.`, 'New scripts and the sample start with this Narrator.');
}

function onVoiceChange(charId) {
  const select = document.getElementById(`voice_${charId}`);
  const char = characters.find(c => c.id === charId);
  if (select && char) {
    char.voice = select.value;
    const start = voiceEntry(char.voice)?.start_speed;
    if (start) setSpeed(charId, start);  // a voice made with a starting speed (Grandpa a little slower)
  }
}

/* Everyday people: voices in your own folder called "Everyday people" are picked first for new speakers,
   by voice type, in the order you added them. A speaker named like Mom, Dad or Grandpa gets that voice. */
const EVERYDAY_FOLDER = 'everyday people';
const ROLE_NAMES = [
  { voice: 'mom', gender: 'Female', words: /^(mom|mum|mother|mommy|mummy|mama)$/ },
  { voice: 'dad', gender: 'Male', words: /^(dad|father|daddy|papa)$/ },
  { voice: 'grandpa', gender: 'Male', words: /^(grandpa|grandad|granddad|grandfather|gramps)$/ },
];

function voiceEntry(id) {
  return (appConfig?.available_popular_voices || []).find(v => v.id === id);
}

function everydayEntries() {
  return Object.entries(userSettings.voice_folders || {})
    .filter(([, folder]) => folder.toLowerCase() === EVERYDAY_FOLDER)
    .map(([id]) => voiceEntry(id)).filter(Boolean);
}

function everydayVoices(gender) {
  return everydayEntries().filter(v => v.voice_type === gender)
    .map(v => ({ voice: v.id, speed: v.start_speed || 1.0 }));
}

// The first Everyday voice of a type that isn't in `taken` ({gender, voice, speed}), or null.
function everydayPick(gender, taken = new Set()) {
  const pick = everydayVoices(gender).find(o => !taken.has(o.voice));
  return pick ? { gender, ...pick } : null;
}

// The role voices (Mom, Dad, Grandpa) of speakers with those names: kept for them, not given to anyone else.
function roleVoicesFor(names) {
  return new Set(names.map(n => ownRoleVoice(n)?.id).filter(Boolean));
}

// The Everyday voice named for a speaker's role (Mum -> the voice called Mom), or undefined.
function ownRoleVoice(name) {
  const word = (name || '').trim().toLowerCase();
  const role = ROLE_NAMES.find(r => r.words.test(word));
  return role && everydayEntries().find(e => e.id.toLowerCase().replace(/_\d+$/, '') === role.voice);
}

// Mom, Mum, Mother… get the Everyday voice called Mom (any case, an ending like _2 ignored); without one,
// an Everyday voice of the role's type. Null when the name isn't a role or there is no Everyday voice to use.
function roleVoice(name, taken = new Set()) {
  const word = (name || '').trim().toLowerCase();
  const role = ROLE_NAMES.find(r => r.words.test(word));
  if (!role) return null;
  const v = ownRoleVoice(name);
  if (v) return { gender: v.voice_type, voice: v.id, speed: v.start_speed || 1.0 };
  return everydayPick(role.gender, taken);
}

/* Delivery cues: (whispers), [pause 1.5s] … Keep in step with _CUE_GROUPS and _pause_seconds in delivery_cues.py. */
const CUE_WORDS = new Set(['whispers', 'whispering', 'whispered', 'softly', 'quietly', 'shouts', 'shouting',
  'shouted', 'yells', 'yelling', 'yelled', 'angrily', 'excitedly', 'sadly', 'slowly', 'quickly']);

function isCueTag(content) {
  const c = (content || '').trim().toLowerCase();
  return CUE_WORDS.has(c) || /^pauses?(?:\s+[-+]?[\d.][\s\S]*)?$/.test(c);  // not "(pause for effect)"
}

// Replaces cue and pause brackets with a space; other brackets stay (the server drops them when speaking).
function stripCueBrackets(text) {
  return text.replace(/\(([^()]*)\)|\[([^\[\]]*)\]/g, (m, round, square) => (isCueTag(round ?? square) ? ' ' : m));
}

// Removes the speaker's own name written again after the tag ("[Timmy] Timmy: hi", also **Timmy:** and [Timmy]),
// as speakability.drop_repeated_name does before speaking. Leaves the text alone if nothing would be left.
function dropRepeatedName(speaker, text) {
  const name = (speaker || '').trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  if (!name || !text) return text;
  const pattern = new RegExp(`^\\s*(?:\\[\\s*${name}\\s*\\]\\s*:?|\\*\\*\\s*${name}\\s*:?\\s*\\*\\*\\s*:?|${name}\\s*:)\\s*(?=\\S)`, 'i');
  return text.replace(pattern, '');
}

/* Auto-Detect Speakers from Script */
function extractSpeakersFromText(text) {
  if (!text) return { hasNarrator: false, speakers: [] };
  const lines = text.split('\n');
  const seen = new Set();
  const detected = [];
  let hasNarrator = false;

  for (const rawLine of lines) {
    const line = rawLine.trim ? rawLine.trim() : rawLine;
    if (!line || line.startsWith('#')) continue;
    // Only a tag at the start of a line names a speaker (as in voice_cast.parse_script); [whispers] and [pause 2s] don't.
    const match = line.match(/^\[([^\]]+)\]/);
    if (!match) continue;
    if (isCueTag(match[1])) continue;
    const name = cleanSpeakerTag(match[1]).name;
    if (!name) continue;
    if (name.toLowerCase() === 'narrator') {
      hasNarrator = true;
    } else {
      const lower = name.toLowerCase();
      if (!seen.has(lower)) {
        seen.add(lower);
        detected.push(name);
      }
    }
  }
  return { hasNarrator, speakers: detected };
}

// Hints suggest a voice type, never overwrite a saved cast or nickname.
function speakerNameType(name) {
  const text = (name || '').toLowerCase();
  if (/^\s*(mrs|ms|miss)(?:\.\s*|\s+)/.test(text)) return 'Female';
  if (/^\s*mr(?:\.\s*|\s+)/.test(text)) return 'Male';
  if (/\b(child|kid|boy|girl|timmy|billy|andy|toddler|youth)\b/.test(text)) return 'Child';
  if (/\b(alice|sarah|emma|bella|lily|rose|mary|elaine|mom|mum|mother|woman|princess|queen|lady|sister|daughter|aunt|grandma)\b/.test(text)) return 'Female';
  if (/\b(bob|adam|michael|charlie|john|tom|george|jerry|kramer|dad|father|man|king|prince|lord|brother|son|uncle|grandpa)\b/.test(text)) return 'Male';
  return null;
}

function guessGenderAndVoice(name, index, taken = new Set()) {
  const role = roleVoice(name, taken);
  if (role) return role;
  const gender = speakerNameType(name);
  if (gender) {
    const pick = voiceForType(gender, index, taken);
    if (pick) return pick;
  }
  const numbered = speakerNumber(name);
  const slot = numbered ? numbered - 1 : index;
  const known = new Set((appConfig?.available_popular_voices || []).map(v => v.id));
  const options = FALLBACK_CAST.filter(v => !known.size || known.has(v.voice));
  const preferred = FALLBACK_CAST[slot % FALLBACK_CAST.length];
  const pick = options.includes(preferred) && !taken.has(preferred.voice) ? preferred
    : options.find(v => !taken.has(v.voice)) || options[slot % options.length] || preferred;
  return {...pick};
}

// Distinct voices for speakers whose names don't suggest a voice type (such as "Speaker 1").
const FALLBACK_CAST = [
  { gender: "Female", voice: "af_sky", speed: 1.05 },
  { gender: "Male", voice: "am_eric", speed: 1.00 },
  { gender: "Male", voice: "am_liam", speed: 1.00 },
  { gender: "Female", voice: "af_bella", speed: 1.00 },
  { gender: "Male", voice: "am_adam", speed: 1.00 },
  { gender: "Female", voice: "bf_emma", speed: 1.00 },
  { gender: "Male", voice: "bm_daniel", speed: 1.00 },
  { gender: "Female", voice: "af_nicole", speed: 1.00 },
];

// 3 for "Speaker 3" (any capitalization, space optional), otherwise null.
function speakerNumber(name) {
  const m = /^speaker\s*(\d{1,3})$/i.exec((name || '').trim());
  return m ? parseInt(m[1], 10) : null;
}

function autoDetectSpeakers(isManual = false) {
  const scriptText = document.getElementById('scriptInput').value;
  const { hasNarrator, speakers } = extractSpeakersFromText(scriptText);
  if (scriptText.length > 2_000_000 || speakers.length > 199 || speakers.some(name => name.length > 100)) {
    showStatus('Too many speakers', 'Use at most 199 characters plus the narrator, with names up to 100 characters.', 'error');
    return;
  }

  const countBadge = document.getElementById('detectedCountBadge');
  if (countBadge) {
    const total = (hasNarrator ? 1 : 0) + speakers.length;
    if (total > 0) {
      countBadge.innerText = `${total}`;
      countBadge.title = `${total} speaker${total === 1 ? '' : 's'} found`;
      setShown(countBadge, true);
    } else {
      setShown(countBadge, false);
    }
  }

  if (speakers.length === 0 && !isManual) {
    renderDynamicToolbarTags();
    return;
  }

  const updatedCharacters = [];
  // Everyday voices already given out, plus the ones kept for speakers named Mom, Dad or Grandpa.
  const taken = new Set([...roleVoicesFor(speakers),
    ...characters.filter(c => speakers.some(n => n.toLowerCase() === cleanSpeakerTag(c.name).name.toLowerCase())).map(c => c.voice)]);
  speakers.forEach((spkName, idx) => {
    const existing = characters.find(c => cleanSpeakerTag(c.name).name.toLowerCase() === spkName.toLowerCase());
    if (existing) {
      existing.name = spkName; // Keep exact casing from script
      updatedCharacters.push(existing);
    } else {
      const guess = guessGenderAndVoice(spkName, idx, taken);
      taken.add(voiceForSpeakerName(spkName) || guess.voice);
      updatedCharacters.push({
        id: `char_${nextCharIndex++}`,
        name: spkName,
        prevName: spkName,
        gender: guess.gender,
        voice: voiceForSpeakerName(spkName) || guess.voice,
        speed: guess.speed
      });
    }
  });

  // Update character list
  if (isManual || updatedCharacters.length > 0) {
    characters = updatedCharacters;
  }

  renderCharacterCards();
  renderDynamicToolbarTags();

  if (isManual) {
    const totalFound = (hasNarrator ? 1 : 0) + speakers.length;
    const msg = totalFound > 0
      ? `✓ Auto-detected ${totalFound} speaker(s): ${hasNarrator ? '[Narrator], ' : ''}${speakers.map(s => `[${s}]`).join(', ')}`
      : "No bracketed [SpeakerName] tags found in script.";
    document.getElementById('progressStatus').innerText = msg;
  }
}

function manualDetectSpeakers() {
  autoDetectSpeakers(true);
}

// Single-quoted dialogue ('Run,' or ‘Run,’) becomes double-quoted for Format Prose. A quote opens only after a
// space or the line start and closes only before a space, punctuation or the line end, so apostrophes (Let's,
// It’s, girls') stay as they are.
function singleQuotesToDouble(text) {
  return text.replace(/(^|[\s(\[—–-])['‘](?=\S)([\s\S]*?\S)['’](?=$|[\s.,;:!?)\]—–-])/g, '$1"$2"');
}

// The voices to send with the script: the Narrator and each card. A card renamed without "Update … in script"
// still voices the tag the script uses (its old name), unless another card has that name now.
function voicesPayload() {
  const voices = {};

  // Narrator
  const narrVoiceEl = document.getElementById('voice_Narrator');
  voices['Narrator'] = {
    voice: narrVoiceEl ? narrVoiceEl.value : narratorConfig.voice,
    speed: narratorConfig.speed || 0.95
  };

  // All dynamic characters
  characters.forEach(c => {
    const nameInput = document.getElementById(`name_${c.id}`);
    const voiceSelect = document.getElementById(`voice_${c.id}`);
    const genderSelect = document.getElementById(`gender_${c.id}`);

    const name = (nameInput ? nameInput.value.trim() : "") || c.name;
    const voice = voiceSelect ? voiceSelect.value : c.voice;
    const speed = getSpeed(c.id);
    const gender = genderSelect ? genderSelect.value : c.gender;

    voices[name] = {
      voice: voice,
      speed: speed,
      gender: gender
    };
  });
  characters.forEach(c => {
    const nameInput = document.getElementById(`name_${c.id}`);
    const name = (nameInput ? nameInput.value.trim() : "") || c.name;
    if (c.prevName && !(c.prevName in voices)) voices[c.prevName] = voices[name];
  });
  // Older saved cards may include annotations in their names. Preserve their chosen voices.
  for (const name of Object.keys(voices)) {
    const canonical = cleanSpeakerTag(name).name;
    if (!Object.hasOwn(voices, canonical)) voices[canonical] = voices[name];
  }
  // Keep raw tag aliases for older projects and cards renamed before formatting.
  for (const line of document.getElementById('scriptInput').value.split('\n')) {
    const tag = line.trim().match(/^\[([^\]]+)\]/);
    if (!tag || isCueTag(tag[1])) continue;
    const canonical = cleanSpeakerTag(tag[1]).name;
    if (Object.hasOwn(voices, canonical) && !Object.hasOwn(voices, tag[1])) voices[tag[1]] = voices[canonical];
  }
  return voices;
}

/* Auto-Format Prose into Multi-Speaker Script */
// Who speaks a quote: the tag after it ("said Tom", "Tom replied", "said the old man"), else a sentence just before
// it ("Aunt Polly frowned.", "He said,"). Speaking words only: "nodded" or "smiled" is something done, not said.
const SPEECH_VERBS = ['said', 'says', 'asked', 'asks', 'replied', 'answered', 'called', 'cried', 'shouted', 'yelled',
  'screamed', 'shrieked', 'whispered', 'murmured', 'muttered', 'mumbled', 'sighed', 'laughed', 'giggled', 'chuckled',
  'groaned', 'moaned', 'exclaimed', 'added', 'agreed', 'admitted', 'explained', 'began', 'continued', 'insisted',
  'warned', 'snapped', 'hissed', 'growled', 'grumbled', 'pleaded', 'begged', 'demanded', 'declared', 'announced',
  'suggested', 'squeaked', 'gasped', 'breathed', 'sobbed', 'interrupted', 'protested', 'teased', 'wondered', 'offered',
  'promised', 'repeated', 'responded', 'remarked', 'told', 'barked', 'roared', 'stammered', 'stuttered', 'chirped',
  'squealed', 'boomed', 'croaked', 'grunted', 'piped', 'called out'].join('|');
const NAME_TITLES = 'Mr|Mrs|Ms|Miss|Mister|Dr|Doctor|Aunt|Auntie|Uncle|Grandma|Grandpa|Granny|Nana|Captain|Professor|' +
  'Sir|Lady|King|Queen|Prince|Princess|Officer|Sergeant';
const WHO = `(?:(?:${NAME_TITLES})\\.?\\s+)?[A-Z][\\w'’-]*(?:\\s+[A-Z][\\w'’-]*)?` +
  `|(?:[Tt]he|[Hh]is|[Hh]er|[Tt]heir|[Mm]y|[Oo]ur)\\s+[a-z][a-z'’-]*(?:\\s+[a-z][a-z'’-]*)?` +
  `|he|she|they|we|you|someone|somebody|everyone`;
const SPEECH_TAG_AFTER = new RegExp(`^(?:(?:${SPEECH_VERBS})\\s+(${WHO})|(${WHO})\\s+(?:[a-z]+ly\\s+)?(?:${SPEECH_VERBS}))\\b`);
const SPEECH_TAG_END = new RegExp(`(?:(${WHO})\\s+(?:[a-z]+ly\\s+)?(?:${SPEECH_VERBS})|(?:${SPEECH_VERBS})\\s+(${WHO}))[\\s,.:;—–-]*$`);
const BEAT_START = new RegExp(`^(${WHO})\\s+[a-z]`);
const NOT_NAMES = new Set(['then', 'but', 'and', 'so', 'when', 'as', 'this', 'that', 'there', 'here', 'now', 'just',
  'still', 'finally', 'suddenly', 'yes', 'no', 'oh', 'well', 'it', 'i', 'a', 'an', 'the', 'after', 'before', 'once',
  'soon', 'later', 'meanwhile', 'outside', 'inside', 'everyone', 'nobody', 'today', 'tonight', 'if', 'what', 'why',
  'how', 'where', 'who', 'while', 'with', 'at', 'in', 'on', 'for']);
const PRONOUN_WORDS = new Set(['he', 'she', 'they', 'we', 'you', 'someone', 'somebody', 'everyone']);
const PHRASE_STOP = new Set(['and', 'as', 'with', 'to', 'from', 'at', 'in', 'on', 'then', 'who', 'while', 'but', 'so']);

// {name} or {pronoun: true} for the words a tag names, or null when they aren't a speaker ("Then", "the and").
function speakerOf(words) {
  const text = (words || '').replace(/\s+/g, ' ').trim();
  const lower = text.toLowerCase();
  if (PRONOUN_WORDS.has(lower)) return { pronoun: true };
  const phrase = lower.match(/^(?:the|his|her|their|my|our)\s+(.+)$/);
  if (phrase) {  // "the old man" → Old man, "her mother" → Mother
    const kept = [];
    for (const w of phrase[1].split(' ')) {
      if (PHRASE_STOP.has(w) || w.endsWith('ly') || new RegExp(`^(?:${SPEECH_VERBS})$`).test(w)) break;
      kept.push(w);
    }
    if (!kept.length) return null;
    const name = kept.join(' ');
    return { name: name.charAt(0).toUpperCase() + name.slice(1) };
  }
  if (NOT_NAMES.has(lower.split(' ')[0])) return null;
  return { name: text };
}

// Cue brackets in a piece of narration, kept when the narration itself isn't read.
function cuesIn(text) {
  return (text.match(/\(([^()]*)\)|\[([^\[\]]*)\]/g) || []).filter(m => isCueTag(m.slice(1, -1))).join(' ');
}

// Narration just after a quote: who the tag names, and the narration left once a bare tag ("said Tom.") is taken out
// (empty for a bare tag; the whole text when the tag goes on, as in "Sarah murmured, and took his hand").
function speechTagAfter(text) {
  const plain = stripCueBrackets(text).replace(/\s+/g, ' ').replace(/^[\s,—–-]+/, '');
  const m = plain.match(SPEECH_TAG_AFTER);
  const who = m && speakerOf(m[1] ?? m[2]);
  if (!who) return null;
  const rest = plain.slice(m[0].length).replace(/^\s*,?\s*[a-z]+ly\b/, '');
  if (/^[\s.,!?;:…—–-]*$/.test(rest)) return { who, rest: cuesIn(text) };
  const sentence = rest.match(/^\s*[.!?…]+\s+/);
  return { who, rest: sentence ? `${rest.slice(sentence[0].length)} ${cuesIn(text)}`.trim() : text };
}

// Narration just before a quote: its last sentence may name the speaker ("Aunt Polly frowned.", "He said,").
// bare: the narration is nothing but that tag, so it needn't be read.
function speechTagBefore(text) {
  const plain = stripCueBrackets(text).replace(/\s+/g, ' ').trim();
  if (!plain) return null;
  const sentences = plain.split(/(?<=[.!?…])\s+/);
  const last = sentences[sentences.length - 1];
  let m = last.match(SPEECH_TAG_END);
  if (m) {
    const who = speakerOf(m[1] ?? m[2]);
    // "Anna laughed." is something to hear; "He said," or "Tom said." only says who speaks next.
    const onlyTag = /[,:]\s*$/.test(last) || /\b(?:said|says|asked|asks)\b/.test(last);
    return who && { who, bare: sentences.length === 1 && m.index === 0 && onlyTag };
  }
  m = last.match(BEAT_START);
  const who = m && speakerOf(m[1]);
  return who && { who, bare: false };
}

// "[Speaker 1 (Lila)]" names Lila; "[Ben (gruffly)]" is Ben with a delivery note.
function cleanSpeakerTag(raw) {
  const tag = typeof raw === 'string' ? raw.replace(/[\[\]*]/g, '').replace(/\s+/g, ' ').trim() : '';
  if (!speakerLabelRules) return { name: tag, cue: '' };
  let name = tag;
  let notes = [];
  const first = tag.indexOf('(');
  if (first > 0) {
    let depth = 0, start = 0, valid = true;
    for (let i = first; i < tag.length; i++) {
      const ch = tag[i];
      if (ch === '(') { if (depth === 0) start = i + 1; depth++; }
      else if (ch === ')') {
        depth--;
        if (depth < 0) { valid = false; break; }
        if (depth === 0) notes.push(tag.slice(start, i).trim());
      } else if (depth === 0 && !/\s/.test(ch)) { valid = false; break; }
    }
    if (valid && depth === 0 && notes.length) name = tag.slice(0, first).trim();
    else notes = [];
  }
  const rules = speakerLabelRules;
  if (new RegExp(rules.numbered, 'i').test(name) && notes.length &&
      new RegExp(rules.alias_name).test(notes[0]) && !new RegExp(rules.metadata, 'i').test(notes[0]) &&
      !Object.hasOwn(rules.delivery_aliases, notes[0].toLowerCase())) name = notes.shift();
  if (name.toLowerCase() === 'narrator') name = 'Narrator';
  const cue = notes.filter(Boolean).map(n => `(${Object.hasOwn(rules.delivery_aliases, n.toLowerCase()) ? rules.delivery_aliases[n.toLowerCase()] : n})`).join(' ');
  return { name, cue };
}

// Some models answer in records ({"speaker": "Ben", "text": "Hi"}, often broken); each becomes a [Ben] Hi line.
const RECORD_KEY = /[{,]?\s*[“"”]\s*(speaker|character|name|text|line|dialogue)\s*[“"”]\s*[:：]/gi;
function recordsToLines(text) {
  const keys = [...text.matchAll(RECORD_KEY)];
  const kinds = keys.map(k => (/^(?:text|line|dialogue)$/i.test(k[1]) ? 'said' : 'who'));
  if (!kinds.includes('who') || !kinds.includes('said')) return text;
  const valueAt = (i) => text.slice(keys[i].index + keys[i][0].length, i + 1 < keys.length ? keys[i + 1].index : text.length)
    .replace(/\s+/g, ' ').replace(/^[\s“"”]+/, '').replace(/[\s,{}\[\]]+$/, '').replace(/[“"”]$/, '').trim();
  const out = [];
  let before = text.slice(0, keys[0].index).replace(/[\s{}\[\],]+$/, '');
  if (/["“”]$/.test(before) && quoteLeftOpen(before.split('\n').pop())) before = before.slice(0, -1);  // the record's "
  if (before.trim()) out.push(before);
  let who = null, said = null;
  keys.forEach((_, i) => {
    const value = valueAt(i);
    if (kinds[i] === 'who') {
      if (said !== null) { if (said) out.push(`[${value || 'Narrator'}] ${said}`); said = null; } else who = value || 'Narrator';
    } else if (who !== null) {
      if (value) out.push(`[${who}] ${value}`);
      who = null;
    } else said = value;
  });
  if (said) out.push(`[Narrator] ${said}`);
  return out.join('\n');
}

const QUOTE_CLOSERS = { '“': '”', '"': '"', '«': '»' };
const TAG_AND_TEXT = /^(\[[^\]]+\]\s*)?(.*)$/;

// More quote marks open than close.
function quoteLeftOpen(text) {
  const count = (re) => (text.match(re) || []).length;
  return count(/"/g) % 2 === 1 || count(/“/g) > count(/”/g) || count(/«/g) > count(/»/g);
}

// A speech that goes on into the next paragraph has no closing quote there; the next paragraph opens with a quote.
// The two become one line, so the speech keeps one speaker.
function joinContinuedSpeech(lines) {
  const out = [...lines];
  for (let i = 0; i < out.length; i++) {
    const text = out[i].trim().match(TAG_AND_TEXT)[2];
    if (!/^[“"«]/.test(text) || !quoteLeftOpen(text)) continue;
    let j = i + 1;
    while (j < out.length && !out[j].trim()) j++;
    const next = j < out.length ? out[j].trim().match(TAG_AND_TEXT)[2] : '';
    if (!/^[“"«]/.test(next)) continue;
    out.splice(i, j - i + 1, `${out[i].trimEnd()}${QUOTE_CLOSERS[text[0]]} ${next}`);
    i--;  // the joined line may go on again
  }
  return out;
}

// Drops quote marks that have no partner (a closing mark with no opening one, an opening one never closed,
// an odd straight one at either end), and the full stop after "?" or "!" that this can leave.
function tidyQuoteMarks(text) {
  const chars = [...text];
  const drop = new Set();
  const open = [];
  chars.forEach((ch, i) => {
    if (ch === '“' || ch === '«') open.push(i);
    else if (ch === '”' || ch === '»') { if (open.length) open.pop(); else drop.add(i); }
  });
  open.forEach(i => drop.add(i));
  let out = drop.size ? chars.filter((_, i) => !drop.has(i)).join('') : text;
  if ((out.match(/"/g) || []).length % 2 === 1) {
    if (/^\s*"/.test(out)) out = out.replace('"', '');
    else if (/"[\s.,!?]*$/.test(out)) out = out.replace(/"(?=[^"]*$)/, '');
  }
  if (out === text) return text;
  return out.replace(/([!?…])\.+(?=\s|$)/g, '$1').trim();
}

// Splits lines that mix narration and quoted dialogue into [Narrator] and [Speaker] lines.
// Returns the parts (an empty string between them), how many parts were made and how many lines changed.
function formatProseText(rawText) {
  const lines = joinContinuedSpeech(recordsToLines(rawText).split('\n'));
  const formattedBlocks = [];
  let totalChanges = 0;
  let changedLines = 0;
  const quotesRegex = /([“"«][^”"»]*[”"»])/;
  let recent = [];  // the latest speakers, most recent last: "she said" in a back-and-forth is the one before last
  const spoke = (name) => {
    if (/^(?:narrator|speaker)$/i.test(name)) return;
    recent = recent.filter(n => n !== name);
    recent.push(name);
  };

  for (let i = 0; i < lines.length; i++) {
    const rawLine = lines[i];
    const trimmed = rawLine.trim();

    if (!trimmed) {
      if (formattedBlocks.length > 0 && formattedBlocks[formattedBlocks.length - 1] !== '') {
        formattedBlocks.push('');
      }
      continue;
    }

    if (trimmed.startsWith('#')) {
      formattedBlocks.push(trimmed);
      formattedBlocks.push('');
      recent = [];  // a new chapter starts a new conversation
      continue;
    }

    const tagMatch = trimmed.match(/^\[([^\]]+)\]\s*(.*)$/);
    let explicitSpeaker = null;
    let content = trimmed;

    if (tagMatch && !isCueTag(tagMatch[1])) {
      const tag = cleanSpeakerTag(tagMatch[1]);
      explicitSpeaker = tag.name;
      content = tagMatch[2].trim();
      const withoutName = dropRepeatedName(explicitSpeaker, dropRepeatedName(tagMatch[1], content));
      if (withoutName !== content) {
        content = withoutName;
        totalChanges++;
      }
      if (tag.cue) content = `${tag.cue} ${content}`.trim();
    }
    content = tidyQuoteMarks(content.replace(/^[:：]\s*/, ''));

    if (!/[“"«]/.test(content)) content = singleQuotesToDouble(content);  // 'Run,' he said. (not inside "...")
    const hasQuotes = /[“"«][^”"»]*[”"»]/.test(content);
    if (!hasQuotes) {
      const kept = !explicitSpeaker ? content : content ? `[${explicitSpeaker}] ${content}` : trimmed;
      if (kept !== trimmed) changedLines++;
      if (explicitSpeaker) spoke(explicitSpeaker);
      formattedBlocks.push(kept);
      formattedBlocks.push('');
      continue;
    }

    changedLines++;
    // A [Narrator] tag names who tells the story, not who speaks the quotes: look for "said Rose" as usual.
    const narratorTag = explicitSpeaker && explicitSpeaker.toLowerCase() === 'narrator';

    // The line's parts in order: quotes, and narration with what it says about who speaks them.
    const parts = [];
    for (const rawToken of content.split(quotesRegex)) {
      const token = rawToken.trim();
      if (!token) continue;
      const isQuote = (token.startsWith('"') && token.endsWith('"')) ||
                      (token.startsWith('“') && token.endsWith('”')) ||
                      (token.startsWith('«') && token.endsWith('»'));
      parts.push(isQuote ? { quote: token.slice(1, -1).trim() } : { narr: token });
    }
    const quotes = parts.filter(p => p.quote !== undefined);
    parts.forEach((p, k) => {
      if (p.narr === undefined) return;
      let text = p.narr;
      const before = k > 0 && parts[k - 1].quote !== undefined ? parts[k - 1] : null;
      const after = k + 1 < parts.length && parts[k + 1].quote !== undefined ? parts[k + 1] : null;
      if (before) {
        const tag = speechTagAfter(text);
        if (tag) {
          before.tag = tag.who;
          p.bareOf = before;  // read only when the quote's speaker isn't found
          p.short = tag.rest;
          text = tag.rest;
        }
      }
      if (after) {
        const tag = speechTagBefore(text);
        if (tag) {
          after.beat = tag.who;
          if (tag.bare) { p.bareOf = after; p.short = cuesIn(p.narr); }
        }
      }
    });

    // Who speaks each quote: the line's own speaker tag; else the named ones in it; else, in a back-and-forth,
    // the speaker before last; else as before (Speaker, or the Narrator for a [Narrator] line).
    const nameOf = (q) => (q.tag && q.tag.name) || (q.beat && q.beat.name) || null;
    const named = quotes.map(nameOf);
    const found = new Set(named.filter(Boolean));
    quotes.forEach((q, n) => {
      if (explicitSpeaker && !narratorTag) q.speaker = explicitSpeaker;
      else if (found.size === 1) q.speaker = [...found][0];
      else if (found.size > 1) {
        q.speaker = named[n] || named.slice(0, n).reverse().find(Boolean) || named.slice(n).find(Boolean);
      } else {
        q.speaker = (recent.length >= 2 && recent[recent.length - 2]) || (narratorTag ? 'Narrator' : 'Speaker');
      }
    });
    quotes.forEach(q => spoke(q.speaker));

    let pendingCue = '';           // a cue standing alone between parts goes in front of the next part
    const firstBlock = formattedBlocks.length;
    const withCue = (text) => {
      const out = pendingCue ? `${pendingCue} ${text}` : text;
      pendingCue = '';
      return out;
    };

    for (const part of parts) {
      if (part.quote !== undefined) {
        let quoteText = part.quote;
        if (!quoteText) continue;

        quoteText = quoteText.charAt(0).toUpperCase() + quoteText.slice(1);
        if (quoteText.endsWith(',')) {
          quoteText = quoteText.slice(0, -1).trim() + '.';
        } else if (!/[.!?…]$/.test(quoteText)) {
          quoteText += '.';
        }

        formattedBlocks.push(`[${part.speaker}] ${withCue(quoteText)}`);
        formattedBlocks.push('');
        totalChanges++;
      } else {
        // A bare speech tag isn't read once its quote has a real speaker: the voice says who it is.
        const found = part.bareOf && !/^(?:narrator|speaker)$/i.test(part.bareOf.speaker);
        let narrText = found ? part.short : part.narr;
        if (!/[\p{L}\p{N}]/u.test(stripCueBrackets(narrText))) {  // only cues: keep them for the next part
          const cue = narrText.replace(/^[,\s—–\-:]+/, '').replace(/[,\s—–\-:.]+$/, '').trim();
          if (cue) pendingCue = pendingCue ? `${pendingCue} ${cue}` : cue;
          continue;
        }
        // Cues at the end of the narration ("Tom said. (whispers)") belong to the next quote.
        let trailing, movedCue = '';
        while ((trailing = narrText.match(/\s*(\(([^()]*)\)|\[([^\[\]]*)\])[\s,.—–\-:]*$/)) &&
               isCueTag(trailing[2] ?? trailing[3])) {
          movedCue = movedCue ? `${trailing[1]} ${movedCue}` : trailing[1];
          narrText = narrText.slice(0, trailing.index);
        }
        narrText = narrText.replace(/^[,\s—–\-:]+/, '').replace(/[,\s—–\-:]+$/, '').trim();
        if (!narrText) continue;

        narrText = narrText.charAt(0).toUpperCase() + narrText.slice(1);
        if (!/[.!?…]$/.test(narrText)) {
          narrText += '.';
        }

        formattedBlocks.push(`[Narrator] ${withCue(narrText)}`);
        formattedBlocks.push('');
        totalChanges++;
        pendingCue = movedCue;
      }
    }
    if (pendingCue) {  // a cue after the last part: keep it on that part rather than lose it
      const last = formattedBlocks.length - 2;  // each part is followed by an empty block
      if (last >= firstBlock) formattedBlocks[last] += ` ${pendingCue}`;
      else formattedBlocks.push(`[Narrator] ${pendingCue}`, '');
    }
  }
  return { blocks: formattedBlocks, totalChanges, changedLines };
}

// The Script Assistant's automatic split: like Format Prose, but keeps one line per row (no blank lines).
function splitNarrationAndDialogue(text) {
  const { blocks, changedLines } = formatProseText(text);
  return { text: blocks.filter(b => b !== '').join('\n'), changedLines };
}

function formatProseScript() {
  const textarea = document.getElementById('scriptInput');
  if (!textarea) return;
  const rawText = textarea.value;
  if (!rawText.trim()) return;

  const { blocks: formattedBlocks } = formatProseText(rawText);
  const resultText = formattedBlocks
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  const changed = resultText !== rawText.trim();
  const lineCount = formattedBlocks.filter(b => b !== '').length;

  textarea.value = resultText;
  updateWordCount();

  // Auto-detect speakers so character cards and toolbar tag buttons reflect the new script
  autoDetectSpeakers(true);

  const btn = document.getElementById('btnFormatProse');
  if (btn && changed) {
    if (!btn.dataset.label) btn.dataset.label = btn.innerHTML;  // a second press while it shows ✓ keeps the real label
    btn.innerHTML = `✓ Formatted (${lineCount} lines)`;
    btn.style.borderColor = 'var(--success)';
    btn.style.color = 'var(--success)';
    clearTimeout(btn._restore);
    btn._restore = setTimeout(() => {
      btn.innerHTML = btn.dataset.label;
      btn.style.borderColor = '';
      btn.style.color = '';
    }, 2200);
  }

  const statusEl = document.getElementById('progressStatus');
  if (statusEl) {
    statusEl.innerText = changed ? `✓ Formatted into ${lineCount} lines, each with its speaker.`
      : 'Nothing to change: every line already has its speaker.';
  }
}

function renderDynamicToolbarTags() {
  const container = document.getElementById('dynamicTagButtons');
  if (!container) return;
  container.innerHTML = '';

  // Narrator tag
  const makeTagButton = (name, spkClass) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'tag-btn';
    btn.title = `Insert [${name}] at the cursor`;
    btn.append(makeSpeakerAvatar(name, spkClass));
    const label = document.createElement('span');
    label.className = 'tag-label';
    label.innerText = `[${name}]`;
    btn.append(label);
    btn.onmousedown = (e) => e.preventDefault();
    return btn;
  };

  const narrBtn = makeTagButton('Narrator', 'spk-narrator');
  narrBtn.onclick = () => insertTagByName('Narrator');
  container.appendChild(narrBtn);

  // Character tags
  characters.forEach((c, idx) => {
    const btn = makeTagButton(c.name, characterSpeakerClass(idx));
    btn.id = `tagBtn_${c.id}`;
    btn.onclick = () => insertTagByName(c.name);
    container.appendChild(btn);
  });
}

function renderCharacterCards() {
  const container = document.getElementById('characterCardsContainer');
  if (!container) return;
  if (activePreview && container.contains(activePreview.button)) stopPreview(); // its Stop button is being rebuilt

  // Save currently active element to restore focus if user is typing
  const activeElId = document.activeElement ? document.activeElement.id : null;
  const activeSelStart = document.activeElement?.selectionStart;
  const activeSelEnd = document.activeElement?.selectionEnd;

  container.innerHTML = '';

  characters.forEach((c, idx) => {
    const spkClass = characterSpeakerClass(idx);
    const card = document.createElement('div');
    card.className = `role-row ${spkClass}`;
    card.id = `card_${c.id}`;

    card.innerHTML = `
      <div class="role-header">
        <div class="role-ident">
          <span class="spk-avatar ${spkClass}" id="avatar_${c.id}" aria-hidden="true">${escapeHtml(speakerInitial(c.name))}</span>
          <input type="text" id="name_${c.id}" class="char-name-input" value="${escapeHtml(c.name)}" placeholder="Speaker Name" data-input="onCharacterNameChange" data-speaker="${c.id}" title="Click to rename this speaker" aria-label="Speaker name">
          <select id="gender_${c.id}" class="char-gender-select" data-change="onCharacterGenderChange" data-speaker="${c.id}" title="Voice type, used to suggest a fitting voice" aria-label="Voice type">
            <option value="Female" ${c.gender === 'Female' ? 'selected' : ''}>Female</option>
            <option value="Male" ${c.gender === 'Male' ? 'selected' : ''}>Male</option>
            <option value="Child" ${c.gender === 'Child' ? 'selected' : ''}>Child / Youth</option>
            <option value="Neutral" ${c.gender === 'Neutral' ? 'selected' : ''}>Neutral</option>
          </select>
          <span id="renameHelper_${c.id}" class="rename-helper hidden" data-click="renameInScript" data-speaker="${c.id}"></span>
          <button type="button" class="btn-remove-speaker" data-click="removeSpeakerCard" data-speaker="${c.id}" title="Remove this speaker card" aria-label="Remove ${escapeHtml(c.name)}">×</button>
        </div>
        <span id="speedBadge_${c.id}" class="speed-badge">${c.speed.toFixed(2)}x</span>
      </div>
      <div class="voice-select-bar">
        <select id="voice_${c.id}" data-change="onVoiceChange" data-speaker="${c.id}" aria-label="Voice"></select>
        <button type="button" class="btn-sample btn-icon" id="sampleBtn_${c.id}" data-click="playVoiceSample" data-speaker="${c.id}" title="Hear this speaker's lines from your script at the chosen speed" aria-label="Hear ${escapeHtml(c.name)}'s lines">${icon('play')}</button>
        <button type="button" class="btn-sample btn-icon" id="compareBtn_${c.id}" data-click="compareVoiceSample" data-speaker="${c.id}" title="Hear the same lines at 1.00x, then at the chosen speed" aria-label="Compare ${escapeHtml(c.name)} at 1.00x and the chosen speed">${icon('compare')}</button>
      </div>
      <div class="speed-control-box">
        <div class="speed-control-header">
          <div class="speed-label-group">
            <span class="speed-label">Speed</span>
            <div class="speed-input-stepper">
              <button type="button" class="speed-stepper-btn" data-click="stepSpeed" data-speaker="${c.id}" data-value="-0.05" title="Decrease speed by 0.05">−</button>
              <input type="number" id="speedNum_${c.id}" class="speed-num-input" value="${c.speed.toFixed(2)}" min="0.5" max="2.0" step="0.05" data-input="onSpeedNumInput" data-change="formatSpeedNum" data-speaker="${c.id}" aria-label="Speed">
              <span class="speed-unit">x</span>
              <button type="button" class="speed-stepper-btn" data-click="stepSpeed" data-speaker="${c.id}" data-value="0.05" title="Increase speed by 0.05">+</button>
            </div>
          </div>
          <div class="speed-presets-bar" id="speedPresets_${c.id}">
            <button type="button" class="speed-preset-btn ${Math.abs(c.speed - 0.90) < 0.01 ? 'active' : ''}" data-val="0.90" data-click="setSpeed" data-speaker="${c.id}" data-value="0.90">0.90x</button>
            <button type="button" class="speed-preset-btn ${Math.abs(c.speed - 0.95) < 0.01 ? 'active' : ''}" data-val="0.95" data-click="setSpeed" data-speaker="${c.id}" data-value="0.95">0.95x</button>
            <button type="button" class="speed-preset-btn ${Math.abs(c.speed - 1.00) < 0.01 ? 'active' : ''}" data-val="1.00" data-click="setSpeed" data-speaker="${c.id}" data-value="1.00">1.00x</button>
            <button type="button" class="speed-preset-btn ${Math.abs(c.speed - 1.05) < 0.01 ? 'active' : ''}" data-val="1.05" data-click="setSpeed" data-speaker="${c.id}" data-value="1.05">1.05x</button>
            <button type="button" class="speed-preset-btn ${Math.abs(c.speed - 1.10) < 0.01 ? 'active' : ''}" data-val="1.10" data-click="setSpeed" data-speaker="${c.id}" data-value="1.10">1.10x</button>
            <button type="button" class="speed-preset-btn ${Math.abs(c.speed - 1.20) < 0.01 ? 'active' : ''}" data-val="1.20" data-click="setSpeed" data-speaker="${c.id}" data-value="1.20">1.20x</button>
          </div>
        </div>
        <div class="speed-slider-row">
          <input type="range" id="speedSlider_${c.id}" min="0.75" max="1.50" step="0.05" value="${c.speed.toFixed(2)}" data-input="setSpeed" data-speaker="${c.id}" aria-label="Speed slider">
        </div>
      </div>
    `;

    container.appendChild(card);

    // Populate voice dropdown
    const select = card.querySelector(`#voice_${c.id}`);
    if (select) {
      populateDropdownWithOptions(select, c.voice);
    }
  });

  // Restore active element focus if possible
  if (activeElId) {
    const el = document.getElementById(activeElId);
    if (el) {
      el.focus();
      if (typeof activeSelStart === 'number') {
        try { el.setSelectionRange(activeSelStart, activeSelEnd); } catch (_) {}
      }
    }
  }
}

function addSpeakerCardManually() {
  if (characters.length >= 199) {
    showStatus('Cast limit reached', 'Use at most 199 characters plus the narrator.', 'error');
    return;
  }
  // The next free number: "Speaker 4" after Speaker 1-3, or a gap left by a removed speaker.
  const used = new Set(characters.map(c => c.name.trim().toLowerCase()));
  let newNum = 1;
  while (used.has(`speaker ${newNum}`)) newNum++;
  const newName = `Speaker ${newNum}`;
  const guess = guessGenderAndVoice(newName, newNum - 1,
    new Set([...roleVoicesFor(characters.map(c => c.name)), ...characters.map(c => c.voice)]));
  characters.push({
    id: `char_${nextCharIndex++}`,
    name: newName,
    prevName: newName,
    gender: guess.gender,
    voice: guess.voice,
    speed: guess.speed
  });
  renderCharacterCards();
  renderDynamicToolbarTags();
}

function removeSpeakerCard(charId) {
  characters = characters.filter(c => c.id !== charId);
  renderCharacterCards();
  renderDynamicToolbarTags();
}

// Keeps the Sample / Compare / × spoken names in step with the speaker's name. A button that is
// playing a sample is named Stop for now, so it gets the new name when the sample ends.
function nameSampleButtons(charId, name) {
  const names = [[document.getElementById(`sampleBtn_${charId}`), `Hear ${name}'s lines`],
                 [document.getElementById(`compareBtn_${charId}`), `Compare ${name} at 1.00x and the chosen speed`],
                 [document.querySelector(`[data-click=removeSpeakerCard][data-speaker="${charId}"]`), `Remove ${name}`]];
  for (const [button, label] of names) {
    if (!button) continue;
    if (isPreviewButton(button)) activePreview.label = label;
    else button.setAttribute('aria-label', label);
  }
}

function onCharacterNameChange(charId) {
  const char = characters.find(c => c.id === charId);
  if (!char) return;
  const input = document.getElementById(`name_${charId}`);
  const newName = input.value.trim() || char.name;
  const tagLabel = document.querySelector(`#tagBtn_${charId} .tag-label`);
  if (tagLabel) tagLabel.innerText = `[${newName}]`;
  document.querySelectorAll(`#tagBtn_${charId} .spk-avatar, #avatar_${charId}`)
    .forEach(a => { a.innerText = speakerInitial(newName); });
  nameSampleButtons(charId, newName);

  const script = document.getElementById('scriptInput').value;
  const helper = document.getElementById(`renameHelper_${charId}`);
  if (helper) {
    if (char.prevName && char.prevName !== newName && script.includes(`[${char.prevName}]`)) {
      setShown(helper, true);
      helper.innerText = `Update [${char.prevName}] → [${newName}] in script`;
    } else {
      setShown(helper, false);
    }
  }
}

function renameInScript(charId) {
  const char = characters.find(c => c.id === charId);
  if (!char) return;
  const input = document.getElementById(`name_${charId}`);
  const newName = input.value.trim() || char.name;
  const oldTag = `[${char.prevName}]`;
  const newTag = `[${newName}]`;

  const textarea = document.getElementById('scriptInput');
  const prevScrollTop = textarea.scrollTop;
  const prevScrollLeft = textarea.scrollLeft;
  const prevWindowX = window.scrollX || window.pageXOffset || 0;
  const prevWindowY = window.scrollY || window.pageYOffset || 0;
  const selStart = textarea.selectionStart;
  const selEnd = textarea.selectionEnd;

  textarea.value = textarea.value.split(oldTag).join(newTag);
  char.name = newName;
  char.prevName = newName;

  textarea.scrollTop = prevScrollTop;
  textarea.scrollLeft = prevScrollLeft;
  try { textarea.setSelectionRange(selStart, selEnd); } catch (_) {}
  window.scrollTo(prevWindowX, prevWindowY);

  const helper = document.getElementById(`renameHelper_${charId}`);
  if (helper) setShown(helper, false);
  updateWordCount();
  renderDynamicToolbarTags();
}

function onCharacterGenderChange(charId) {
  const char = characters.find(c => c.id === charId);
  if (!char) return;
  const genderSelect = document.getElementById(`gender_${charId}`);
  char.gender = genderSelect.value;

  // Voice recommendation matching new gender
  const voiceSelect = document.getElementById(`voice_${charId}`);
  if (voiceSelect) {
    if (char.gender === 'Female' && (voiceSelect.value.startsWith('am_') || voiceSelect.value.startsWith('bm_'))) {
      voiceSelect.value = 'af_sky';
      char.voice = 'af_sky';
    } else if (char.gender === 'Male' && (voiceSelect.value.startsWith('af_') || voiceSelect.value.startsWith('bf_'))) {
      voiceSelect.value = 'am_puck';
      char.voice = 'am_puck';
    } else if (char.gender === 'Child') {
      voiceSelect.value = 'af_sky';
      char.voice = 'af_sky';
    }
  }
}

function insertTagByName(speakerName) {
  const textarea = document.getElementById('scriptInput');
  if (!textarea) return;

  const prevScrollTop = textarea.scrollTop;
  const prevScrollLeft = textarea.scrollLeft;
  const prevWindowX = window.scrollX || window.pageXOffset || 0;
  const prevWindowY = window.scrollY || window.pageYOffset || 0;

  let start = textarea.selectionStart;
  let end = textarea.selectionEnd;
  if (typeof start !== 'number' || typeof end !== 'number') {
    start = textarea.value.length;
    end = textarea.value.length;
  }

  let prefix = '';
  if (start > 0 && textarea.value.charAt(start - 1) !== '\n') {
    prefix = '\n';
  }
  const tagText = `${prefix}[${speakerName}] `;

  if (typeof textarea.setRangeText === 'function') {
    textarea.setRangeText(tagText, start, end, 'end');
  } else {
    textarea.value = textarea.value.substring(0, start) + tagText + textarea.value.substring(end);
    textarea.selectionStart = textarea.selectionEnd = start + tagText.length;
  }

  try { textarea.focus({ preventScroll: true }); } catch (_) { textarea.focus(); }

  textarea.scrollTop = prevScrollTop;
  textarea.scrollLeft = prevScrollLeft;
  window.scrollTo(prevWindowX, prevWindowY);

  requestAnimationFrame(() => {
    textarea.scrollTop = prevScrollTop;
    textarea.scrollLeft = prevScrollLeft;
    window.scrollTo(prevWindowX, prevWindowY);
  });

  updateWordCount();
}

function loadSampleScript() {
  document.getElementById('scriptInput').value = sampleScriptText;
  updateWordCount();

  narratorConfig = defaultNarrator();
  const narrVoiceEl = document.getElementById('voice_Narrator');
  if (narrVoiceEl) narrVoiceEl.value = narratorConfig.voice;
  setSpeed('Narrator', narratorConfig.speed);

  characters = [
    { id: "char_1", name: "Speaker 1", prevName: "Speaker 1", gender: "Female", voice: "af_sky", speed: 1.05 },
    { id: "char_2", name: "Speaker 2", prevName: "Speaker 2", gender: "Male", voice: "am_eric", speed: 1.00 },
    { id: "char_3", name: "Speaker 3", prevName: "Speaker 3", gender: "Male", voice: "am_liam", speed: 1.00 }
  ];

  renderCharacterCards();
  renderDynamicToolbarTags();
  autoDetectSpeakers(false);

  // The demo is a fresh, unsaved project.
  pronunciations = [];
  renderPronunciations();
  document.getElementById('evenVolumeCheckbox').checked = true;
  document.getElementById('performanceLevel').value = 'natural';
  projectFileHandle = null;
  setProjectName(null);
  markProjectSaved();
}

function updateWordCount() {
  const text = document.getElementById('scriptInput').value.trim();
  const count = text ? text.split(/\s+/).length : 0;
  document.getElementById('charCount').innerText = `${count} words`;
}

/* Voice Sample Preview Player with Speed Support */
const MAX_SAMPLE_CHARS = 300;
let previewToken = 0;        // increases each time a sample starts or stops, so older ones know to quit
let activePreview = null;    // { button, label, others }: the button that is now a Stop button

function getSpeakerName(speakerKey) {
  if (speakerKey === 'Narrator') return 'Narrator';
  const nameInput = document.getElementById(`name_${speakerKey}`);
  const char = characters.find(c => c.id === speakerKey);
  return (nameInput ? nameInput.value.trim() : '') || (char ? char.name : '');
}

function trimToSentence(text, max) {
  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  const end = Math.max(cut.lastIndexOf('. '), cut.lastIndexOf('! '), cut.lastIndexOf('? '));
  return end > 40 ? cut.slice(0, end + 1) : cut.slice(0, cut.lastIndexOf(' '));
}

// The speaker's own lines from the script (same rules as the Python parser), about 150-300 characters.
function getSampleText(speakerKey) {
  const name = cleanSpeakerTag(getSpeakerName(speakerKey)).name.toLowerCase();
  const scriptEl = document.getElementById('scriptInput');
  if (!name || !scriptEl) return '';
  let current = 'narrator';
  let text = '';
  for (const raw of scriptEl.value.split('\n')) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const match = line.match(/^\[([^\]]+)\]\s*(.*)$/);
    let content = line;
    if (match && !isCueTag(match[1])) {
      const label = cleanSpeakerTag(match[1]);
      current = label.name.toLowerCase();
      content = dropRepeatedName(label.name, dropRepeatedName(match[1], match[2].trim()));
    }
    content = stripCueBrackets(content).replace(/\s+/g, ' ').trim();
    if (current !== name || !content) continue;
    text = text ? `${text} ${content}` : content;
    if (text.length >= 150) break;
  }
  return trimToSentence(text, MAX_SAMPLE_CHARS);
}

async function fetchPreviewUrl(voiceId, speed, text) {
  const params = new URLSearchParams({ voice: voiceId, speed: speed.toFixed(2) });
  if (text) params.set('text', text);
  const pron = pronunciationsFor(text);
  if (pron.length) params.set('pron', JSON.stringify(pron));
  const res = await fetch(`/api/preview_voice?${params}`);
  const data = await res.json();
  if (!data.success || !data.preview_url) throw new Error(data.error || 'Unknown error');
  return data.preview_url;
}

// Resolves true when the clip finishes, false if it was stopped by another sample.
function playPreviewUrl(url) {
  stopLiveListening();
  return new Promise((resolve, reject) => {
    const audio = new Audio(`${url}?t=${Date.now()}`);
    activePreviewAudio = audio;
    audio.onended = () => resolve(true);
    audio.onpause = () => { if (!audio.ended) resolve(false); };
    audio.onerror = () => reject(new Error('The sample could not be played.'));
    audio.play().catch(reject);
  });
}

/* While a sample loads or plays, the button that started it becomes a highlighted Stop
   button (the speaker's other sample button is disabled). Clicking it, pressing Esc,
   starting another sample or pressing Play stops the sample. */
function setButtonName(button, text) {
  button.title = text;
  button.setAttribute('aria-label', text);
}

function beginPreview(button, others = []) {
  stopLiveListening();
  stopPreview();
  activePreview = { button, html: button.innerHTML, title: button.title,
                    label: button.getAttribute('aria-label'), others };
  button.classList.add('previewing');
  button.innerHTML = icon('stop');
  setButtonName(button, 'Stop this sample (Esc)');
  others.forEach(b => { if (b) b.disabled = true; });
  return previewToken;
}

// The playing button's tooltip and spoken name, e.g. "Stop (1.05x) (Esc)".
function setPreviewLabel(token, text) {
  if (token === previewToken && activePreview) setButtonName(activePreview.button, `${text} (Esc)`);
}

function isPreviewButton(el) {
  return !!activePreview && activePreview.button === el;
}

function endPreview(token) {
  if (token === previewToken) stopPreview(); // otherwise a newer sample took over
}

function stopPreview() {
  previewToken++;
  if (activePreviewAudio) {
    activePreviewAudio.pause();
    activePreviewAudio = null;
  }
  if (activePreview) {
    const { button, html, title, label, others } = activePreview;
    activePreview = null;
    button.classList.remove('previewing');
    button.innerHTML = html;
    button.title = title;
    if (label) button.setAttribute('aria-label', label); else button.removeAttribute('aria-label');
    others.forEach(b => { if (b) b.disabled = false; });
    if (button.id === 'blendListen') button.disabled = blenderBusy;  // stayed on only to stop the sample
  }
}

async function playVoiceSample(speakerKey) {
  const select = document.getElementById(`voice_${speakerKey}`);
  const btn = document.getElementById(`sampleBtn_${speakerKey}`);
  if (!select || !btn) return;
  const speed = getSpeed(speakerKey);
  const text = getSampleText(speakerKey);
  const token = beginPreview(btn, [document.getElementById(`compareBtn_${speakerKey}`)]);
  setPreviewLabel(token, 'Stop (loading…)');

  try {
    const url = await fetchPreviewUrl(select.value, speed, text);
    if (token !== previewToken) return;
    setPreviewLabel(token, `Stop (${speed.toFixed(2)}x)`);
    await playPreviewUrl(url);
  } catch (err) {
    if (token === previewToken) alert('Could not load sample: ' + err.message);
  } finally {
    endPreview(token);
  }
}

// Plays the same text at 1.00x, then at the speaker's chosen speed.
async function compareVoiceSample(speakerKey) {
  const select = document.getElementById(`voice_${speakerKey}`);
  const btn = document.getElementById(`compareBtn_${speakerKey}`);
  if (!select || !btn) return;
  const speed = getSpeed(speakerKey);
  if (Math.round(speed * 100) === 100) {
    alert('This speaker is set to 1.00x. Change the speed first, then compare it with 1.00x.');
    return;
  }
  const text = getSampleText(speakerKey);
  const token = beginPreview(btn, [document.getElementById(`sampleBtn_${speakerKey}`)]);
  setPreviewLabel(token, 'Stop (loading…)');

  try {
    const baseUrl = await fetchPreviewUrl(select.value, 1.0, text);
    if (token !== previewToken) return;
    const chosenUrl = await fetchPreviewUrl(select.value, speed, text);
    if (token !== previewToken) return;

    setPreviewLabel(token, 'Stop (1.00x)');
    if (!(await playPreviewUrl(baseUrl)) || token !== previewToken) return;
    await new Promise(resolve => setTimeout(resolve, 600));
    if (token !== previewToken) return;

    setPreviewLabel(token, `Stop (${speed.toFixed(2)}x)`);
    await playPreviewUrl(chosenUrl);
  } catch (err) {
    if (token === previewToken) alert('Could not load comparison: ' + err.message);
  } finally {
    endPreview(token);
  }
}

/* Voice blending (voice_blends.py on the server): mix up to 3 voices, listen, save as a named voice
   that appears in every voice list ("My blends"). Blends made here keep their recipe, so they can be edited. */
const MAX_BLEND_ROWS = 3;
const BLEND_SAMPLE_TEXT = 'Hello there. This is how my new voice sounds when I read a few sentences from a story.';
let blendRows = [];        // [{ id, voice, weight }], weight 0-100
let nextBlendRow = 0;
let editingBlend = null;   // the saved blend being edited (saving under its name doesn't ask), or null
let describeBusy = false;  // waiting for a suggestion; Suggest shows Stop
let blendSnapshot = '';    // the panel as opened, saved or last suggested, to ask before replacing changes

function blendState() {
  return JSON.stringify({ rows: blendRows.map(r => [r.voice, r.weight]),
    name: document.getElementById('blendName').value, type: document.getElementById('blendType').value,
    pitch: document.getElementById('blendPitch').value, speed: document.getElementById('blendSpeed').value });
}

function blendPitch() {
  return parseFloat(document.getElementById('blendPitch').value) || 0;
}

// The typed starting speed, or null when the box is empty.
function blendStartSpeed() {
  const text = document.getElementById('blendSpeed').value.trim();
  return text === '' ? null : parseFloat(text);
}

function describeHelperText() {
  return userSettings.ai_writer_enabled ? `Using your AI program (${aiProgramName()}).`
    : 'Using word matching (the Script Assistant is off).';
}

function setDescribeHelper(text, isError = false) {
  const el = document.getElementById('blendDescribeHelper');
  el.innerText = text;
  el.classList.toggle('error', isError);
}

function blendEntry(name) {
  return (appConfig.available_popular_voices || []).find(v => v.id === name);
}

function setBlendMessage(text, isError = false) {
  const el = document.getElementById('blendMessage');
  el.innerText = text;
  el.classList.toggle('error', isError);
}

async function postBlend(path, body) {
  const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return res.json();
}

function toggleBlender() {
  if (document.getElementById('blendPanel').classList.contains('hidden')) openBlender(); else closeBlender();
}

function openBlender(name = null) {
  dropDescribe();
  lastSuggestedName = '';
  const entry = name ? blendEntry(name) : null;
  editingBlend = entry && entry.recipe ? name : null;
  if (editingBlend) {
    blendRows = entry.recipe.map(p => ({ id: nextBlendRow++, voice: p.voice, weight: Math.round(p.share * 100) }));
    document.getElementById('blendName').value = name;
    document.getElementById('blendType').value = entry.voice_type;
    document.getElementById('blendPitch').value = String(entry.pitch || 0);
    document.getElementById('blendSpeed').value = entry.start_speed ? entry.start_speed.toFixed(2) : '';
  } else {
    blendRows = [{ id: nextBlendRow++, voice: narratorConfig.voice || 'af_heart', weight: 100 }];
    document.getElementById('blendName').value = '';
    document.getElementById('blendType').value = 'Neutral';
    document.getElementById('blendPitch').value = '0';
    document.getElementById('blendSpeed').value = '';
  }
  const text = document.getElementById('blendText');
  if (!text.value.trim()) text.value = BLEND_SAMPLE_TEXT;
  const known = new Set((appConfig.available_popular_voices || []).map(v => v.id));
  const missing = blendRows.map(r => r.voice).filter(v => !known.has(v));
  setBlendMessage(!editingBlend ? '' : missing.length
    ? `Editing ${name}. ${joinNames(missing)} ${missing.length === 1 ? "isn't" : "aren't"} installed any more: ` +
      `choose another voice for ${missing.length === 1 ? 'that row' : 'those rows'}.`
    : `Editing ${name}. Save keeps the name.`, missing.length > 0);
  renderBlendRows();
  renderBlendList();
  document.getElementById('blendDescribe').value = editingBlend ? (entry.description || '') : '';
  setDescribeHelper(describeHelperText());
  blendSnapshot = blendState();
  setShown(document.getElementById('blendPanel'), true);
  document.getElementById('blendToggle').setAttribute('aria-expanded', 'true');
}

function closeBlender() {
  dropDescribe();
  if (isPreviewButton(document.getElementById('blendListen'))) stopPreview();
  setShown(document.getElementById('blendPanel'), false);
  document.getElementById('blendToggle').setAttribute('aria-expanded', 'false');
}

function editBlend(name) { openBlender(name); }

function renderBlendRows() {
  const box = document.getElementById('blendRows');
  box.innerHTML = '';
  blendRows.forEach((row, i) => {
    const div = document.createElement('div');
    div.className = 'blend-row';
    box.appendChild(div);  // the voice picker attaches next to the select, so it must be in the page first
    const select = document.createElement('select');
    select.id = `blendVoice_${row.id}`;
    select.dataset.change = 'setBlendVoice';
    select.dataset.row = row.id;
    select.setAttribute('aria-label', `Voice ${i + 1}`);
    div.appendChild(select);
    populateDropdownWithOptions(select, row.voice);
    if (row.voice && select.value !== row.voice) {  // an ingredient that was deleted: show its name, not "Choose a voice"
      select.add(new Option(`${row.voice} (not installed)`, row.voice), 0);
      select.value = row.voice;
      refreshVoicePickerButton(select);
    }
    const slider = document.createElement('input');
    slider.type = 'range';
    slider.min = '0';
    slider.max = '100';
    slider.step = '5';
    slider.value = String(row.weight);
    slider.id = `blendWeight_${row.id}`;
    slider.dataset.input = 'setBlendWeight';
    slider.dataset.row = row.id;
    slider.setAttribute('aria-label', `How much of voice ${i + 1}`);
    div.appendChild(slider);
    const share = document.createElement('span');
    share.className = 'blend-share';
    share.id = `blendShare_${row.id}`;
    div.appendChild(share);
    if (blendRows.length > 1) {
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'btn-remove-speaker';
      remove.dataset.click = 'removeBlendRow';
      remove.dataset.row = row.id;
      remove.title = 'Remove this voice from the blend';
      remove.setAttribute('aria-label', `Remove voice ${i + 1}`);
      remove.innerText = '×';
      div.appendChild(remove);
    }
  });
  setShown(document.getElementById('blendAddRow'), blendRows.length < MAX_BLEND_ROWS);
  updateBlendShares();
}

function updateBlendShares() {
  const total = blendRows.reduce((sum, r) => sum + r.weight, 0);
  for (const row of blendRows) {
    const el = document.getElementById(`blendShare_${row.id}`);
    if (el) el.innerText = `${total ? Math.round(row.weight / total * 100) : 0}%`;
  }
}

function findBlendRow(id) { return blendRows.find(r => String(r.id) === String(id)); }

function addBlendRow() {
  if (blendRows.length >= MAX_BLEND_ROWS) return;
  blendRows.push({ id: nextBlendRow++, voice: 'af_heart', weight: 50 });
  renderBlendRows();
}

function removeBlendRow(id) {
  if (blendRows.length <= 1) return;
  blendRows = blendRows.filter(r => String(r.id) !== String(id));
  renderBlendRows();
}

function setBlendVoice(id, voice) {
  const row = findBlendRow(id);
  if (!row) return;
  row.voice = voice;
  const select = document.getElementById(`blendVoice_${row.id}`);
  if (select && select.value !== voice) select.value = voice;
}

function setBlendWeight(id, value) {
  const row = findBlendRow(id);
  if (!row) return;
  row.weight = Math.max(0, Math.min(100, Number(value) || 0));
  const slider = document.getElementById(`blendWeight_${row.id}`);
  if (slider && Number(slider.value) !== row.weight) slider.value = String(row.weight);
  updateBlendShares();
}

function blendRecipe() {
  return blendRows.map(r => ({ voice: r.voice, weight: r.weight }));
}

async function listenToBlend() {
  const btn = document.getElementById('blendListen');
  const token = beginPreview(btn);
  setPreviewLabel(token, 'Stop (loading…)');
  setBlendMessage('');
  try {
    const data = await postBlend('/api/blend/preview', { recipe: blendRecipe(), text: document.getElementById('blendText').value,
      pitch: blendPitch() });
    if (!data.success) throw new Error(data.error || 'The sample could not be made.');
    if (token !== previewToken) return;
    setPreviewLabel(token, 'Stop');
    await playPreviewUrl(data.preview_url);
  } catch (err) {
    if (token === previewToken) setBlendMessage(err.message, true);
  } finally {
    endPreview(token);
  }
}

/* Describe a voice (describe_voice.py on the server): a description fills the rows, name and type.
   The server uses the AI program when the Script Assistant is on, else word matching. */
function setDescribeBusy(busy) {
  describeBusy = busy;
  const btn = document.getElementById('blendSuggest');
  btn.innerText = busy ? 'Stop' : 'Suggest';
  btn.setAttribute('aria-label', busy ? 'Stop the suggestion' : 'Suggest voices for the description');
  btn.disabled = !busy && blenderBusy;
  document.getElementById('blendDescribe').disabled = busy;
  document.getElementById('blendListen').disabled = busy || blenderBusy;
  document.getElementById('blendSave').disabled = busy || blenderBusy;
}

let describeTicket = 0;        // goes up on Stop, or when the panel moves on: a later answer is dropped
let describeClock = null;     // shows the seconds while the AI program thinks
let describeId = '';          // names the request on its way, for its Stop
let lastSuggestedName = '';   // a name the user typed instead is kept by the next suggestion

function stopDescribeClock() {
  if (describeClock) clearInterval(describeClock);
  describeClock = null;
}

// Drops a suggestion still on its way (Stop, the panel closed or opened again, a voice deleted) and stops
// the request on the server, so the AI program is free again. Describe a voice has its own Stop there,
// so a script being written is never stopped by it. Returns whether a suggestion was on its way.
function dropDescribe() {
  describeTicket++;
  stopDescribeClock();
  if (!describeBusy) return false;
  setDescribeBusy(false);
  // The request's id: a Stop that reaches the server before the request itself still stops it.
  fetch('/api/blend/describe/cancel', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: describeId }) }).catch(() => { /* nothing to stop */ });
  return true;
}

function stopDescribe() {
  dropDescribe();
  setDescribeHelper('Stopped.');
}

// The panel without its name: a typed name isn't something a suggestion replaces.
function blendStateWithoutName(state) {
  const parsed = JSON.parse(state);
  delete parsed.name;
  return JSON.stringify(parsed);
}

async function suggestBlend() {
  if (describeBusy) return stopDescribe();
  const description = document.getElementById('blendDescribe').value.trim();
  if (!description) {
    setDescribeHelper('Describe the voice first, for example: a grumpy old troll with a deep voice.', true);
    return;
  }
  if (isPreviewButton(document.getElementById('blendListen'))) stopPreview();
  const ticket = ++describeTicket;
  const asking = userSettings.ai_writer_enabled ? `Asking ${aiProgramName(true)}…` : 'Matching words…';
  setDescribeHelper(asking);
  if (userSettings.ai_writer_enabled) {  // a thinking model can take a while: show that time is passing
    const started = Date.now();
    stopDescribeClock();
    describeClock = setInterval(() => setDescribeHelper(`${asking} ${Math.round((Date.now() - started) / 1000)} s`), 1000);
  }
  setDescribeBusy(true);
  let data;
  try {
    describeId = `describe-${Date.now()}-${ticket}`;
    data = await postBlend('/api/blend/describe', { description, id: describeId });
  } catch (err) {
    data = { success: false, error: 'No suggestion. Check that the studio is still running, then try again.' };
  }
  if (ticket !== describeTicket) return;  // stopped, or the panel moved on meanwhile
  stopDescribeClock();
  setDescribeBusy(false);
  if (data.stopped) { setDescribeHelper('Stopped.'); return; }
  const extra = [data.note, data.unknown && data.unknown.length ? `Not used: ${data.unknown.join(', ')}.` : '']
    .filter(Boolean).join(' ');
  if (!data.success) {
    setDescribeHelper([data.error || 'No suggestion.', extra].filter(Boolean).join(' '), true);
    return;
  }
  if (blendStateWithoutName(blendState()) !== blendStateWithoutName(blendSnapshot)
      && !window.confirm("Replace what's in the panel with the suggestion?")) {
    setDescribeHelper('Kept what was in the panel.');
    return;
  }
  blendRows = data.recipe.map(p => ({ id: nextBlendRow++, voice: p.voice, weight: Math.round(p.share * 100) }));
  // A name the user typed (not one from an earlier suggestion) is kept.
  const nameBox = document.getElementById('blendName');
  const typed = nameBox.value.trim();
  // The edited blend's own name is replaced, so Save can't quietly overwrite it; a new name typed while editing stays.
  if (!typed || typed === lastSuggestedName || typed === editingBlend) nameBox.value = data.name;
  lastSuggestedName = data.name;
  document.getElementById('blendType').value = data.voice_type;
  document.getElementById('blendPitch').value = '0';  // a suggestion starts from the voices as they are
  document.getElementById('blendSpeed').value = '';
  renderBlendRows();
  blendSnapshot = blendState();
  const by = data.helper === 'ai' ? `(Suggested by ${aiProgramName(true)}.)` : '(Suggested by word matching.)';
  setDescribeHelper([data.explanation, extra, by].filter(Boolean).join(' '));
}

document.getElementById('blendDescribe').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) {
    e.preventDefault();
    if (!describeBusy && !document.getElementById('blendSuggest').disabled) suggestBlend();
  }
});

let blendSaving = false;  // a second click on Save while a save is running does nothing

async function saveBlend(replace = false) {
  if (blendSaving) return;
  const name = document.getElementById('blendName').value.trim();
  const saveBtn = document.getElementById('blendSave');
  blendSaving = true;
  saveBtn.disabled = true;
  setBlendMessage('Saving…');
  let askReplace = null;
  try {
    const data = await postBlend('/api/blend/save', {
      name, recipe: blendRecipe(), voice_type: document.getElementById('blendType').value,
      description: document.getElementById('blendDescribe').value.trim(),
      pitch: blendPitch(), start_speed: blendStartSpeed(),
      replace: replace || (editingBlend !== null && name === editingBlend),
    });
    if (data.exists && !replace) {
      askReplace = data.error;
    } else if (!data.success) {
      setBlendMessage(data.error || 'The voice could not be saved.', true);
    } else {
      editingBlend = data.voice;
      blendSnapshot = blendState();
      await reloadVoiceLists();
      const folder = voiceFolder(data.voice);
      setBlendMessage(`Saved. ${data.voice} is now in every voice list, in ${folder ? `your ${folder} folder` : 'the My blends folder'}.`);
    }
  } catch (err) {
    setBlendMessage('The voice could not be saved. Check that the studio is still running, then try again.', true);
  } finally {
    blendSaving = false;
    saveBtn.disabled = document.getElementById('generateBtn').disabled;  // stays off while audio is being made
  }
  if (askReplace !== null) {
    if (window.confirm(askReplace)) return saveBlend(true);
    setBlendMessage('');
  }
}

function speakersUsingVoice(name) {
  const users = narratorConfig.voice === name ? ['the Narrator'] : [];
  return users.concat(characters.filter(c => c.voice === name).map(c => c.name));
}

let blendDeleting = false;  // a second click on Delete while a delete is running does nothing

// "A", "A and B", "A, B and C"
function joinNames(names) {
  return names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}` : names.join('');
}

async function deleteBlend(name) {
  if (blendDeleting) return;
  const users = speakersUsingVoice(name);
  const names = joinNames(users);
  const who = users.length
    ? ` ${names.charAt(0).toUpperCase()}${names.slice(1)} ${users.length === 1 ? 'uses' : 'use'} this voice and will get a standard voice of their type.`
    : '';
  if (!window.confirm(`Delete the voice ${name}?${who}`)) return;
  if (dropDescribe()) setDescribeHelper('Stopped.');  // a suggestion on its way would land on a changed panel
  blendDeleting = true;
  document.querySelectorAll('#blendList .blend-delete').forEach(b => { b.disabled = true; });
  try {
    const data = await postBlend('/api/blend/delete', { name });
    if (!data.success) {
      setBlendMessage(data.error || 'The voice could not be deleted.', true);
      return;
    }
    if (editingBlend === name) editingBlend = null;
    await reloadVoiceLists();
    setBlendMessage(`Deleted ${name}.`);
  } catch (err) {
    setBlendMessage('The voice could not be deleted. Check that the studio is still running, then try again.', true);
  } finally {
    blendDeleting = false;
    document.querySelectorAll('#blendList .blend-delete').forEach(b => { b.disabled = blenderBusy; });
  }
}

function renderBlendList() {
  const list = document.getElementById('blendList');
  list.innerHTML = '';
  const own = (appConfig.available_popular_voices || []).filter(v => v.accent === 'Blend' || v.accent === 'Custom');
  if (!own.length) {
    const li = document.createElement('li');
    li.className = 'muted';
    li.innerText = 'No voices of your own yet.';
    list.appendChild(li);
  }
  for (const v of own) {
    const li = document.createElement('li');
    li.className = 'blend-item';
    const label = document.createElement('span');
    label.innerText = v.recipe ? `${v.id} · ${v.gender} · ${v.tone}` : `${v.id} (made before the blender; can't be edited)`;
    li.appendChild(label);
    if (v.recipe) {
      const edit = document.createElement('button');
      edit.type = 'button';
      edit.className = 'btn btn-ghost btn-sm';
      edit.dataset.click = 'editBlend';
      edit.dataset.voice = v.id;
      edit.innerText = 'Edit';
      edit.setAttribute('aria-label', `Edit ${v.id}`);
      li.appendChild(edit);
    }
    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'btn btn-ghost btn-sm blend-delete';
    del.dataset.click = 'deleteBlend';
    del.dataset.voice = v.id;
    del.innerText = 'Delete';
    del.disabled = blenderBusy || blendDeleting;
    del.setAttribute('aria-label', `Delete ${v.id}`);
    li.appendChild(del);
    list.appendChild(li);
  }
}

// After a save or delete: fetch the voice list again and refill every voice list on the page.
// A speaker whose voice is gone gets a standard voice of their type (the Narrator gets your default Narrator).
async function reloadVoiceLists() {
  const res = await fetch('/api/config');
  const data = await res.json();
  appConfig.available_popular_voices = data.config.available_popular_voices || [];
  const known = new Set(appConfig.available_popular_voices.map(v => v.id));
  if (!known.has(narratorConfig.voice)) {
    const fallback = defaultNarrator();
    narratorConfig.voice = fallback.voice;
    setSpeed('Narrator', fallback.speed);
  }
  // Folder places of voices that no longer exist are dropped (they would count toward the limit).
  const folders = userSettings.voice_folders || {};
  const gone = Object.keys(folders).filter(id => !known.has(id));
  if (gone.length) {
    gone.forEach(id => delete folders[id]);
    saveUserSettings();
  }
  const used = new Set(characters.map(c => c.voice).filter(v => known.has(v)));
  characters.forEach((c, n) => {
    if (known.has(c.voice)) return;
    c.voice = (voiceForType(c.gender, n, used) || { voice: 'af_heart' }).voice;
    used.add(c.voice);
  });
  populateNarratorDropdown();
  updateNarratorDefaultBtn();
  renderCharacterCards();
  if (!document.getElementById('blendPanel').classList.contains('hidden')) renderBlendRows();
  renderBlendList();
  autosaveNow();
}

let blenderBusy = false;  // audio is being made: no saving, deleting or new samples

function setBlenderBusy(busy) {
  blenderBusy = busy;
  const listen = document.getElementById('blendListen');
  listen.disabled = busy && !isPreviewButton(listen);  // a playing sample can still be stopped
  document.getElementById('blendSave').disabled = busy;
  document.getElementById('blendSuggest').disabled = busy && !describeBusy;
  document.querySelectorAll('#blendList .blend-delete').forEach(b => { b.disabled = busy || blendDeleting; });
}


function formatTime(seconds) {
  if (!seconds || isNaN(seconds) || seconds < 0) return '00:00';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

function updatePlayButton(isPlaying) {
  const playIcon = document.getElementById('playIcon');
  const playText = document.getElementById('playText');
  const btn = document.getElementById('btnPlayMaster');
  if (playIcon) playIcon.innerHTML = icon(isPlaying ? 'pause' : 'play');
  if (playText) playText.innerText = isPlaying ? 'Pause' : 'Play';
  if (btn) btn.classList.toggle('playing', isPlaying);
}

function setMasterVolume(val) {
  const audio = document.getElementById('mainAudio');
  if (audio) {
    audio.volume = Math.max(0, Math.min(1, parseFloat(val)));
  }
}

function loadWaveformData(peaks) {
  currentWaveformPeaks = Array.isArray(peaks) && peaks.length ? Float32Array.from(peaks) : null;
  cachedPeaks = null;
  computePeaks();
  drawWaveform(0);
}

function cssToken(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function computePeaks() {
  const canvas = document.getElementById('waveformCanvas');
  const wrapper = document.getElementById('waveformWrapper');
  if (!canvas || !wrapper || !currentWaveformPeaks) return;

  const dpr = window.devicePixelRatio || 1;
  const rect = wrapper.getBoundingClientRect();
  const displayWidth = rect.width || 600;
  const displayHeight = rect.height || 72;

  canvas.width = Math.floor(displayWidth * dpr);
  canvas.height = Math.floor(displayHeight * dpr);

  // Bar width + bar spacing in CSS pixels
  const barStep = 4.5;
  const numBars = Math.max(20, Math.floor(displayWidth / barStep));
  cachedPeaks = new Float32Array(numBars);

  const totalPeaks = currentWaveformPeaks.length;
  for (let i = 0; i < numBars; i++) {
    const start = Math.floor(i * totalPeaks / numBars);
    const end = Math.min(totalPeaks, Math.max(start + 1, Math.floor((i + 1) * totalPeaks / numBars)));
    let peak = 0;
    for (let j = start; j < end; j++) peak = Math.max(peak, currentWaveformPeaks[j]);
    cachedPeaks[i] = Math.max(0.04, Math.min(1.0, Math.pow(peak, 0.85) * 1.05));
  }
}

function drawWaveform(progressFraction = 0) {
  const canvas = document.getElementById('waveformCanvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const dpr = window.devicePixelRatio || 1;
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);

  if (!cachedPeaks || cachedPeaks.length === 0) {
    // Subtle center line when no audio loaded yet
    ctx.fillStyle = cssToken('--wave-unplayed');
    ctx.fillRect(0, height / 2 - 1 * dpr, width, 2 * dpr);
    return;
  }

  const numBars = cachedPeaks.length;
  const totalBarStep = width / numBars;
  const barWidth = Math.max(1.5 * dpr, totalBarStep - 1.5 * dpr);

  // Played part: accent gradient; unplayed: muted. Both follow the light/dark theme.
  const playedGrad = ctx.createLinearGradient(0, 0, width, 0);
  playedGrad.addColorStop(0, cssToken('--wave-played-a'));
  playedGrad.addColorStop(1, cssToken('--wave-played-b'));
  const unplayedColor = cssToken('--wave-unplayed');

  const centerY = height / 2;
  const maxBarHeight = height * 0.85;
  const clampedProg = Math.min(1, Math.max(0, progressFraction));
  const currentPlayedIdx = Math.floor(numBars * clampedProg);

  for (let i = 0; i < numBars; i++) {
    const x = i * totalBarStep;
    const barAmp = cachedPeaks[i];
    const barH = Math.max(4 * dpr, barAmp * maxBarHeight);
    const y = centerY - barH / 2;

    ctx.fillStyle = (i <= currentPlayedIdx) ? playedGrad : unplayedColor;

    const radius = Math.min(barWidth / 2, 2 * dpr);
    ctx.beginPath();
    if (ctx.roundRect) {
      ctx.roundRect(x, y, barWidth, barH, radius);
    } else {
      ctx.rect(x, y, barWidth, barH);
    }
    ctx.fill();
  }

  // Update playhead DOM element position
  const playhead = document.getElementById('waveformPlayhead');
  if (playhead) {
    playhead.style.left = `${clampedProg * 100}%`;
  }
}

function startWaveformPlaybackLoop() {
  if (animFrameId) cancelAnimationFrame(animFrameId);
  function loop() {
    const audio = document.getElementById('mainAudio');
    if (audio && audio.duration && !audio.paused) {
      const prog = audio.currentTime / audio.duration;
      drawWaveform(prog);
      const curEl = document.getElementById('currentTimeDisplay');
      if (curEl) curEl.innerText = formatTime(audio.currentTime);
      updateTranscriptHighlight(audio.currentTime);
      animFrameId = requestAnimationFrame(loop);
    } else {
      animFrameId = null;
    }
  }
  animFrameId = requestAnimationFrame(loop);
}

function stopWaveformPlaybackLoop() {
  if (animFrameId) {
    cancelAnimationFrame(animFrameId);
    animFrameId = null;
  }
}

// Moves playback to `time` (seconds) and updates the waveform, time display, transcript and position slider.
function seekTo(time) {
  const audio = document.getElementById('mainAudio');
  if (!audio || !audio.duration) return;
  audio.currentTime = Math.max(0, Math.min(audio.duration, time));
  drawWaveform(audio.currentTime / audio.duration);
  const curEl = document.getElementById('currentTimeDisplay');
  if (curEl) curEl.innerText = formatTime(audio.currentTime);
  updateTranscriptHighlight(audio.currentTime);
  updatePositionSlider();
}

// The waveform is a slider for screen readers: "01:23 of 03:45".
function updatePositionSlider() {
  const audio = document.getElementById('mainAudio');
  const wrapper = document.getElementById('waveformWrapper');
  if (!wrapper) return;
  const duration = audio && audio.src && audio.duration ? audio.duration : 0;
  const current = duration ? audio.currentTime : 0;
  wrapper.setAttribute('aria-valuemax', String(Math.round(duration)));
  wrapper.setAttribute('aria-valuenow', String(Math.round(current)));
  wrapper.setAttribute('aria-valuetext', `${formatTime(current)} of ${formatTime(duration)}`);
}

const SKIP_SECONDS = 10;

function skipAudio(seconds) {
  const audio = document.getElementById('mainAudio');
  if (!audio || !audio.src || !audio.duration) return;
  seekTo(audio.currentTime + seconds);
}

function skipBack() { skipAudio(-SKIP_SECONDS); }
function skipForward() { skipAudio(SKIP_SECONDS); }

function seekWaveform(e) {
  const audio = document.getElementById('mainAudio');
  const wrapper = document.getElementById('waveformWrapper');
  if (!audio || !audio.duration || !wrapper) return;

  const rect = wrapper.getBoundingClientRect();
  const fraction = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  seekTo(fraction * audio.duration);
}

// Press on the waveform to jump there; keep the button down and drag to move through the audio.
function startWaveformDrag(e) {
  const audio = document.getElementById('mainAudio');
  if (e.button !== 0 || !audio || !audio.duration) return;
  e.preventDefault();
  seekWaveform(e);
  const move = (ev) => seekWaveform(ev);
  const stop = (ev) => {
    if (ev.type === 'pointerup') seekWaveform(ev);
    document.removeEventListener('pointermove', move);
    document.removeEventListener('pointerup', stop);
    document.removeEventListener('pointercancel', stop);
  };
  document.addEventListener('pointermove', move);
  document.addEventListener('pointerup', stop);
  document.addEventListener('pointercancel', stop);
}

function hoverWaveform(e) {
  const audio = document.getElementById('mainAudio');
  const wrapper = document.getElementById('waveformWrapper');
  const line = document.getElementById('waveformHoverLine');
  const tip = document.getElementById('waveformHoverTooltip');
  if (!audio || !audio.duration || !wrapper || !line || !tip) return;

  const rect = wrapper.getBoundingClientRect();
  const hoverX = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
  const fraction = hoverX / rect.width;
  const hoverTime = fraction * audio.duration;

  line.style.display = 'block';
  line.style.left = `${hoverX}px`;

  tip.style.display = 'block';
  tip.style.left = `${Math.min(rect.width - 24, Math.max(24, hoverX))}px`;
  tip.innerText = formatTime(hoverTime);
}

function leaveWaveform() {
  const line = document.getElementById('waveformHoverLine');
  const tip = document.getElementById('waveformHoverTooltip');
  if (line) line.style.display = 'none';
  if (tip) tip.style.display = 'none';
}

function toggleMasterPlay() {
  stopLiveListening();
  const audio = document.getElementById('mainAudio');
  if (!audio || !audio.src) return;

  if (window.audioCtx && window.audioCtx.state === 'suspended') {
    window.audioCtx.resume();
  }

  if (audio.paused) {
    stopPreview();
    audio.play().catch(err => {
      console.warn("Audio play blocked or failed:", err);
    });
  } else {
    audio.pause();
  }
}

function syncLoopCheckboxes(isChecked) {
  const c = document.getElementById('loopMasterCheckbox');
  if (c) c.checked = isChecked;
  const mainAudio = document.getElementById('mainAudio');
  if (mainAudio) mainAudio.loop = isChecked;
}

// Reads a status message out to screen readers. Emptied first, so the same message twice is read twice.
let announceTimer = null;
function announce(text) {
  const el = document.getElementById('srAnnounce');
  el.textContent = '';
  clearTimeout(announceTimer);
  announceTimer = setTimeout(() => { el.textContent = text; }, 100);
}

// The status box beside Generate: a title and a line of detail, both read out.
function showStatus(title, detail = null) {
  document.getElementById('progressTitle').innerText = title;
  if (detail !== null) document.getElementById('progressStatus').innerText = detail;
  announce(detail ? `${title}. ${detail}` : title);
}

let liveListening = null;
function stopLiveListening() {
  if (liveListening) liveListening.stop();
  liveListening = null;
}
function liveListeningChanged() {
  if (!document.getElementById('liveListenCheckbox').checked) stopLiveListening();
}

/* Live Real-Time Generation with Progress Bar. Returns true when the audio was made. */
async function generateAudio() {
  resetTabTitle();
  const scriptBox = document.getElementById('scriptInput');
  if (!scriptBox.value.trim()) {
    showStatus('Nothing to generate yet', 'Type or paste a script first, or click Reset Sample for the demo.');
    scriptBox.focus();
    return false;
  }
  stopLiveListening();
  const wantsLive = document.getElementById('liveListenCheckbox').checked;
  if (wantsLive) {
    stopPreview();
    document.getElementById('mainAudio').pause();
    try {
      liveListening = new LiveListening({status: text => { document.getElementById('liveListenStatus').innerText = text; }});
    } catch (_) {
      document.getElementById('liveListenStatus').innerText = 'Listening unavailable. The full recording will still be prepared.';
    }
  }
  const listener = liveListening;
  const btn = document.getElementById('generateBtn');
  const spinner = document.getElementById('spinner');
  const btnText = document.getElementById('btnText');

  const progressBox = document.getElementById('progressBox');
  const progressPercent = document.getElementById('progressPercent');
  const progressFill = document.getElementById('progressFill');
  const progressStatus = document.getElementById('progressStatus');

  btn.disabled = true;
  document.getElementById('clearOutputBtn').disabled = true;
  setImportRendering(true);
  setBlenderBusy(true);
  setShown(spinner, true);
  btnText.innerText = "Generating…";
  const stopBtn = document.getElementById('stopBtn');
  stopBtn.disabled = false;
  stopBtn.innerHTML = `${icon('stop')} Stop`;
  setShown(stopBtn, true);

  progressBox.className = 'progress-box active';
  progressFill.style.width = '0%';
  progressPercent.innerText = '0%';
  showStatus("Synthesizing Audio Lines...");
  progressStatus.innerText = "Initializing Kokoro pipeline...";

  const voices = voicesPayload();

  const payload = {
    script: document.getElementById('scriptInput').value,
    dry_run: false,
    export_wav: false,
    live_listen: !!listener,
    voices,
    pronunciations: activePronunciations(),
    even_volume: document.getElementById('evenVolumeCheckbox').checked,
    performance: document.getElementById('performanceLevel').value,
    pacing: {
      dialogue_turn_pause: parseFloat(document.getElementById('pacingDialogue').value),
      narrator_transition_pause: parseFloat(document.getElementById('pacingNarrator').value)
    }
  };

  let made = false;
  let receivedComplete = false;
  try {
    const response = await fetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (!response.ok) throw new Error(`Studio returned ${response.status}`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split('\n\n');
      buffer = events.pop();

      let streamCompleted = false;

      for (const rawEvt of events) {
        const trimmed = rawEvt.trim();
        if (trimmed.startsWith('data: ')) {
          try {
            const message = JSON.parse(trimmed.substring(6));

            if (message.type === 'audio') {
              listener?.enqueue(message.data);
            } else if (message.type === 'listen_error') {
              listener?.fail();
            } else if (message.type === 'progress') {
              const p = message.data;
              progressFill.style.width = `${p.percent}%`;
              progressPercent.innerText = `${p.percent}%`;
              const reused = p.cached ? ' (reused, unchanged)' : '';
              progressStatus.innerText = `[Line ${p.current}/${p.total}]${reused} ${p.speaker}: "${p.text}"`;
            } else if (message.type === 'complete') {
              streamCompleted = true;
              receivedComplete = true;
              const result = message.result || {};

              if (result.cancelled) {
                listener?.stop();
                progressBox.className = 'progress-box';
                showStatus("Stopped", `Stopped after ${result.lines_done} of ${result.total_lines} lines. ` +
                  `Those lines are kept, so Generate picks up where you left off.`);
              } else if (result.success && result.audio_url && !result.dry_run) {
                progressFill.style.width = '100%';
                progressPercent.innerText = '100%';
                progressBox.className = 'progress-box complete';
                const total = (result.lines || []).length;
                const reusedCount = result.cached_lines || 0;
                const readyMessage = listener && !listener.closed
                  ? "Recording ready. Listening continues. Uncheck Listen while preparing to stop; Play restarts the recording."
                  : "Audio is ready. Press Play, or click a line in the transcript.";
                showStatus("Synthesis Complete", reusedCount > 0
                  ? `${readyMessage} ${reusedCount} of ${total} lines were unchanged and reused; ` +
                    `${total - reusedCount} ${total - reusedCount === 1 ? 'was' : 'were'} rendered.`
                  : readyMessage);
                displayOutput(result);
                listener?.finish();
                made = true;
              } else {
                progressBox.className = 'progress-box';
                listener?.stop();
                showStatus("Generation failed", result.error || "Unknown error");
                alert("Generation failed: " + (result.error || "Unknown error"));
              }
              break;
            }
          } catch (e) {
            console.error("Error parsing stream event:", e);
          }
        }
      }

      if (streamCompleted) {
        try {
          await reader.cancel();
        } catch (_) {}
        break;
      }
    }
    if (!receivedComplete) throw new Error('The recording stream ended before completion.');
  } catch (err) {
    listener?.stop();
    alert("Failed to communicate with local studio: " + err);
    progressBox.className = 'progress-box';
    showStatus("Generation failed", "Error: " + err);
  } finally {
    if (!made) listener?.stop();
    btn.disabled = false;
    document.getElementById('clearOutputBtn').disabled = false;
    setImportRendering(false);
    setBlenderBusy(false);
    setShown(spinner, false);
    btnText.innerText = "Generate";
    setShown(stopBtn, false);
  }
  return made;
}

async function stopRender() {
  stopLiveListening();
  const stopBtn = document.getElementById('stopBtn');
  stopBtn.disabled = true;
  stopBtn.innerText = 'Stopping after this line...';
  try {
    await fetch('/api/cancel', { method: 'POST' });
  } catch (err) {
    stopBtn.disabled = false;
    stopBtn.innerHTML = `${icon('stop')} Stop`;
    alert("Could not reach the studio to stop the render: " + err);
  }
}

// Empties the Output zone (transcript, downloads, waveform, player). The script and cast stay,
// and the remembered lines make Generate bring the same audio back quickly.
function clearOutput() {
  stopLiveListening();
  document.getElementById('mainAudio').pause();
  displayOutput({ audio_url: null, elapsed_seconds: 0 });
  document.getElementById('outElapsed').innerText = '-';
  document.getElementById('currentTimeDisplay').innerText = '00:00';
  document.getElementById('totalTimeDisplay').innerText = '00:00';
  updatePlayButton(false);
}

function setSkipButtons(enabled) {
  for (const id of ['btnSkipBack', 'btnSkipForward']) {
    const btn = document.getElementById(id);
    if (btn) btn.disabled = !enabled;
  }
}

function displayOutput(result) {
  const hasAudio = !!result.audio_url;
  setShown(document.getElementById('outputEmpty'), !hasAudio);
  setShown(document.getElementById('clearOutputBtn'), hasAudio);
  setShown(document.getElementById('exportSection'), hasAudio);

  const outDur = document.getElementById('outDuration');
  const outElapsed = document.getElementById('outElapsed');
  const mainAudio = document.getElementById('mainAudio');
  const downloadLink = document.getElementById('downloadLink');
  const downloadZipLink = document.getElementById('downloadZipLink');
  const btnPlayMaster = document.getElementById('btnPlayMaster');
  const individualTracksSection = document.getElementById('individualTracksSection');
  const individualTracksList = document.getElementById('individualTracksList');

  outElapsed.innerText = `${result.elapsed_seconds.toFixed(2)}s`;

  if (!result.audio_url) {
    outDur.innerText = `-`;
    mainAudio.removeAttribute('src');
    setShown(downloadLink, false);
    setShown(downloadZipLink, false);
    btnPlayMaster.disabled = true;
    setSkipButtons(false);
    updatePositionSlider();
    setShown(individualTracksSection, false);
    currentWaveformPeaks = null;
    cachedPeaks = null;
    drawWaveform(0);
  } else {
    const urlWithTimestamp = `${result.audio_url}?t=${Date.now()}`;
    outDur.innerText = `${result.duration_seconds.toFixed(2)}s`;

    const curEl = document.getElementById('currentTimeDisplay');
    const totEl = document.getElementById('totalTimeDisplay');
    if (curEl) curEl.innerText = '00:00';
    if (totEl) totEl.innerText = formatTime(result.duration_seconds);

    mainAudio.src = urlWithTimestamp;

    const isLoop = !!document.getElementById('loopMasterCheckbox')?.checked;
    mainAudio.loop = isLoop;
    mainAudio.load();

    btnPlayMaster.disabled = false;
    setSkipButtons(true);
    updatePlayButton(false);

    // Load waveform data and render
    loadWaveformData(result.waveform_peaks);

    // Master dialogue WAV download
    downloadLink.href = `${result.audio_url}?download=1&t=${Date.now()}`;
    setShown(downloadLink, true);

    // Multi-track ZIP download (if multiple speakers)
    if (result.zip_url) {
      downloadZipLink.href = `${result.zip_url}?t=${Date.now()}`;
      setShown(downloadZipLink, true);
    } else {
      setShown(downloadZipLink, false);
    }

    // Populate Individual Speaker Tracks
    const tracks = result.tracks || [];
    if (tracks.length > 0) {
      setShown(individualTracksSection, true);
      individualTracksList.innerHTML = '';

      tracks.forEach(track => {
        const item = document.createElement('div');
        item.className = 'stem-item';
        item.innerHTML = `
          <span class="stem-name"><span class="stem-avatar"></span><span>${escapeHtml(track.speaker)}</span></span>
          <a href="${track.url}&t=${Date.now()}" download="${escapeHtml(track.filename)}" class="stem-btn" title="Download the track for ${escapeHtml(track.speaker)} on its own">${icon('download')}.wav</a>
        `;
        item.querySelector('.stem-avatar').replaceWith(makeSpeakerAvatar(track.speaker));
        individualTracksList.appendChild(item);
      });
    } else {
      setShown(individualTracksSection, false);
    }
  }

  renderTranscript(result.audio_url ? (result.lines || []) : []);
}

/* Read-along Transcript */
let transcriptLines = [];
let activeTranscriptIndex = -1;

function renderTranscript(lines) {
  const section = document.getElementById('transcriptSection');
  const list = document.getElementById('transcriptList');
  transcriptLines = lines.filter(l => typeof l.start === 'number' && typeof l.end === 'number');
  activeTranscriptIndex = -1;
  list.innerHTML = '';
  if (transcriptLines.length === 0) {
    setShown(section, false);
    return;
  }

  transcriptLines.forEach((line, i) => {
    const row = document.createElement('div');
    row.className = 'transcript-line';
    row.id = `transcriptLine_${i}`;
    row.title = 'Play from this line';
    row.onclick = () => playFromTranscriptLine(i);

    const time = document.createElement('span');
    time.className = 'transcript-time';
    time.innerText = formatTime(line.start);

    const speaker = document.createElement('span');
    speaker.className = 'transcript-speaker';
    speaker.title = line.speaker;
    speaker.append(makeSpeakerAvatar(line.speaker));

    const text = document.createElement('span');
    const name = document.createElement('span');
    name.className = 'transcript-name';
    name.innerText = line.speaker;
    text.append(name, document.createTextNode(line.text));
    if (line.cached) {
      const reused = document.createElement('span');
      reused.className = 'transcript-reused';
      reused.innerText = '(reused)';
      reused.title = 'Unchanged since an earlier render, so it was not re-synthesized';
      text.appendChild(reused);
    }

    row.append(time, speaker, text);
    list.appendChild(row);
  });

  const reusedCount = transcriptLines.filter(l => l.cached).length;
  document.getElementById('transcriptHint').innerText = reusedCount > 0
    ? `Click a line to jump to it · ${reusedCount} of ${transcriptLines.length} lines reused`
    : 'Click a line to jump to it';
  setShown(section, true);
}

// Highlights the line being spoken at `time` (seconds) and keeps it in view inside the list.
function updateTranscriptHighlight(time) {
  if (transcriptLines.length === 0) return;
  let index = -1;
  for (let i = 0; i < transcriptLines.length; i++) {
    if (time >= transcriptLines[i].start) index = i; else break;
  }
  // During the pause after a line, keep that line highlighted until the next one starts.
  if (index === activeTranscriptIndex) return;

  const previous = document.getElementById(`transcriptLine_${activeTranscriptIndex}`);
  if (previous) previous.classList.remove('active');
  activeTranscriptIndex = index;
  const current = document.getElementById(`transcriptLine_${index}`);
  if (!current) return;
  current.classList.add('active');

  const list = document.getElementById('transcriptList');
  const rowTop = current.offsetTop;
  if (rowTop < list.scrollTop || rowTop + current.offsetHeight > list.scrollTop + list.clientHeight) {
    list.scrollTop = Math.max(0, rowTop - list.clientHeight / 3);
  }
}

function playFromTranscriptLine(i) {
  const audio = document.getElementById('mainAudio');
  const line = transcriptLines[i];
  if (!audio || !audio.src || !line) return;
  audio.currentTime = line.start;
  updateTranscriptHighlight(line.start);
  if (audio.duration) drawWaveform(line.start / audio.duration);
  const curEl = document.getElementById('currentTimeDisplay');
  if (curEl) curEl.innerText = formatTime(line.start);
  if (audio.paused) {
    stopLiveListening();
    audio.play().catch(err => console.warn("Audio play blocked or failed:", err));
  }
}

/* Speaker colors: each character gets a colorblind-safe color by position in the cast
   (classes spk-c0 to spk-c6 in studio.css), always shown with the speaker's initial. */
const SPEAKER_COLOR_COUNT = 7;

function characterSpeakerClass(index) {
  return `spk-c${index % SPEAKER_COLOR_COUNT}`;
}

function speakerClassForName(speaker) {
  const sLower = (speaker || '').toLowerCase().trim();
  if (sLower === 'narrator') return 'spk-narrator';
  const idx = characters.findIndex(c => c.name.toLowerCase() === sLower);
  return idx >= 0 ? characterSpeakerClass(idx) : 'spk-other';
}

function speakerInitial(name) {
  const number = speakerNumber(name);
  if (number) return String(number); // "Speaker 2" shows 2, not S
  const first = Array.from((name || '').trim())[0];
  return first ? first.toUpperCase() : '?';
}

function makeSpeakerAvatar(name, spkClass) {
  const avatar = document.createElement('span');
  avatar.className = `spk-avatar ${spkClass || speakerClassForName(name)}`;
  avatar.setAttribute('aria-hidden', 'true');
  avatar.innerText = speakerInitial(name);
  return avatar;
}

function setShown(el, shown) {
  if (el) el.classList.toggle('hidden', !shown);
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str).replace(/[&<>"']/g, function(m) {
    return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[m];
  });
}

/* Pronunciation dictionary */
let pronunciations = [];  // [{ word, say }], in the order the user added them
const MAX_PRONUNCIATIONS = 500;

function renderPronunciations() {
  const list = document.getElementById('pronunciationList');
  if (activePreview && list.contains(activePreview.button)) stopPreview(); // its Stop button is being rebuilt
  list.innerHTML = '';
  if (pronunciations.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'pron-empty';
    empty.innerText = 'None yet. Add a word to control how it is spoken, for example "Nguyen" → "Win" or "SQL" → "sequel".';
    list.appendChild(empty);
    return;
  }

  pronunciations.forEach((entry, i) => {
    const row = document.createElement('div');
    row.className = 'pron-row';

    const word = document.createElement('input');
    word.type = 'text';
    word.maxLength = 60;
    word.placeholder = 'Word (e.g. SQL)';
    word.setAttribute('aria-label', 'Word');
    word.value = entry.word;
    word.addEventListener('input', () => { entry.word = word.value; });

    const arrow = document.createElement('span');
    arrow.className = 'pron-arrow';
    arrow.innerText = '→';

    const say = document.createElement('input');
    say.type = 'text';
    say.maxLength = 120;
    say.placeholder = 'Say it as (e.g. sequel)';
    say.setAttribute('aria-label', 'Say it as');
    say.value = entry.say;
    say.addEventListener('input', () => { entry.say = say.value; });

    const test = document.createElement('button');
    test.type = 'button';
    test.className = 'btn-sample btn-icon';
    test.innerHTML = icon('play');
    setButtonName(test, 'Hear it with the Narrator voice');
    test.addEventListener('click', () => isPreviewButton(test) ? stopPreview() : testPronunciation(entry, test));

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'pron-remove';
    remove.innerText = '×';
    remove.title = 'Remove this word';
    remove.setAttribute('aria-label', 'Remove this word');
    remove.addEventListener('click', () => {
      pronunciations.splice(pronunciations.indexOf(entry), 1);
      renderPronunciations();
    });

    row.append(word, arrow, say, test, remove);
    list.appendChild(row);
  });
}

function addPronunciation() {
  if (pronunciations.length >= MAX_PRONUNCIATIONS) {
    alert(`The dictionary is limited to ${MAX_PRONUNCIATIONS} words.`);
    return;
  }
  pronunciations.push({ word: '', say: '' });
  renderPronunciations();
  const inputs = document.querySelectorAll('#pronunciationList .pron-row input');
  if (inputs.length >= 2) inputs[inputs.length - 2].focus();
}

// Complete entries only (both boxes filled in).
function activePronunciations() {
  return pronunciations
    .map(p => ({ word: p.word.trim(), say: p.say.trim() }))
    .filter(p => p.word && p.say);
}

// Only the entries that occur in `text`, so preview requests stay small.
function pronunciationsFor(text) {
  const lower = (text || '').toLowerCase();
  return activePronunciations().filter(p => lower.includes(p.word.toLowerCase()));
}

async function testPronunciation(entry, btn) {
  const word = entry.word.trim();
  if (!word || !entry.say.trim()) {
    alert('Fill in both the word and how to say it first.');
    return;
  }
  const voice = document.getElementById('voice_Narrator').value || narratorConfig.voice;
  const token = beginPreview(btn);
  setPreviewLabel(token, 'Stop');
  try {
    const url = await fetchPreviewUrl(voice, getSpeed('Narrator'), word);
    if (token !== previewToken) return;
    await playPreviewUrl(url);
  } catch (err) {
    if (token === previewToken) alert('Could not play it: ' + err.message);
  } finally {
    endPreview(token);
  }
}

/* Projects: save, open and autosave */
const PROJECT_FORMAT = 'VoiceCastTTS project';
const PERFORMANCE_LEVELS = ['off', 'natural', 'lively'];
const PROJECT_VERSION = 1;
const AUTOSAVE_KEY = 'voicecast.autosave.v1';
// Older versions kept the autosave in localStorage, which brought the last script back every time.
const OLD_AUTOSAVE_KEY = AUTOSAVE_KEY;
const MAX_PROJECT_BYTES = 10 * 1024 * 1024;
let projectFileHandle = null;  // set when the browser lets us save back to the same file
let projectFileName = null;
let lastAutosaveJson = null;
let savedScriptText = null; // the script as last saved or opened, to tell whether it changed since

function markProjectSaved() {
  savedScriptText = document.getElementById('scriptInput').value;
}

// True when the Script box has text that changed since the last Save or Open. Only the script counts:
// Continue replaces the script and keeps the cast, voices and speeds.
function projectHasUnsavedScript() {
  const script = document.getElementById('scriptInput').value;
  return !!script.trim() && script !== savedScriptText;
}

function hasOutputAudio() {
  return !!document.getElementById('mainAudio').getAttribute('src');
}

function collectProjectState() {
  const narrVoiceEl = document.getElementById('voice_Narrator');
  return {
    format: PROJECT_FORMAT,
    version: PROJECT_VERSION,
    savedAt: new Date().toISOString(),
    script: document.getElementById('scriptInput').value,
    narrator: {
      voice: (narrVoiceEl && narrVoiceEl.value) || narratorConfig.voice,
      speed: getSpeed('Narrator')
    },
    characters: characters.map(c => {
      const genderSelect = document.getElementById(`gender_${c.id}`);
      const voiceSelect = document.getElementById(`voice_${c.id}`);
      return {
        name: getSpeakerName(c.id) || c.name,
        gender: (genderSelect && genderSelect.value) || c.gender,
        voice: (voiceSelect && voiceSelect.value) || c.voice,
        speed: getSpeed(c.id)
      };
    }),
    pacing: {
      dialogue_turn_pause: parseFloat(document.getElementById('pacingDialogue').value),
      narrator_transition_pause: parseFloat(document.getElementById('pacingNarrator').value)
    },
    pronunciations: activePronunciations(),
    evenVolume: document.getElementById('evenVolumeCheckbox').checked,
    performance: document.getElementById('performanceLevel').value
  };
}

// Applies a saved project to the studio. Returns a list of warnings (e.g. voices that no longer exist).
function applyProjectState(state) {
  if (!state || state.format !== PROJECT_FORMAT || typeof state.script !== 'string') {
    throw new Error('This file is not a VoiceCast project.');
  }
  if (state.version > PROJECT_VERSION) {
    throw new Error('This project was saved by a newer version of VoiceCast Studio.');
  }

  // Validate before touching the current project or allocating any cast DOM cards.
  if (state.script.length > 2_000_000 ||
      (state.characters !== undefined && (!Array.isArray(state.characters) || state.characters.length > 199)) ||
      (Array.isArray(state.characters) && state.characters.some(c => !c || typeof c.name !== 'string' || c.name.length > 100 ||
        (c.voice !== undefined && (typeof c.voice !== 'string' || c.voice.length > 100)))) ||
      (state.pronunciations !== undefined && (!Array.isArray(state.pronunciations) || state.pronunciations.length > MAX_PRONUNCIATIONS))) {
    throw new Error('This project is too large or has invalid speaker details (maximum 199 characters plus the narrator).');
  }

  const knownVoices = new Set((appConfig.available_popular_voices || []).map(v => v.id));
  const warnings = [];
  const pickVoice = (voice, fallback, who) => {
    if (typeof voice === 'string' && knownVoices.has(voice)) return voice;
    warnings.push(`${who}: voice "${voice}" isn't installed, so ${fallback} was used instead.`);
    return fallback;
  };
  const clampSpeed = s => {
    const n = parseFloat(s);
    return isNaN(n) ? 1.0 : Math.max(0.5, Math.min(2.0, n));
  };
  const genders = ['Female', 'Male', 'Child', 'Neutral'];

  document.getElementById('scriptInput').value = state.script;
  updateWordCount();

  const narrator = state.narrator || {};
  const fallbackNarrator = defaultNarrator();
  narratorConfig.voice = pickVoice(narrator.voice, fallbackNarrator.voice, 'Narrator');
  const narrVoiceEl = document.getElementById('voice_Narrator');
  if (narrVoiceEl) narrVoiceEl.value = narratorConfig.voice;
  setSpeed('Narrator', clampSpeed(narrator.speed ?? fallbackNarrator.speed));

  characters = (Array.isArray(state.characters) ? state.characters : [])
    .filter(c => c && typeof c.name === 'string' && c.name.trim())
    .map(c => {
      const name = c.name.trim();
      return {
        id: `char_${nextCharIndex++}`,
        name,
        prevName: name,
        gender: genders.includes(c.gender) ? c.gender : 'Neutral',
        voice: pickVoice(c.voice, 'af_heart', name),
        speed: clampSpeed(c.speed ?? 1.0)
      };
    });

  const pacing = state.pacing || {};
  for (const [sliderId, labelId, value] of [
    ['pacingDialogue', 'pacingDialogueVal', pacing.dialogue_turn_pause],
    ['pacingNarrator', 'pacingNarratorVal', pacing.narrator_transition_pause]
  ]) {
    const n = parseFloat(value);
    if (isNaN(n)) continue;
    const slider = document.getElementById(sliderId);
    slider.value = n;
    document.getElementById(labelId).innerText = slider.value + 's';
  }

  // Projects saved before the dictionary existed simply have none.
  pronunciations = (Array.isArray(state.pronunciations) ? state.pronunciations : [])
    .filter(p => p && typeof p.word === 'string' && typeof p.say === 'string')
    .slice(0, MAX_PRONUNCIATIONS)
    .map(p => ({ word: p.word.slice(0, 60), say: p.say.slice(0, 120) }));
  renderPronunciations();
  document.getElementById('evenVolumeCheckbox').checked = state.evenVolume !== false;
  // Projects saved before Performance existed (or with an unknown value) open at Natural.
  document.getElementById('performanceLevel').value =
    PERFORMANCE_LEVELS.includes(state.performance) ? state.performance : 'natural';

  renderCharacterCards();
  characters.forEach(c => setSpeed(c.id, c.speed));
  renderDynamicToolbarTags();
  autoDetectSpeakers(false);
  return warnings;
}

function setProjectName(name) {
  projectFileName = name;
  document.getElementById('projectName').innerText = name || 'Unsaved project';
}

function suggestedProjectFileName() {
  return projectFileName || 'VoiceCast Project.voicecast.json';
}

// Returns true when the project was saved or downloaded, false when the dialog was closed.
let lastSaveError = null;  // why the last save failed, or null (a closed save window isn't a failure)

async function saveProject(saveAs) {
  const json = JSON.stringify(collectProjectState(), null, 2);
  lastSaveError = null;

  if (window.showSaveFilePicker) {
    let pickerWorks = true;
    let writable = null;
    try {
      if (saveAs || !projectFileHandle) {
        try {
          projectFileHandle = await window.showSaveFilePicker({
            suggestedName: suggestedProjectFileName(),
            types: [{ description: 'VoiceCast project', accept: { 'application/json': ['.json'] } }]
          });
        } catch (err) {
          if (err.name !== 'AbortError') pickerWorks = false;
          throw err;
        }
      }
      writable = await projectFileHandle.createWritable();
      await writable.write(json);
      await writable.close();
      setProjectName(projectFileHandle.name);
      showStatus('Saved', `✓ Project saved to ${projectFileHandle.name}`);
      markProjectSaved();
      return true;
    } catch (err) {
      if (err.name === 'AbortError') return false; // the user closed the save dialog
      if (pickerWorks) {  // the file was chosen but writing it failed (disk full, file locked …): not saved
        console.warn('Could not write the project file:', err);
        if (writable) writable.abort().catch(() => {});  // don't leave a half-written temporary file
        lastSaveError = `The project could not be saved: ${err.message || err.name}. Try again, or use Save As for another place.`;
        showStatus('Not saved', lastSaveError);
        return false;
      }
      console.warn('Save dialog unavailable, downloading instead:', err);
      projectFileHandle = null;
    }
  }

  // Fallback for browsers without the file picker: download to the Downloads folder.
  const blob = new Blob([json], { type: 'application/json' });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = suggestedProjectFileName();
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  setProjectName(link.download);
  showStatus('Saved', `✓ Project downloaded as ${link.download} (check your Downloads folder)`);
  markProjectSaved();
  return true;
}

async function openProject() {
  if (window.showOpenFilePicker) {
    try {
      const [handle] = await window.showOpenFilePicker({
        types: [{ description: 'VoiceCast project', accept: { 'application/json': ['.json'] } }]
      });
      const file = await handle.getFile();
      if (await loadProjectFile(file)) projectFileHandle = handle;
      return;
    } catch (err) {
      if (err.name === 'AbortError') return;
      console.warn('Open dialog unavailable, using file input instead:', err);
    }
  }
  document.getElementById('projectFileInput').click();
}

async function onProjectFileChosen(input) {
  const file = input.files && input.files[0];
  input.value = ''; // so choosing the same file again still triggers a change
  if (file && await loadProjectFile(file)) projectFileHandle = null;
}

async function loadProjectFile(file) {
  try {
    if (file.size > MAX_PROJECT_BYTES) throw new Error('The file is too large to be a VoiceCast project.');
    let state;
    try {
      state = JSON.parse(await file.text());
    } catch (_) {
      throw new Error('This file is not a VoiceCast project.');
    }
    const warnings = applyProjectState(state);
    setProjectName(file.name);
    markProjectSaved();
    document.getElementById('progressStatus').innerText = `✓ Opened ${file.name}`;
    if (warnings.length) alert('The project opened with some changes:\n\n' + warnings.join('\n'));
    autosaveNow();
    return true;
  } catch (err) {
    alert('Could not open project: ' + err.message);
    return false;
  }
}

// Autosave keeps a copy of the current work for this browser tab only (sessionStorage), so an
// accidental refresh loses nothing, but closing the tab and opening the studio again starts empty.
function autosaveNow() {
  try {
    const state = collectProjectState();
    state.projectFileName = projectFileName;
    state.savedScriptText = savedScriptText;  // so a refresh doesn't make a saved story look unsaved
    const json = JSON.stringify({ ...state, savedAt: undefined });
    if (json === lastAutosaveJson) return;
    lastAutosaveJson = json;
    sessionStorage.setItem(AUTOSAVE_KEY, JSON.stringify(state));
  } catch (err) {
    console.warn('Autosave unavailable:', err);
  }
}

function restoreAutosave() {
  try { localStorage.removeItem(OLD_AUTOSAVE_KEY); } catch (_) { /* storage blocked */ }
  try {
    const saved = JSON.parse(sessionStorage.getItem(AUTOSAVE_KEY) || 'null');
    if (!saved || typeof saved.script !== 'string') return;
    if (!(appConfig.available_popular_voices || []).length) return; // voice list didn't load; keep the autosave for next time
    const warnings = applyProjectState(saved);
    setProjectName(saved.projectFileName || null);
    savedScriptText = typeof saved.savedScriptText === 'string' ? saved.savedScriptText : null;
    const when = saved.savedAt ? new Date(saved.savedAt).toLocaleString() : 'earlier';
    document.getElementById('progressStatus').innerText =
      `↺ Restored your work from before the page reloaded (${when}).`;
    if (warnings.length) console.warn('Autosave restored with changes:', warnings);
  } catch (err) {
    console.warn('Could not restore autosave:', err);
  }
}

function startProjectFeatures() {
  renderPronunciations();
  restoreAutosave();
  if (!(appConfig.available_popular_voices || []).length) return; // don't overwrite the autosave with an empty studio
  autosaveNow();
  setInterval(autosaveNow, 3000);
  window.addEventListener('beforeunload', autosaveNow);
  projectsReady = true;  // Ctrl+S (handleShortcut) saves from now on
}

/* Light / Dark Theme
   theme.js applies the theme before the page is drawn; this button cycles
   Auto (follow Windows) -> Light -> Dark and saves the choice with the user settings. */
const THEME_LABELS = { system: 'Auto', light: 'Light', dark: 'Dark' };
const THEME_ORDER = ['system', 'light', 'dark'];

function currentThemeChoice() {
  return document.documentElement.getAttribute('data-theme-choice') || 'system';
}

function updateThemeToggle() {
  const btn = document.getElementById('themeToggle');
  if (!btn) return;
  const choice = currentThemeChoice();
  btn.innerText = `Theme: ${THEME_LABELS[choice]}`;
  btn.title = choice === 'system'
    ? 'Colors follow your Windows setting. Click for light, dark, or automatic.'
    : `Colors are always ${THEME_LABELS[choice].toLowerCase()}. Click to change.`;
}

function cycleTheme() {
  const next = THEME_ORDER[(THEME_ORDER.indexOf(currentThemeChoice()) + 1) % THEME_ORDER.length];
  window.applyTheme(next);
  userSettings.theme = next;
  saveUserSettings();
}

// Redraw the colors that live in code (the waveform) whenever the theme changes.
document.addEventListener('voicecast-theme', () => {
  updateThemeToggle();
  const audio = document.getElementById('mainAudio');
  drawWaveform(audio && audio.duration ? audio.currentTime / audio.duration : 0);
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !e.defaultPrevented && activePreview) {
    e.preventDefault();
    stopPreview();
  }
});

/* Script Assistant
   Optional and off by default. When on, a panel in the Script zone asks a model in an AI program
   on this computer (LM Studio, Ollama and others, see ai_programs.py) to write a script or split
   pasted text into speakers. The page never talks to the program itself: the studio server does,
   at /api/llm/*. */
let aiMode = 'write';
let aiPrograms = [];  // from /api/config: [{id, name, default_url, kind}], in ai_programs.PROGRAMS order

function aiProgram() {
  return aiPrograms.find(p => p.id === (userSettings.llm_program || 'lmstudio')) || aiPrograms[0]
    || { id: 'lmstudio', name: 'LM Studio', kind: 'lmstudio', default_url: 'http://127.0.0.1:1234' };
}

// The program's name for messages ("Ollama"); Other reads "your AI program" inside a sentence.
function aiProgramName(midSentence = false) {
  const p = aiProgram();
  return p.id === 'other' && midSentence ? 'your AI program' : p.name;
}

function aiProgramUsualUrl(p) {
  return p.kind === 'lmstudio' ? (aiDefaults().base_url || p.default_url) : p.default_url;
}

// Picking a program fills in its usual address, unless the box holds an address the person typed.
function onAiProgramChange() {
  const chosen = aiPrograms.find(p => p.id === document.getElementById('aiProgram').value);
  const box = document.getElementById('aiBaseUrl');
  const usual = aiPrograms.map(aiProgramUsualUrl).filter(Boolean);
  if (chosen && aiProgramUsualUrl(chosen) && (!box.value.trim() || usual.includes(box.value.trim().replace(/\/+$/, '')))) {
    box.value = aiProgramUsualUrl(chosen);
  }
  setShown(document.getElementById('aiContextRow'), !!chosen && chosen.kind === 'basic');
}
let aiModels = [];
let aiStatusChecked = false;

const AI_PLACEHOLDERS = {
  write: 'Describe the script you want, e.g. a 3-minute bedtime story about a fox and an owl',
  split: "Paste the text to split into speakers. Words won't be changed.",
  continue: 'What should happen next? (optional)',
};
const AI_BUTTON_LABELS = { write: 'Write Script', split: 'Split into Speakers', continue: 'Write Next Chapter' };

const AI_LENGTH_WORDS = { short: 300, medium: 800, long: 2000 };
let aiModelInfo = null; // what "Write new" uses for the chosen model (from /api/llm/model_settings)

function applyAiLength() {
  const length = AI_LENGTH_WORDS[userSettings.llm_length] ? userSettings.llm_length : 'medium';
  document.querySelectorAll('.ai-length').forEach(b => {
    const on = b.dataset.value === length;
    b.classList.toggle('is-active', on);
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
    b.title = `About ${AI_LENGTH_WORDS[b.dataset.value].toLocaleString()} words`;
  });
}

function setAiLength(length) {
  if (aiBusy || !AI_LENGTH_WORDS[length]) return;
  userSettings.llm_length = length;
  applyAiLength();
  saveUserSettings();
}

function aiModelPrompt(key) {
  return (userSettings.llm_model_prompts || {})[key] || { text: '', use_own: false };
}

function setAiModelPrompt(key, entry) {
  const prompts = userSettings.llm_model_prompts = { ...(userSettings.llm_model_prompts || {}) };
  if (entry.text.trim() || entry.use_own) prompts[key] = entry; else delete prompts[key];
}

function aiModelName(info) {
  return info.quantization ? `${info.display_name} (${info.quantization})` : info.display_name;
}

async function refreshAiModelInfo() {
  const key = document.getElementById('aiModel').value;
  aiModelInfo = null;
  if (key) {
    let data;
    try {
      const res = await fetch('/api/llm/model_settings?model=' + encodeURIComponent(key));
      data = await res.json();
    } catch (err) {
      data = { success: false, error: 'The studio server is not responding.' };
    }
    if (document.getElementById('aiModel').value !== key) return; // the choice changed meanwhile
    aiModelInfo = data.success ? data : { error: data.error };
  }
  renderAiPromptLine();
  if (aiSettingsOpen) renderAiSettings();
}

function renderAiPromptLine() {
  const box = document.getElementById('aiOwnBox');
  const link = document.getElementById('aiPromptLink');
  const info = aiModelInfo;
  let message = '';
  let linkLabel = '';
  let showBox = false;
  if (aiMode === 'split') {
    message = "Split uses VoiceCast's word-for-word instructions.";
    linkLabel = 'Edit';
  } else if (info && info.error) {
    message = info.error;
  } else if (info) {
    const name = aiModelName(info);
    const note = info.lm_studio.note;
    if (info.source === 'own' && info.own.use_own) {
      message = `Using your own instructions for ${name}.`;
      linkLabel = 'Edit';
    } else if (info.source === 'lm_studio') {
      message = `Using your LM Studio prompt for ${name}.`;
      linkLabel = 'View';
    } else {
      message = note === 'not_lm_studio' ? `Using VoiceCast's instructions for ${name}. You can add your own instructions (optional).`
        : note === 'several' ? `LM Studio has more than one settings file that could be ${name}, so its prompt isn't used. You can add your own instructions (optional).`
        : note === 'preset' ? `${name} uses an LM Studio preset, which VoiceCast can't read yet. You can add your own instructions (optional).`
        : note === 'unreadable' ? `Couldn't read LM Studio's settings for ${name}. You can add your own instructions (optional).`
        : `No LM Studio prompt for ${name}. You can add your own instructions (optional).`;
      showBox = true;
      box.value = info.own.text || '';
    }
  }
  document.getElementById('aiPromptText').innerText = message;
  link.innerText = linkLabel;
  setShown(link, !!linkLabel);
  setShown(box, showBox);
}

function saveAiOwnBox() {
  const key = document.getElementById('aiModel').value;
  if (!key) return;
  const entry = { text: document.getElementById('aiOwnBox').value, use_own: aiModelPrompt(key).use_own };
  setAiModelPrompt(key, entry);
  saveUserSettings();
  if (aiModelInfo && !aiModelInfo.error) aiModelInfo.own = entry;
}

let aiSettingsOpen = false;

function setAiSettingsStatus(text, kind = '') {
  const el = document.getElementById('aiSettingsStatus');
  el.innerText = text;
  el.classList.remove('is-ok', 'is-warn', 'is-error');
  if (kind) el.classList.add(`is-${kind}`);
}

function toggleAiSettings() {
  if (aiSettingsOpen) closeAiSettings(); else openAiSettings();
}

function openAiSettings() {
  aiSettingsOpen = true;
  const link = document.getElementById('aiSettingsLink');
  link.setAttribute('aria-expanded', 'true');
  link.innerText = 'Hide instructions';
  setAiSettingsStatus(''); // re-renders after a refresh keep the last "Saved." message
  renderAiSettings();
  setShown(document.getElementById('aiSettings'), true);
  document.getElementById('aiSettings').scrollIntoView({ block: 'nearest' });
}

function closeAiSettings() {
  aiSettingsOpen = false;
  const link = document.getElementById('aiSettingsLink');
  link.setAttribute('aria-expanded', 'false');
  link.innerText = 'Edit instructions';
  setShown(document.getElementById('aiSettings'), false);
}

function aiDefaults() {
  return appConfig?.llm_defaults || {};
}

function describeAiParams(params) {
  const labels = { temperature: 'temperature', top_p: 'top-p', top_k: 'top-k', repeat_penalty: 'repeat penalty' };
  const parts = Object.entries(labels).filter(([key]) => key in params).map(([key, label]) => `${label} ${params[key]}`);
  if (params.stop?.length) parts.push(`${params.stop.length} stop word${params.stop.length === 1 ? '' : 's'}`);
  return parts.length ? `LM Studio settings used for Write new: ${parts.join(', ')}.`
    : 'No saved LM Studio settings for this model; VoiceCast uses temperature 0.8.';
}

function renderAiSettings() {
  const info = aiModelInfo && !aiModelInfo.error ? aiModelInfo : null;
  const own = aiModelPrompt(document.getElementById('aiModel').value);
  const lmPrompt = info ? info.lm_studio.system_prompt : '';
  document.getElementById('aiSetModelName').innerText = info ? aiModelName(info) : 'no model chosen';
  const useLm = document.getElementById('aiUseLm');
  useLm.disabled = !lmPrompt;
  useLm.checked = !!lmPrompt && !own.use_own;
  document.getElementById('aiUseOwn').checked = !useLm.checked;
  document.getElementById('aiLmPrompt').value = lmPrompt || 'This model has no prompt saved in LM Studio.';
  document.getElementById('aiOwnText').value = own.text;
  document.getElementById('aiLmParams').innerText = info ? describeAiParams(info.lm_studio.params) : '';
  document.getElementById('aiSplitText').value = userSettings.llm_instructions?.split || aiDefaults().instructions?.split || '';
  const isLm = aiProgram().kind === 'lmstudio';
  for (const id of ['aiUseLmRow', 'aiLmPrompt', 'aiLmParams']) setShown(document.getElementById(id), isLm);
  const programSelect = document.getElementById('aiProgram');
  programSelect.innerHTML = '';
  for (const p of aiPrograms) programSelect.add(new Option(p.id === 'other' ? 'Other (same request format)' : p.name, p.id));
  programSelect.value = aiProgram().id;
  document.getElementById('aiBaseUrl').value = userSettings.llm_base_url || aiProgramUsualUrl(aiProgram()) || 'http://127.0.0.1:1234';
  document.getElementById('aiContextLength').value = userSettings.llm_context_length || 4096;
  setShown(document.getElementById('aiContextRow'), aiProgram().kind === 'basic');
  document.getElementById('aiTimeout').value = Math.round((userSettings.llm_timeout_seconds || 180) / 60);
}

// Applies a change and saves it; if the server refuses, the change is undone so later saves still work.
async function saveAiSetting(apply) {
  setAiSettingsStatus('Saving…');
  await settingsSaved();
  const before = JSON.stringify(userSettings);
  apply();
  const result = await saveUserSettings();
  if (!result.success) userSettings = JSON.parse(before);
  return result;
}

async function saveAiWriteSettings() {
  const key = document.getElementById('aiModel').value;
  if (!key) { setAiSettingsStatus('Pick a model first.', 'warn'); return; }
  const hasLmPrompt = !!(aiModelInfo && !aiModelInfo.error && aiModelInfo.lm_studio.system_prompt);
  const entry = { text: document.getElementById('aiOwnText').value,
                  use_own: hasLmPrompt && document.getElementById('aiUseOwn').checked };
  const result = await saveAiSetting(() => setAiModelPrompt(key, entry));
  if (!result.success) { setAiSettingsStatus(result.error, 'error'); return; }
  await refreshAiModelInfo();
  setAiSettingsStatus('Saved.', 'ok');
}

async function saveAiSplitSettings() {
  const text = document.getElementById('aiSplitText').value.trim();
  const isDefault = !text || text === (aiDefaults().instructions?.split || '').trim();
  const result = await saveAiSetting(() => {
    const instructions = { ...(userSettings.llm_instructions || {}) };
    if (isDefault) delete instructions.split; else instructions.split = text;
    userSettings.llm_instructions = instructions;
  });
  if (!result.success) { setAiSettingsStatus(result.error, 'error'); return; }
  setAiSettingsStatus('Saved.', 'ok');
}

async function resetAiSplitSettings() {
  const result = await saveAiSetting(() => {
    const instructions = { ...(userSettings.llm_instructions || {}) };
    delete instructions.split;
    userSettings.llm_instructions = instructions;
  });
  if (!result.success) { setAiSettingsStatus(result.error, 'error'); return; }
  document.getElementById('aiSplitText').value = aiDefaults().instructions?.split || '';
  setAiSettingsStatus("Reset to VoiceCast's default.", 'ok');
}

async function saveAiConnection() {
  const url = document.getElementById('aiBaseUrl').value.trim();
  const minutes = Number(document.getElementById('aiTimeout').value);
  const program = aiPrograms.find(p => p.id === document.getElementById('aiProgram').value) || aiProgram();
  const context = Number(document.getElementById('aiContextLength').value);
  if (!Number.isInteger(minutes) || minutes < 1 || minutes > 30) {
    setAiSettingsStatus('The time limit must be 1 to 30 minutes.', 'warn');
    return;
  }
  if (program.kind === 'basic' && (!Number.isInteger(context) || context < 2048 || context > 262144)) {
    setAiSettingsStatus('The longest text must be a whole number from 2,048 to 262,144.', 'warn');
    return;
  }
  const result = await saveAiSetting(() => {
    userSettings.llm_program = program.id;
    userSettings.llm_base_url = url;
    userSettings.llm_timeout_seconds = minutes * 60;
    if (program.kind === 'basic') userSettings.llm_context_length = context;
  });
  if (!result.success) { setAiSettingsStatus(result.error, 'error'); return; }
  setAiSettingsStatus('Saved. Checking the connection…', 'ok');
  await refreshAiStatus();
  // The panel's status line is scrolled out of sight while the editor is open, so repeat the result here.
  const status = document.getElementById('aiStatus');
  const kind = ['error', 'warn'].find(k => status.classList.contains(`is-${k}`)) || 'ok';
  setAiSettingsStatus(`Saved. ${status.innerText}`, kind);
}

function setAiStatus(text, kind = '') {
  const el = document.getElementById('aiStatus');
  el.innerText = text;
  el.classList.remove('is-ok', 'is-warn', 'is-error');
  if (kind) el.classList.add(`is-${kind}`);
}

function applyAiEnabled() {
  const on = !!userSettings.ai_writer_enabled;
  setShown(document.getElementById('aiPanel'), on);
  const toggle = document.getElementById('aiToggle');
  toggle.setAttribute('aria-pressed', on ? 'true' : 'false');
  toggle.title = on ? 'Hide the Script Assistant' : 'Show the Script Assistant (needs an AI program such as LM Studio)';
}

async function toggleAiWriter() {
  userSettings.ai_writer_enabled = !userSettings.ai_writer_enabled;
  applyAiEnabled();
  await saveUserSettings();
  if (userSettings.ai_writer_enabled && document.getElementById('aiPanel').open) refreshAiStatus();
}

async function refreshAiStatus() {
  aiStatusChecked = true;
  setAiStatus(`Checking ${aiProgramName(true)}…`);
  const select = document.getElementById('aiModel');
  let data;
  try {
    const res = await fetch('/api/llm/status');
    data = await res.json();
  } catch (err) {
    data = { success: false, error: 'The studio server is not responding.' };
  }
  aiModels = data.success ? data.models : [];
  select.innerHTML = '';
  for (const m of aiModels) {
    const opt = document.createElement('option');
    opt.value = m.key;
    opt.textContent = aiProgram().kind === 'ollama' && m.ready ? `${m.display_name} (ready)` : m.display_name;
    select.appendChild(opt);
  }
  const remembered = aiModels.find(m => m.key === (data.last_model || userSettings.llm_model));
  if (remembered) select.value = remembered.key;
  updateAiWriteButton();
  refreshAiModelInfo();
  if (!data.success) {
    setAiStatus(data.error, 'error');
  } else if (!aiModels.length) {
    setAiStatus(aiProgram().kind === 'lmstudio'
      ? 'No model loaded. Load one in LM Studio, then press the refresh button next to the model list.'
      : `${aiProgramName()} didn't list any models. Load a model in ${aiProgramName(true)}, then press the refresh button next to the model list.`, 'warn');
  } else {
    setAiStatus(aiConnectedText(aiModels.find(m => m.key === select.value)), 'ok');
  }
}

// "LM Studio connected: Fablewright 12B loaded." / "Ollama connected: qwen3:8b (loads when used)."
function aiConnectedText(model) {
  return `${aiProgramName()} connected: ${model.display_name}${model.ready === false ? ' (loads when used)' : ' loaded'}.`;
}

function updateAiWriteButton() {
  const btn = document.getElementById('aiWriteBtn');
  btn.disabled = !aiModels.length;
  btn.innerText = AI_BUTTON_LABELS[aiMode];
}

function onAiModelChange() {
  userSettings.llm_model = document.getElementById('aiModel').value;
  saveUserSettings();
  const chosen = aiModels.find(m => m.key === userSettings.llm_model);
  if (chosen) setAiStatus(aiConnectedText(chosen), 'ok');
  refreshAiModelInfo();
}

function setAiMode(mode) {
  if (aiBusy) return;
  aiMode = AI_BUTTON_LABELS[mode] ? mode : 'write';
  for (const [id, m] of [['aiModeWrite', 'write'], ['aiModeSplit', 'split'], ['aiModeContinue', 'continue']]) {
    const b = document.getElementById(id);
    b.classList.toggle('is-active', m === aiMode);
    b.setAttribute('aria-pressed', m === aiMode ? 'true' : 'false');
  }
  updateAiPlaceholder();
  updateAiWriteButton();
  setShown(document.getElementById('aiLengths'), aiMode !== 'split');
  setShown(document.getElementById('aiSaveChoice'), false);
  renderAiPromptLine();
}

let aiBusy = false;
let aiPending = null; // a finished result waiting for Replace / Add to end / Cancel
let aiTimer = null;
let aiProgress = { words: 0, thinking: false }; // latest counter from the AI program's streamed answer
let aiStartedAt = 0;

const STUDIO_TITLE = document.title;

function resetTabTitle() {
  document.title = STUDIO_TITLE;
}
window.addEventListener('focus', resetTabTitle);

function applyAiAutoAudio() {
  document.getElementById('aiAutoAudio').checked = !!userSettings.llm_auto_audio;
}

function setAiAutoAudio(on) {
  userSettings.llm_auto_audio = !!on;
  saveUserSettings();
}

function updateAiPlaceholder() {
  document.getElementById('aiRequest').placeholder = AI_PLACEHOLDERS[aiMode];
}

function applyAiAutoSplit() {
  document.getElementById('aiAutoSplit').checked = !!userSettings.llm_auto_split;
}

function setAiAutoSplit(on) {
  userSettings.llm_auto_split = !!on;
  saveUserSettings();
}

// The last automatic split, so Undo can put back what the AI wrote: the Script box before and after it.
let aiSplitUndo = null;

function showAiSplitNote(changedLines) {
  document.getElementById('aiSplitSummary').innerText =
    `Split ${changedLines} line${changedLines === 1 ? '' : 's'} into narration and dialogue.`;
  setShown(document.getElementById('aiSplitNote'), true);
}

function hideAiSplitNote() {
  aiSplitUndo = null;
  setShown(document.getElementById('aiSplitNote'), false);
}

function undoAiSplit() {
  if (!aiSplitUndo) return;
  const textarea = document.getElementById('scriptInput');
  if (textarea.readOnly) return;  // the Assistant is changing the script right now
  if (textarea.value !== aiSplitUndo.after &&
      !confirm('You changed the script after the split. Undo the split anyway? Your changes since then will be lost.')) {
    return;
  }
  textarea.value = aiSplitUndo.before;
  textarea.dispatchEvent(new Event('input', { bubbles: true })); // word count, autosave
  autoDetectSpeakers();
  hideAiSplitNote();
  const audio = hasOutputAudio() || document.getElementById('generateBtn').disabled;
  setAiStatus(audio
    ? 'Split undone: the script is back as the AI wrote it. Press Generate to make the audio again.'
    : 'Split undone: the script is back as the AI wrote it.', 'ok');
}

// "Make the audio when the script is ready": presses Generate once the script is in.
// Nothing plays by itself; the tab title says when the audio is ready.
async function autoMakeAudio() {
  if (document.getElementById('generateBtn').disabled) {
    setAiStatus("Audio is already being made; press Generate when it's done.", 'warn');
    return;
  }
  if (await generateAudio()) document.title = `✓ Audio ready · ${STUDIO_TITLE}`;
}

function aiElapsedText(seconds) {
  return seconds < 60 ? `${seconds} s` : `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
}

function aiBusyText() {
  const verb = aiMode === 'split' ? 'Splitting' : 'Writing';
  const time = aiElapsedText(Math.round((Date.now() - aiStartedAt) / 1000));
  const words = aiProgress.words;
  // While a short story is topped up or given an ending (lm_studio.write_script), the counter says so.
  // A long text for Split my text goes in parts (lm_studio.split_pieces): the counter says which.
  const phase = aiProgress.phase === 'more' ? ` · adding more (${aiProgress.round} of ${aiProgress.rounds})`
    : aiProgress.phase === 'ending' ? ' · adding an ending'
    : aiProgress.pieces > 1 ? ` · part ${aiProgress.piece} of ${aiProgress.pieces}` : '';
  if (words > 0) return `${verb}… about ${words.toLocaleString('en-US')} word${words === 1 ? '' : 's'}${phase} · ${time}`;
  if (aiProgress.loading) return `Loading the model in ${aiProgramName()}… ${time}`;
  if (aiProgress.thinking) return `Thinking…${phase} ${time}`;
  return `${verb}…${phase} ${time}`;
}

function showAiBusyStatus() {
  setAiStatus(aiBusyText());
}

// Reads /api/llm/write_script's events: progress updates the counter; returns the final result.
async function readAiStream(res) {
  if (!res.ok || !res.body) return { success: false, error: 'The studio server is not responding.' };
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let result = null;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split('\n\n');
    buffer = events.pop();
    for (const rawEvt of events) {
      const trimmed = rawEvt.trim();
      if (!trimmed.startsWith('data: ')) continue;
      let message;
      try { message = JSON.parse(trimmed.substring(6)); } catch (err) { continue; }
      if (message.type === 'progress') {
        aiProgress = { words: message.words || 0, thinking: !!message.thinking, loading: !!message.loading,
          phase: message.phase || '', round: message.round || 0, rounds: message.rounds || 0,
          piece: message.piece || 0, pieces: message.pieces || 0 };
        showAiBusyStatus();
      } else if (message.type === 'result') {
        result = message;
      }
    }
  }
  return result || { success: false, error: 'The studio server stopped before the script was finished.' };
}

function aiCastForRequest() {
  return characters.map(c => ({ name: c.name, voice_type: c.gender || 'Neutral' }));
}

function setAiBusy(busy) {
  aiBusy = busy;
  const btn = document.getElementById('aiWriteBtn');
  btn.classList.toggle('previewing', busy); // same highlight as a playing sample
  if (busy) btn.innerHTML = `${icon('stop')} Stop`; else btn.innerText = AI_BUTTON_LABELS[aiMode];
  btn.disabled = !busy && !aiModels.length;
  document.querySelectorAll('.ai-length, .ai-mode').forEach(b => { b.disabled = busy; });
  clearInterval(aiTimer);
  if (busy) {
    aiStartedAt = Date.now();
    aiProgress = { words: 0, thinking: false };
    showAiBusyStatus();
    aiTimer = setInterval(showAiBusyStatus, 1000);
  }
}

function aiWriteOrStop() {
  if (aiBusy) stopAiWrite(); else runAiWrite();
}

async function stopAiWrite() {
  try { await fetch('/api/llm/cancel', { method: 'POST' }); } catch (err) { /* the write reports it */ }
}

function hideAiRows() {
  for (const id of ['aiChoice', 'aiDiffs', 'aiSaveChoice']) setShown(document.getElementById(id), false);
  aiPending = null;
}

// Checks what's needed, then sends. Continue asks to save first when the story is unsaved or has audio.
function runAiWrite() {
  const request = document.getElementById('aiRequest').value.trim();
  const script = document.getElementById('scriptInput').value;
  hideAiRows();
  if (aiMode === 'continue') {
    if (!script.trim()) {
      setAiStatus('Write or open a story first; Continue writes its next chapter.', 'warn');
      return;
    }
    if (document.getElementById('generateBtn').disabled) { // its audio would land under the new chapter
      setAiStatus('Audio is being made for this story. Wait for it to finish, or press Stop, then continue.', 'warn');
      return;
    }
    if (projectHasUnsavedScript() || hasOutputAudio()) {
      setShown(document.getElementById('aiSaveChoice'), true);
      setAiStatus('');
      return;
    }
  } else if (!request) {
    setAiStatus(aiMode === 'split' ? 'Paste the text to split first.' : 'Describe the script you want first.', 'warn');
    document.getElementById('aiRequest').focus();
    return;
  }
  sendAiRequest();
}

async function aiSaveChoice(choice) {
  if (choice === 'cancel') {
    setShown(document.getElementById('aiSaveChoice'), false);
    setAiStatus('Left your script as it was.');
    return;
  }
  const buttons = document.querySelectorAll('#aiSaveChoice button');
  if (choice === 'save') {
    buttons.forEach(b => { b.disabled = true; }); // no second click while the save window is open
    let saved = false;
    try { saved = await saveProject(false); } finally { buttons.forEach(b => { b.disabled = false; }); }
    if (!saved) {  // dialog closed or the file couldn't be written: the row stays
      if (lastSaveError) setAiStatus(lastSaveError, 'error');
      return;
    }
  }
  setShown(document.getElementById('aiSaveChoice'), false);
  sendAiRequest();
}

// The Script box and the buttons that change it (speaker tags, Format Prose, Import, Reset Sample, Open).
function lockScript(locked) {
  document.getElementById('scriptInput').readOnly = locked;
  document.querySelectorAll('#dynamicTagButtons button, #btnFormatProse, [data-click=loadSampleScript], [data-click=openProject]')
    .forEach(b => { b.disabled = locked; });
  importScriptLocked = locked;
  updateImportButton();
}

async function sendAiRequest() {
  if (aiBusy) return;
  resetTabTitle();
  const mode = aiMode;
  const request = document.getElementById('aiRequest').value.trim();
  const body = { mode, length: userSettings.llm_length || 'medium', model: document.getElementById('aiModel').value,
                 cast: aiCastForRequest(),
                 text: mode === 'continue' ? document.getElementById('scriptInput').value : request };
  if (mode === 'continue') body.request = request;
  if (mode === 'continue') lockScript(true);  // the new chapter replaces the box, so edits now would be lost
  setAiBusy(true);
  hideAiSplitNote();  // a new script is on its way; the last split can't be undone any more
  await settingsSaved();
  let data;
  try {
    const res = await fetch('/api/llm/write_script', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    data = await readAiStream(res);
  } catch (err) {
    data = { success: false, error: 'The studio server is not responding.' };
  }
  if (mode === 'continue') lockScript(false);
  setAiBusy(false);
  if (!data.success) {
    setAiStatus(data.error, data.stopped ? '' : 'error');
    return;
  }
  data.mode = mode;
  if (mode === 'continue') {
    insertAiScript(data, 'replace', true);
  } else if (document.getElementById('scriptInput').value.trim()) {
    aiPending = data;
    setShown(document.getElementById('aiChoice'), true);
    setAiStatus('Script ready. Choose where it goes.', 'ok');
  } else {
    insertAiScript(data, 'replace');
  }
}

function aiInsertChoice(choice) {
  setShown(document.getElementById('aiChoice'), false);
  const result = aiPending;
  aiPending = null;
  if (!result || choice === 'cancel') {
    setAiStatus('Left your script as it was.');
    return;
  }
  insertAiScript(result, choice);
}

function insertAiScript(result, how, continued = false) {
  const textarea = document.getElementById('scriptInput');
  const namesBefore = new Set(characters.map(c => c.name.toLowerCase()));
  const current = textarea.value.replace(/\s+$/, '');
  const place = script => (how === 'append' && current ? `${current}\n${script}` : script);
  // Only the AI's new text is split (never Split my text, which must keep the words as they are).
  const split = userSettings.llm_auto_split && result.mode !== 'split' ? splitNarrationAndDialogue(result.script) : null;
  textarea.value = place(split && split.changedLines ? split.text : result.script);
  if (split && split.changedLines) {
    aiSplitUndo = { before: place(result.script), after: textarea.value };
    showAiSplitNote(split.changedLines);
  }
  textarea.dispatchEvent(new Event('input', { bubbles: true })); // word count, autosave
  applyAiCast(result.cast, namesBefore);
  showAiDiffs(result.word_differences || []);
  if (continued) {
    // The old audio belonged to the previous chapter, and Save must not overwrite that chapter's file.
    clearOutput();
    projectFileHandle = null;
    setProjectName(null);
  }
  const notes = [];
  if (result.used_fallback) notes.push('Used plain-text mode; check the speaker tags.');
  if (result.warning) notes.push(result.warning);
  const length = result.length_note ? `${result.length_note} ` : '';
  setAiStatus(notes.length ? `${length}${notes.join(' ')}` : `Script added. ${length}Check it, then press Generate.`,
    notes.length || result.stopped_early ? 'warn' : 'ok');
  // Stop while adding more keeps the story, but the owner pressed Stop: no audio by itself.
  if (userSettings.llm_auto_audio && !result.stopped_early) autoMakeAudio();
}

// Built-in suggestions for new characters. Eric is the owner-auditioned conversational reference;
// Everyday voices, named-role voices and saved assignments retain priority.
const AI_TYPE_VOICES = {
  Female: [{ voice: 'af_bella', speed: 1.00 }, { voice: 'bf_emma', speed: 1.00 }, { voice: 'af_nicole', speed: 1.00 }, { voice: 'af_sky', speed: 1.05 }],
  Male: [{ voice: 'am_eric', speed: 1.00 }, { voice: 'am_adam', speed: 1.00 }, { voice: 'bm_daniel', speed: 1.00 }, { voice: 'am_liam', speed: 1.00 }, { voice: 'am_puck', speed: 1.05 }],
  Child: [{ voice: 'af_sky', speed: 1.05 }, { voice: 'am_puck', speed: 1.05 }, { voice: 'af_kore', speed: 1.05 }, { voice: 'am_liam', speed: 1.05 }],
};

// A voice for the nth new speaker of this type: the first suggestion nobody uses yet,
// otherwise the nth one. Neutral picks from FALLBACK_CAST. Null when nothing is available.
function voiceForType(gender, n, usedVoices) {
  const known = new Set((appConfig?.available_popular_voices || []).map(v => v.id));
  const options = [...everydayVoices(gender), ...(AI_TYPE_VOICES[gender] || FALLBACK_CAST)]
    .filter(o => !known.size || known.has(o.voice));
  if (!options.length) return null;
  const pick = options.find(o => !usedVoices.has(o.voice)) || options[n % options.length];
  return { gender, voice: pick.voice, speed: pick.speed };
}

// After a script arrives: detect speakers as usual, then give each NEW speaker the voice type
// the model chose and a matching voice. Speakers that already had a card keep theirs, and a
// speaker named like a voice nickname keeps that voice. No type from the model: the name's guess stays.
function applyAiCast(cast, namesBefore) {
  autoDetectSpeakers(false);
  const isNew = c => !namesBefore.has(c.name.toLowerCase());
  const typeOf = c => {
    const entry = (cast || []).find(e => String(e.name || '').toLowerCase() === c.name.toLowerCase());
    return entry && (AI_TYPE_VOICES[entry.voice_type] || entry.voice_type === 'Neutral') ? entry.voice_type : null;
  };
  // Voices that stay as they are: old speakers, nickname names, and new speakers without a type (their name's guess).
  const used = new Set(characters.filter(c => !isNew(c) || voiceForSpeakerName(c.name) || !typeOf(c)).map(c => c.voice));
  const counts = {};
  for (const entry of cast || []) {
    const declaredType = (AI_TYPE_VOICES[entry.voice_type] || entry.voice_type === 'Neutral') ? entry.voice_type : null;
    const gender = declaredType === 'Neutral' ? (speakerNameType(entry.name) || declaredType) : declaredType;
    const char = characters.find(c => c.name.toLowerCase() === String(entry.name || '').toLowerCase());
    if (!gender || !char || !isNew(char) || voiceForSpeakerName(char.name)) continue;
    const n = counts[gender] = (counts[gender] ?? -1) + 1;
    const role = roleVoice(char.name, used);
    const pick = role && !used.has(role.voice) ? role
      : voiceForType(gender, n, new Set([...used, ...roleVoicesFor(characters.map(c => c.name))]));
    if (!pick) continue;
    Object.assign(char, pick);
    used.add(pick.voice);
  }
  renderCharacterCards();
  characters.forEach(c => setSpeed(c.id, c.speed));
  renderDynamicToolbarTags();
}

function showAiDiffs(diffs) {
  const box = document.getElementById('aiDiffs');
  const list = document.getElementById('aiDiffList');
  list.innerHTML = '';
  setShown(list, false);
  if (!diffs.length) { setShown(box, false); return; }
  document.getElementById('aiDiffSummary').innerText =
    `${diffs.length} change${diffs.length === 1 ? '' : 's'} from your text. Check the script before you generate.`;
  for (const d of diffs) {
    const li = document.createElement('li');
    li.innerText = d.kind === 'added' ? `Added: “${d.result}”`
      : d.kind === 'dropped' ? `Dropped: “${d.original}”`
      : `Changed: “${d.original}” → “${d.result}”`;
    list.appendChild(li);
  }
  setShown(box, true);
}

function toggleAiDiffs() {
  const list = document.getElementById('aiDiffList');
  setShown(list, list.classList.contains('hidden'));
}

// Check LM Studio the first time the panel is opened, not before.
document.getElementById('aiPanel').addEventListener('toggle', (e) => {
  if (e.target.open && !aiStatusChecked) refreshAiStatus();
});

/* ---- Import: text from a .txt or .md file, or a Google Doc shared by link ----
   The page only reads the file; the server tidies it, and only the server talks to Google. */
const MAX_IMPORT_FILE_BYTES = 5 * 1024 * 1024;
const MAX_IMPORT_REQUEST_BYTES = 10 * 1024 * 1024;  // the studio's request limit (MAX_BODY_BYTES)
const IMPORT_TOO_BIG = 'That is too big to import (the limit is 5 MB).';
let importTicket = 0;  // goes up when the window closes, so an answer that arrives later is dropped
let importPending = null;  // {text, words, label} waiting for Replace / Add to end / Cancel
let importBusy = false;
let importRendering = false;     // audio is being made
let importScriptLocked = false;  // Continue the story is writing (lockScript)
const IMPORT_NOT_NOW = 'Wait until the audio is finished and the Script Assistant has stopped writing, then try again.';

// Import is off while audio is made or the script is locked, and stays off until both have ended.
function importBlocked() {
  return importRendering || importScriptLocked;
}

function updateImportButton() {
  document.getElementById('importBtn').disabled = importBlocked();
}

function setImportRendering(on) {
  importRendering = on;
  updateImportButton();
}

function setImportMessage(text, kind = '') {
  const el = document.getElementById('importMessage');
  el.innerText = text;
  el.classList.remove('is-ok', 'is-warn', 'is-error');
  if (kind) el.classList.add(`is-${kind}`);
}

function openImport() {
  importPending = null;
  setShown(document.getElementById('importChoice'), false);
  setImportMessage(importBusy ? 'Still finishing the last import. Try again in a moment.' : '');
  document.getElementById('importLinksAllowed').checked = !!userSettings.import_links_enabled;
  document.getElementById('inboxEnabled').checked = !!userSettings.inbox_enabled;
  updateImportLinkControls();
  loadImportChats();
  const dialog = document.getElementById('importDialog');
  if (!dialog.open) dialog.showModal();
}

function closeImport() {
  importPending = null;
  document.getElementById('importDialog').close();
}

// Close, Escape or closeImport: forget a waiting result and drop one still on its way.
document.getElementById('importDialog').addEventListener('close', (e) => {
  if (e.target.open) return;  // the event comes a moment late; the window was opened again since
  importTicket++;
  importPending = null;
  setShown(document.getElementById('importChoice'), false);
});

function updateImportLinkControls() {
  const on = !!userSettings.import_links_enabled;
  document.getElementById('importLink').disabled = !on || importBusy;
  document.getElementById('importLinkBtn').disabled = !on || importBusy;
  setShown(document.getElementById('importLinksOffNote'), !on);
}

function setImportBusy(busy) {
  importBusy = busy;
  document.getElementById('importFileBtn').disabled = busy;
  updateImportLinkControls();
  updateImportChatControls();
}

/* Saved LM Studio chats (lmstudio_chats.py reads LM Studio's chats folder on this computer). */
let importChats = [];
const IMPORT_CHAT_NOTE = 'A chat saved in LM Studio. Only the AI\'s answers come in, not your own messages.';
const IMPORT_NO_CHATS = 'No saved chats found in LM Studio. Chats in LM Studio\'s chat list appear here.';

async function loadImportChats() {
  const list = document.getElementById('importChatList');
  list.dataset.loading = '1';
  let data;
  try {
    data = await (await fetch('/api/lmstudio/chats')).json();
  } catch (err) {
    data = { success: false, error: 'The studio server is not responding.' };
  }
  importChats = data.success ? data.chats : [];
  list.replaceChildren(...importChats.map(c => new Option(c.model ? `${c.name} (${c.model})` : c.name, c.file)));
  document.getElementById('importChatNote').innerText = !data.success ? data.error
    : importChats.length ? IMPORT_CHAT_NOTE : IMPORT_NO_CHATS;
  delete list.dataset.loading;
  updateImportChatControls();
}

function updateImportChatControls() {
  const off = !importChats.length || importBusy;
  ['importChatList', 'importChatWhich', 'importChatBtn'].forEach(id => { document.getElementById(id).disabled = off; });
}

async function importFromChat() {
  const file = document.getElementById('importChatList').value;
  const chat = importChats.find(c => c.file === file);
  if (!chat) return;
  // Like a script sent from LM Studio: without speaker names, it goes through Format Prose.
  await sendImport('/api/import/lmstudio_chat', { file, which: document.getElementById('importChatWhich').value },
    `Imported "${chat.name}"`, prepareSentScript);
}

async function onImportLinksToggle(el) {
  userSettings.import_links_enabled = el.checked;
  updateImportLinkControls();
  const result = await saveUserSettings();
  if (!result.success) setImportMessage(result.error, 'error');
}

function chooseImportFile() {
  document.getElementById('importFileInput').click();
}

// UTF-8 first; bytes that aren't valid UTF-8 are read as Windows-1252, so older Windows files keep their accents.
function decodeImportBytes(buffer) {
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(buffer);
  } catch (e) {
    return new TextDecoder('windows-1252').decode(buffer);
  }
}

async function onImportFileChosen(input) {
  const file = input.files && input.files[0];
  input.value = '';  // picking the same file again still counts as a change
  if (!file) return;
  if (!/\.(txt|md)$/i.test(file.name)) {
    setImportMessage('Import opens .txt and .md files.', 'error');
    return;
  }
  if (file.size > MAX_IMPORT_FILE_BYTES) {
    setImportMessage(IMPORT_TOO_BIG, 'error');
    return;
  }
  const text = decodeImportBytes(await file.arrayBuffer());
  await sendImport('/api/import/text', { filename: file.name, text }, `Imported ${file.name}`);
}

async function importFromLink() {
  const url = document.getElementById('importLink').value.trim();
  if (!url) {
    setImportMessage('Paste a Google Docs link first.', 'error');
    return;
  }
  await sendImport('/api/import/google_doc', { url }, 'Imported the Google Doc');
}

async function sendImport(path, body, label, prepare = null) {
  if (importBusy) return;
  if (importBlocked()) {
    setImportMessage(IMPORT_NOT_NOW, 'error');
    return;
  }
  const payload = JSON.stringify(body);
  if (new Blob([payload]).size > MAX_IMPORT_REQUEST_BYTES) {  // e.g. 5 MB of accented letters doubles in size
    setImportMessage(IMPORT_TOO_BIG, 'error');
    return;
  }
  const ticket = importTicket;
  setImportBusy(true);
  await settingsSaved();  // a just-ticked "Allow Google Docs links" must reach the server first
  setImportMessage(path.endsWith('google_doc') ? 'Getting the Doc from Google…'
    : path.endsWith('lmstudio_chat') ? 'Reading the chat…' : 'Reading the file…');
  let data;
  try {
    const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: payload });
    data = res.status === 413 ? { success: false, error: IMPORT_TOO_BIG } : await res.json();
  } catch (err) {
    data = { success: false, error: 'The studio server is not responding.' };
  }
  setImportBusy(false);
  if (ticket !== importTicket) return;  // the window was closed (Close or Escape) while this was running
  if (!data.success) {
    setImportMessage(data.error, 'error');
    return;
  }
  importPending = { text: prepare ? prepare(data.text) : data.text, words: data.words, label };
  if (document.getElementById('scriptInput').value.trim()) {
    setShown(document.getElementById('importChoice'), true);
    setImportMessage('Text ready. Choose where it goes.', 'ok');
  } else {
    importChoice('replace');
  }
}

function importChoice(choice) {
  setShown(document.getElementById('importChoice'), false);
  const pending = importPending;
  importPending = null;
  if (!pending || choice === 'cancel') {
    setImportMessage('Left your script as it was.');
    return;
  }
  if (importBlocked()) {  // the window was left open while audio or a new chapter started
    setImportMessage(IMPORT_NOT_NOW, 'error');
    return;
  }
  const textarea = document.getElementById('scriptInput');
  const current = textarea.value.replace(/\s+$/, '');
  textarea.value = choice === 'append' && current ? `${current}\n${pending.text}` : pending.text;
  textarea.dispatchEvent(new Event('input', { bubbles: true }));  // word count, speaker detection, autosave
  closeImport();
  showStatus(`${pending.label}: ${pending.words.toLocaleString()} words.`, 'Check it, then press Generate.');
}

/* ---- Send to VoiceCast: scripts sent from LM Studio (voicecast_mcp.py) ----
   While "Receive scripts from LM Studio" is ticked, the page asks the server every 2 seconds for a
   waiting script and places it like an import (Replace / Add to end / Cancel when the box has text). */
const INBOX_CHECK_MS = 2000;
let inboxTimer = null;
let inboxAsking = false;

function applyInboxEnabled() {
  const on = !!userSettings.inbox_enabled;
  if (on && inboxTimer === null) inboxTimer = setInterval(checkInbox, INBOX_CHECK_MS);
  if (!on && inboxTimer !== null) {
    clearInterval(inboxTimer);
    inboxTimer = null;
  }
}

async function onInboxToggle(el) {
  userSettings.inbox_enabled = el.checked;
  applyInboxEnabled();
  const result = await saveUserSettings();
  if (!result.success) setImportMessage(result.error, 'error');
}

// A script without named speakers gets the Format Prose step, so its quotes go to their speakers.
function prepareSentScript(text) {
  if (extractSpeakersFromText(text).speakers.length) return text;
  return formatProseText(text).blocks.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

// The script stays on the server while the page is busy, and is taken once the page is free.
async function checkInbox() {
  if (inboxAsking || importBlocked() || importBusy || importPending || aiBusy || aiPending) return;
  inboxAsking = true;
  let data = null;
  try {
    const res = await fetch('/api/inbox/take', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    data = await res.json();
  } catch (err) {
    data = null;  // the studio is restarting: ask again next time
  }
  inboxAsking = false;
  if (data && data.success && data.item) receiveSentScript(data.item);
}

function receiveSentScript(item) {
  const text = prepareSentScript(item.text);
  const words = text.trim() ? text.trim().split(/\s+/).length : 0;
  const label = item.title ? `Received "${item.title}" from LM Studio` : 'Received a script from LM Studio';
  if (document.getElementById('scriptInput').value.trim()) {
    openImport();  // clears any earlier choice, so set this one after it
    importPending = { text, words, label };
    setShown(document.getElementById('importChoice'), true);
    setImportMessage(`${label}. Choose where it goes.`, 'ok');
  } else {
    importPending = { text, words, label };
    importChoice('replace');
  }
}

/* Page Actions
   Buttons and fields name their action with data-click / data-input / data-change
   (plus data-speaker or data-value when needed) instead of inline onclick code, which
   lets the server's Content-Security-Policy block any script that isn't in this file. */
const PAGE_ACTIONS = {
  click: {
    toggleMasterPlay: () => toggleMasterPlay(),
    clearOutput: () => clearOutput(),
    cycleTheme: () => cycleTheme(),
    toggleAiWriter: () => toggleAiWriter(),
    refreshAiStatus: () => refreshAiStatus(),
    setAiMode: el => setAiMode(el.dataset.value),
    setAiLength: el => setAiLength(el.dataset.value),
    aiWriteOrStop: () => aiWriteOrStop(),
    aiInsertChoice: el => aiInsertChoice(el.dataset.value),
    aiSaveChoice: el => aiSaveChoice(el.dataset.value),
    undoAiSplit: () => undoAiSplit(),
    toggleAiDiffs: () => toggleAiDiffs(),
    toggleAiSettings: () => toggleAiSettings(),
    openAiSettings: () => openAiSettings(),
    saveAiWriteSettings: () => saveAiWriteSettings(),
    saveAiSplitSettings: () => saveAiSplitSettings(),
    resetAiSplitSettings: () => resetAiSplitSettings(),
    saveAiConnection: () => saveAiConnection(),
    stopRender: () => stopRender(),
    makeNarratorDefault: () => makeNarratorDefault(),
    openProject: () => openProject(),
    openImport: () => openImport(),
    closeImport: () => closeImport(),
    chooseImportFile: () => chooseImportFile(),
    importFromLink: () => importFromLink(),
    importFromChat: () => importFromChat(),
    importChoice: el => importChoice(el.dataset.value),
    saveProject: el => saveProject(el.dataset.value === 'true'),
    manualDetectSpeakers: () => manualDetectSpeakers(),
    loadSampleScript: () => loadSampleScript(),
    formatProseScript: () => formatProseScript(),
    generateAudio: () => generateAudio(),
    toggleBlender: () => toggleBlender(),
    closeBlender: () => closeBlender(),
    addBlendRow: () => addBlendRow(),
    removeBlendRow: el => removeBlendRow(el.dataset.row),
    listenToBlend: el => isPreviewButton(el) ? stopPreview() : listenToBlend(),
    saveBlend: () => saveBlend(),
    suggestBlend: () => suggestBlend(),
    editBlend: el => editBlend(el.dataset.voice),
    deleteBlend: el => deleteBlend(el.dataset.voice),
    skipBack: () => skipBack(),
    skipForward: () => skipForward(),
    addSpeakerCardManually: () => addSpeakerCardManually(),
    addPronunciation: () => addPronunciation(),
    playVoiceSample: el => isPreviewButton(el) ? stopPreview() : playVoiceSample(el.dataset.speaker),
    compareVoiceSample: el => isPreviewButton(el) ? stopPreview() : compareVoiceSample(el.dataset.speaker),
    renameInScript: el => renameInScript(el.dataset.speaker),
    removeSpeakerCard: el => removeSpeakerCard(el.dataset.speaker),
    setSpeed: el => setSpeed(el.dataset.speaker, parseFloat(el.dataset.value)),
    stepSpeed: el => stepSpeed(el.dataset.speaker, parseFloat(el.dataset.value)),
  },
  input: {
    setSpeed: el => setSpeed(el.dataset.speaker, el.value),
    onSpeedNumInput: el => onSpeedNumInput(el.dataset.speaker, el.value),
    onCharacterNameChange: el => onCharacterNameChange(el.dataset.speaker),
    setMasterVolume: el => setMasterVolume(el.value),
    setBlendWeight: el => setBlendWeight(el.dataset.row, el.value),
    showPacingValue: el => { document.getElementById(el.dataset.target).innerText = el.value + 's'; },
  },
  change: {
    liveListeningChanged: () => liveListeningChanged(),
    formatSpeedNum: el => formatSpeedNum(el.dataset.speaker),
    onVoiceChange: el => onVoiceChange(el.dataset.speaker),
    onNarratorVoiceChange: () => onNarratorVoiceChange(),
    onAiModelChange: () => onAiModelChange(),
    onAiProgramChange: () => onAiProgramChange(),
    saveAiOwnBox: () => saveAiOwnBox(),
    setAiAutoAudio: el => setAiAutoAudio(el.checked),
    setAiAutoSplit: el => setAiAutoSplit(el.checked),
    onCharacterGenderChange: el => onCharacterGenderChange(el.dataset.speaker),
    syncLoopCheckboxes: el => syncLoopCheckboxes(el.checked),
    setBlendVoice: el => setBlendVoice(el.dataset.row, el.value),
    onProjectFileChosen: el => onProjectFileChosen(el),
    onImportFileChosen: el => onImportFileChosen(el),
    onImportLinksToggle: el => onImportLinksToggle(el),
    onInboxToggle: el => onInboxToggle(el),
  },
};

for (const type of Object.keys(PAGE_ACTIONS)) {
  document.addEventListener(type, (e) => {
    const el = e.target.closest && e.target.closest(`[data-${type}]`);
    if (!el) return;
    const action = PAGE_ACTIONS[type][el.dataset[type]];
    if (action) action(el, e);
    else console.error(`Unknown ${type} action: ${el.dataset[type]}`);
  });
}

// The waveform reacts to clicks (seek) and hovering (time tooltip).
const waveformWrapperEl = document.getElementById('waveformWrapper');
if (waveformWrapperEl) {
  waveformWrapperEl.addEventListener('pointerdown', startWaveformDrag);
  waveformWrapperEl.addEventListener('mousemove', hoverWaveform);
  waveformWrapperEl.addEventListener('mouseleave', leaveWaveform);
}

// If the logo image can't load, show a microphone instead.
const brandLogoEl = document.getElementById('brandLogo');
function showLogoFallback() {
  setShown(brandLogoEl, false);
  setShown(document.getElementById('fallbackEmoji'), true);
}
if (brandLogoEl) {
  if (brandLogoEl.complete && !brandLogoEl.naturalWidth) showLogoFallback(); // failed before this file loaded
  else brandLogoEl.addEventListener('error', showLogoFallback);
}

/* Keyboard shortcuts
   Ctrl+Enter generates (in the Script Assistant's request box it presses the Assistant's button);
   Space plays or pauses when you aren't typing or on a control; Esc stops audio being made,
   after its other jobs (closing the voice list, stopping a sample). Ctrl+S saves the project (Ctrl+Shift+S
   saves a copy) once the projects are set up. */
let projectsReady = false;
const KEEPS_ITS_KEYS = 'input, textarea, select, button, a, summary, [contenteditable="true"]';
const KEEPS_ITS_ARROWS = 'input, textarea, select, [contenteditable="true"]';

function handleShortcut(e) {
  if (e.defaultPrevented) return;
  if (document.getElementById('importDialog').open) {  // the Import window keeps the keys to itself
    if ((e.ctrlKey || e.metaKey) && (e.key === 'Enter' || e.key.toLowerCase() === 's')) e.preventDefault();
    return;
  }
  const target = e.target instanceof Element ? e.target : null;
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    if (target && target.id === 'aiRequest') {
      if (!aiBusy && !document.getElementById('aiWriteBtn').disabled) aiWriteOrStop();
    } else if (!document.getElementById('generateBtn').disabled) {
      generateAudio();
    }
  } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
    e.preventDefault();  // never the browser's "Save page"
    if (projectsReady) saveProject(e.shiftKey);
  } else if (e.key === ' ' && !e.ctrlKey && !e.metaKey && !e.altKey) {
    if ((target && target.closest(KEEPS_ITS_KEYS)) || document.getElementById('btnPlayMaster').disabled) return;
    e.preventDefault(); // don't scroll the page
    if (!e.repeat) toggleMasterPlay(); // holding Space toggles once
  } else if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
    // Buttons don't use the arrow keys, so ← / → still work right after clicking a skip button.
    if ((target && target.closest(KEEPS_ITS_ARROWS)) || document.getElementById('btnSkipBack').disabled) return;
    e.preventDefault();
    if (e.key === 'ArrowLeft') skipBack(); else skipForward();
  } else if (e.key === 'Escape') {
    stopLiveListening();
    if (describeBusy) {
      e.preventDefault();
      stopDescribe();
      return;
    }
    const stopBtn = document.getElementById('stopBtn');
    if (!stopBtn.classList.contains('hidden') && !stopBtn.disabled) {
      e.preventDefault();
      stopRender();
    }
  }
}
document.addEventListener('keydown', handleShortcut);
window.addEventListener('pagehide', stopLiveListening);

init().then(startProjectFeatures);

