"""Settings Lea can change just by asking Sylvie ("speak faster", "use the Bella voice in English",
"turn off web search"). "if I want to change something of the configuration I can
say it directly to Sylvie and it must change".

Only the settings listed here can be changed this way; each value is checked. Security settings
(the server address, tokens, which actions are allowed, phone access) stay in config.yaml on
purpose. Changes take effect right away and are kept across restarts (state.json → overrides).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Kokoro voices by language, friendly name → voice id (names are what Lea hears in the samples).
VOICES = {
    "en": {
        "heart": "af_heart", "bella": "af_bella", "nicole": "af_nicole", "sarah": "af_sarah", "sky": "af_sky",
        "nova": "af_nova", "alloy": "af_alloy", "aoede": "af_aoede", "kore": "af_kore", "river": "af_river",
        "jessica": "af_jessica", "emma": "bf_emma", "isabella": "bf_isabella", "alice": "bf_alice",
        "lily": "bf_lily", "michael": "am_michael", "adam": "am_adam", "eric": "am_eric", "liam": "am_liam",
        "george": "bm_george", "daniel": "bm_daniel",
    },
    "es": {"dora": "ef_dora", "alex": "em_alex", "heart": "af_heart", "bella": "af_bella", "isabella": "bf_isabella"},
    "fr": {"siwis": "ff_siwis", "heart": "af_heart", "bella": "af_bella", "isabella": "bf_isabella"},
}  # fmt: skip
MODELS = {"fast": "mlx-community/Qwen3.5-9B-4bit", "smart": "mlx-community/Qwen3.8-27B-4bit"}
ON = {"on", "true", "yes", "1", "sí", "si", "oui", "enable", "enabled"}
OFF = {"off", "false", "no", "0", "non", "disable", "disabled"}


# A message that sounds like a settings request. Measured 2026-11-06: the 9B once answered "I set the
# speed back to 1.0" without calling change_setting, so for these Sylvie gets an explicit reminder.
INTENT_RE = re.compile(
    r"\b(voice|voz|voix|speed|velocidad|vitesse|faster|slower|más (rápido|despacio|lento)|mas (rapido|despacio)|"
    r"plus (vite|lentement)|turn (on|off)|switch (on|off|to)|activa|desactiva|enciende|apaga|active|désactive|"
    r"web search|búsqueda|recherche|setting|ajuste|configuraci[oó]n|réglage|paramètre|modelo|model|"
    r"night learning|aprendizaje nocturno|quick replies|shortcuts?)\b",
    re.I,
)
INTENT_NOTE = (
    "This may be a request to change one of your settings. If it is, you MUST call change_setting (or "
    "show_settings to read them); the change only happens through that call. Never say a setting was "
    "changed unless change_setting succeeded in this turn."
)


def looks_like_setting_request(text: str) -> bool:
    return bool(INTENT_RE.search(text))


class BadValue(ValueError):
    pass


@dataclass
class Setting:
    name: str
    path: str  # where it lives in the config
    help: str

    def parse(self, value: str):
        v = str(value).strip().lower()
        if self.name.startswith("voice_"):
            voices = VOICES[self.name[-2:]]
            if v in voices:
                return voices[v]
            if v in voices.values():
                return v
            raise BadValue(f"unknown voice '{value}'. Choose one of: {', '.join(voices)}")
        if self.name == "speech_speed":
            try:
                speed = float(v.replace(",", "."))
            except ValueError as e:
                raise BadValue("speech_speed must be a number, e.g. 1.1 (normal is 1.0)") from e
            if not 0.6 <= speed <= 1.6:
                raise BadValue("speech_speed must be between 0.6 (slow) and 1.6 (fast)")
            return round(speed, 2)
        if self.name == "model":
            if v in MODELS:
                return MODELS[v]
            raise BadValue("model must be 'fast' (9B, quick answers) or 'smart' (27B, slower)")
        if self.name == "proactivity":
            if v in ("off", "when_relevant"):
                return v
            raise BadValue("proactivity must be 'off' or 'when_relevant'")
        if v in ON:
            return True
        if v in OFF:
            return False
        raise BadValue(f"{self.name} must be on or off")


SETTINGS = {
    s.name: s
    for s in [
        Setting("voice_en", "voice.kokoro_voices.en", "Sylvie's English voice"),
        Setting("voice_es", "voice.kokoro_voices.es", "Sylvie's Spanish voice"),
        Setting("voice_fr", "voice.kokoro_voices.fr", "Sylvie's French voice"),
        Setting("speech_speed", "voice.speed", "how fast she speaks (1.0 normal)"),
        Setting(
            "speak_full_answer", "voice.speak_full_answer", "read the whole answer aloud, or only the start"
        ),
        Setting("web_search", "search.enabled", "searching the web"),
        Setting("quick_replies", "quick_replies.enabled", "instant answers to hello/thanks/bye"),
        Setting("shortcuts", "shortcuts.enabled", "starting frequent lookups right away"),
        Setting("proactivity", "proactivity.mode", "bringing up related things on her own"),
        Setting("night_learning", "schedule.enabled", "practising and training at night"),
        Setting("model", "llm.model", "'fast' (9B) or 'smart' (27B)"),
    ]
}


def _set_path(cfg, path: str, value) -> None:
    *parents, last = path.split(".")
    node = cfg
    for key in parents:
        if not isinstance(node.get(key), dict):
            node[key] = {}
        node = node[key]
    node[last] = value


def describe(cfg) -> str:
    """Current values, for Sylvie to read out."""
    lines = []
    for s in SETTINGS.values():
        value = cfg.get_path(s.path)
        if s.name == "model":
            value = next((k for k, m in MODELS.items() if m == value), value)
        elif s.name.startswith("voice_"):
            value = next((k for k, v in VOICES[s.name[-2:]].items() if v == value), value)
        lines.append(f"- {s.name} = {value} ({s.help})")
    return "Current settings:\n" + "\n".join(lines)


def apply_saved(cfg, state: dict) -> None:
    """At startup: re-apply what Lea changed by asking (state.json → settings.overrides)."""
    for path, value in (state.get("settings", {}).get("overrides") or {}).items():
        _set_path(cfg, path, value)


def save_override(cfg, path: str, value) -> None:
    """Sets a config value now and keeps it across restarts (state.json → settings.overrides)."""
    from learning.promote import load_state, save_state

    _set_path(cfg, path, value)
    st = load_state(cfg)
    st.setdefault("settings", {}).setdefault("overrides", {})[path] = value
    save_state(cfg, st)


def change(sylvie, name: str, value: str) -> str:
    """Validates, applies right away, saves. Returns a sentence for Sylvie to confirm."""
    s = SETTINGS.get(name)
    if s is None:
        raise BadValue(f"'{name}' can't be changed by asking. Settings you can change: {', '.join(SETTINGS)}")
    new = s.parse(value)
    cfg = sylvie.cfg
    old = cfg.get_path(s.path)
    save_override(cfg, s.path, new)

    # Make it take effect now.
    if s.path.startswith("voice."):
        sylvie._tts = None  # rebuilt with the new voice/speed on the next sentence
    elif s.name == "night_learning" and getattr(sylvie, "scheduler", None) is not None:
        sylvie.scheduler.enabled = bool(new)
        if not new:
            sylvie.scheduler.preempt()
    elif s.name == "model" and new != old:
        sylvie.llm.unload()
        sylvie.llm.model_id = new  # the next answer loads it (~5-15 s the first time)
    return f"Changed {name} from {old} to {new}. It's saved and works from now on."
