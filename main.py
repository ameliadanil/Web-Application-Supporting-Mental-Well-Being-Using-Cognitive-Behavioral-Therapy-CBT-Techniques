from __future__ import annotations

import os
import uuid
import re
import unicodedata
import random
import hashlib
from enum import Enum
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Pydantic v1/v2 compatibility for ORM responses
try:
    from pydantic import ConfigDict  # type: ignore
except Exception:  # pragma: no cover
    ConfigDict = None  # type: ignore

from sqlmodel import SQLModel, Field as SQLField, create_engine, Session as DBSession, select


class BaseDTO(BaseModel):
    """Base DTO that supports both Pydantic v1 and v2 ORM/attribute parsing."""
    if ConfigDict is not None:  # Pydantic v2
        model_config = ConfigDict(from_attributes=True)
    else:  # Pydantic v1
        class Config:
            orm_mode = True


def to_dto(dto_cls, obj):
    """Convert ORM object to DTO in a Pydantic v1/v2 compatible way."""
    if hasattr(dto_cls, "model_validate"):
        try:
            return dto_cls.model_validate(obj, from_attributes=True)
        except TypeError:
            return dto_cls.model_validate(obj)
    return dto_cls.from_orm(obj)

# --- eksport ---
from io import StringIO
import csv
from fastapi.responses import StreamingResponse, JSONResponse

# ----------------------- Konfiguracja API -----------------------
app = FastAPI(title="AI-CBT Backend (MVP)", version="0.9.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # MVP; w produkcji zawęź
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===================== AI PROVIDER (Ollama / OpenAI / fallback FSM) =====================
# Domyślnie: ollama (bo darmowe lokalnie)
# Ustaw:
#   AI_PROVIDER=ollama   (default)
#   AI_PROVIDER=openai   (wymusza OpenAI; potrzebujesz OPENAI_API_KEY + billing)
AI_PROVIDER = (os.getenv("AI_PROVIDER") or "ollama").strip().lower()

OLLAMA_URL = (os.getenv("OLLAMA_URL") or "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = (os.getenv("OLLAMA_MODEL") or "llama3").strip()

OPENAI_MODEL = (os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()

_openai_client = None
if AI_PROVIDER == "openai":
    # import dopiero jeśli używasz openai (żeby nie rozwalało jak ktoś nie ma paczki)
    try:
        from openai import OpenAI  # type: ignore
    except Exception as e:
        raise RuntimeError("Brakuje paczki 'openai'. Zrób: pip install openai") from e

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("AI_PROVIDER=openai, ale brak OPENAI_API_KEY w zmiennych środowiskowych.")
    _openai_client = OpenAI(api_key=api_key)


def call_ollama(messages: List[Dict[str, str]]) -> str:
    """
    Ollama chat API.
    Wymaga działającego Ollama w systemie:
      - Ollama musi być zainstalowana
      - model pobrany: ollama pull llama3
    """
    r = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={"model": OLLAMA_MODEL, "messages": messages, "stream": False},
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Ollama error {r.status_code}: {r.text}")
    data = r.json()
    return (data.get("message", {}) or {}).get("content", "").strip()


def call_openai(messages: List[Dict[str, str]]) -> str:
    if _openai_client is None:
        raise RuntimeError("OpenAI client nie jest skonfigurowany.")
    resp = _openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=messages,
        temperature=0.8,
    )
    return (resp.choices[0].message.content or "").strip()


def call_ai(messages: List[Dict[str, str]]) -> str:
    """
    Strategia:
    - jeśli AI_PROVIDER=openai -> OpenAI
    - inaczej -> Ollama
    """
    if AI_PROVIDER == "openai":
        return call_openai(messages)
    return call_ollama(messages)


# ------------------ Języki ------------------
class Lang(str, Enum):
    EN = "en"
    PL = "pl"


# ------------------ Normalizacja ------------------
def normalize(s: str) -> str:
    s = s.lower()
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


# ------------------ Safety (PL/EN) ------------------
CRISIS_PATTERNS_PL = [
    r"\bsamoboj\b",
    r"\bsamobojst\w*\b",
    r"\bsamookalecz\w*\b",
    r"\bzabij\w*\b",
    r"\bodebrac?\s+s(o|0)bie\s+zycie\b",
    r"\bnie\s+chce\s+zyc\b",
    r"\bskrzywdz\w*\s+siebie\b",
    r"\bkonczyc?\s+ze\s+sob(a|a)\b",
]
CRISIS_PATTERNS_EN = [
    r"\bsuicid\w*\b",
    r"\bkill\s+myself\b",
    r"\bself[- ]?harm\w*\b",
    r"\bi\s+don'?t\s+want\s+to\s+live\b",
    r"\bend\s+my\s+life\b",
    r"\bhurt\s+myself\b",
]

CRISIS_REPLY_PL = (
    "Widzę, że to bardzo trudne. Nie jestem terapią ani pomocą kryzysową. "
    "Jeśli jesteś w niebezpieczeństwie, skontaktuj się proszę z lokalnymi służbami ratunkowymi "
    "lub zaufaną osobą. W Polsce możesz zadzwonić pod 112 lub skorzystać z całodobowych linii wsparcia."
)
CRISIS_REPLY_EN = (
    "I’m really sorry you’re feeling this way. I’m not a crisis service. "
    "If you’re in danger or thinking about harming yourself, please contact local emergency services "
    "or someone you trust. In the EU you can dial 112 for emergency assistance."
)


def check_safety(text: str, lang: Lang) -> Optional[str]:
    n = normalize(text)
    patterns = CRISIS_PATTERNS_PL if lang == Lang.PL else CRISIS_PATTERNS_EN
    for pat in patterns:
        if re.search(pat, n):
            return CRISIS_REPLY_PL if lang == Lang.PL else CRISIS_REPLY_EN
    return None


# ------------------ Afirmacje (PL/EN) ------------------
AFFIRMATIONS_PL = [
    "Robisz, co możesz, na miarę swoich sił – to wystarczy.",
    "Twoje emocje mają znaczenie i zasługują na uwagę.",
    "Masz prawo do odpoczynku, nawet jeśli świat mówi inaczej.",
    "Nie musisz być idealna, żeby zasługiwać na miłość i wsparcie.",
    "Każdy krok do przodu, nawet mały, jest postępem.",
    "Masz w sobie więcej siły, niż czasem dostrzegasz.",
    "To, że dziś jest ciężko, nie oznacza, że zawsze tak będzie.",
    "Jesteś ważna i potrzebna – nawet jeśli tak się nie czujesz.",
    "Masz prawo do swoich uczuć – wszystkie są dozwolone.",
    "Możesz zrobić przerwę. To nie rezygnacja – to troska o siebie.",
    "Twoje tempo jest w porządku. Nie musisz się spieszyć.",
    "Zasługujesz na życzliwość – także od samej siebie.",
    "Twoje starania są cenne, nawet jeśli nie widzisz efektów od razu.",
    "Możesz uczyć się na błędach, nie musząc być bezbłędna.",
    "To okej nie czuć się okej.",
    "Masz prawo odpuścić to, co Cię przytłacza.",
    "Nie musisz udowadniać swojej wartości – już ją w sobie masz.",
    "Jesteś bardziej wytrzymała, niż myślisz.",
    "Twoje granice są ważne i zasługują na szacunek.",
    "Masz prawo szukać pomocy i wsparcia.",
    "Twoje uczucia są sygnałem, nie problemem.",
    "Masz w sobie potencjał, który czeka na rozwinięcie.",
    "Każdy dzień jest nową szansą, nie powtórką wczorajszego.",
    "Nawet jeśli zwalniasz, wciąż idziesz do przodu.",
    "Masz wpływ na swoje życie, nawet jeśli czasem tego nie czujesz.",
    "To, że robisz przerwę, oznacza, że dbasz o siebie.",
    "Twoje potrzeby są ważne i zasługują na uwagę.",
    "Jesteś godna miłości, troski i spokoju.",
    "Masz prawo być dumna z nawet najmniejszych rzeczy.",
    "Nawet w trudnych chwilach jesteś dla siebie najlepszym wsparciem.",
]
AFFIRMATIONS_EN = [
    "You are doing the best you can, and that is enough.",
    "Your emotions matter and deserve space.",
    "You have the right to rest, even when life feels busy.",
    "You don’t need to be perfect to be worthy of love and care.",
    "Every small step forward is still progress.",
    "You have more strength inside you than you realize.",
    "A hard day does not define your whole life.",
    "You are important and needed, even if you don’t always feel it.",
    "Your feelings are valid – all of them.",
    "Taking a break is not giving up; it is choosing to care for yourself.",
    "Your pace is okay. You don’t have to rush.",
    "You deserve kindness – especially from yourself.",
    "Your effort matters, even when the results are not visible yet.",
    "It’s okay to make mistakes – that’s how growth happens.",
    "It’s okay to not feel okay.",
    "You have the right to let go of things that overwhelm you.",
    "You don’t need to prove your worth – you already have it.",
    "You are more resilient than you think.",
    "Your boundaries matter and deserve respect.",
    "It’s okay to ask for help and support.",
    "Your feelings are messages, not flaws.",
    "You hold potential within you that is waiting to grow.",
    "Every day is a new chance, not a repeat of the past.",
    "Even when you slow down, you are still moving forward.",
    "You have influence over your life, even if it doesn't feel like it.",
    "Taking rest is a form of strength, not weakness.",
    "Your needs are valid and worth acknowledging.",
    "You are worthy of love, care, and peace.",
    "You can be proud of yourself for even the smallest things.",
    "Even in difficult moments, you can be gentle with yourself.",
]


def _stable_index(key: str, n: int) -> int:
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(h[:8], 16) % n


def get_daily_affirmation_text(user_name: Optional[str], lang: Lang) -> str:
    affs = AFFIRMATIONS_PL if lang == Lang.PL else AFFIRMATIONS_EN
    if not affs:
        return "You are important and your feelings matter."
    today = datetime.utcnow().date().isoformat()
    key = f"{(user_name or '').strip().lower()}|{lang.value}|{today}"
    idx = _stable_index(key, len(affs))
    return affs[idx]


def get_random_affirmation_text(lang: Lang) -> str:
    affs = AFFIRMATIONS_PL if lang == Lang.PL else AFFIRMATIONS_EN
    if not affs:
        return "You are important and your feelings matter."
    return random.choice(affs)


# ------------------ Self-care: przykładowe zadania -------------
SELFCARE_SUGGESTIONS = [
    {"id": "water",
     "text_pl": "Wypij dziś przynajmniej jedną szklankę wody świadomie, skupiając się na smaku i odczuciu w ciele.",
     "text_en": "Drink at least one glass of water mindfully today, paying attention to taste and body sensations."},
    {"id": "walk",
     "text_pl": "Wyjdź na 10-minutowy spacer bez telefonu i zauważ trzy rzeczy, które widzisz, słyszysz i czujesz.",
     "text_en": "Go for a 10-minute walk without your phone and notice three things you see, hear, and feel."},
    {"id": "breath_5",
     "text_pl": "Zrób 5 spokojnych, świadomych oddechów – wdech nosem, wydech ustami.",
     "text_en": "Take 5 slow, mindful breaths – inhale through your nose, exhale through your mouth."},
    {"id": "gratitude_1",
     "text_pl": "Zapisz jedną rzecz, za którą możesz być dziś wdzięczna.",
     "text_en": "Write down one thing you feel grateful for today."},
    {"id": "kind_to_self",
     "text_pl": "Powiedz do siebie jedno życzliwe zdanie, tak jak do przyjaciółki.",
     "text_en": "Say one kind sentence to yourself, as if you were talking to a friend."},
    {"id": "body_check",
     "text_pl": "Zrób krótkie 'skanowanie ciała' – zauważ napięcia od stóp do głowy i spróbuj je delikatnie rozluźnić.",
     "text_en": "Do a short body scan – notice tension from feet to head and gently relax those areas."},
    {"id": "no_social_15",
     "text_pl": "Zrób 15 minut przerwy od social mediów.",
     "text_en": "Take a 15-minute break from social media."},
    {"id": "nice_activity",
     "text_pl": "Zrób jedną małą rzecz, którą lubisz (muzyka, herbata, serial, rysowanie).",
     "text_en": "Do one small thing you enjoy (music, tea, series, drawing)."},
    {"id": "stretch",
     "text_pl": "Poświęć 5 minut na delikatne rozciąganie ciała.",
     "text_en": "Spend 5 minutes gently stretching your body."},
    {"id": "journal_3_lines",
     "text_pl": "Zapisz w notatniku trzy zdania opisujące, jak się dziś czujesz.",
     "text_en": "Write three sentences in a notebook about how you feel today."},
]

# ------------------ Heurystyka emocji (PL/EN) ------------------
EMO_LEXICON_PL = {
    "smut": "sad", "przykro": "sad",
    "zly": "angry", "wsciek": "angry", "zlosc": "angry",
    "strach": "anxious", "boje": "anxious", "lek": "anxious", "stres": "anxious",
    "szczesl": "positive", "rado": "positive",
    "ok": "neutral", "spoko": "neutral",
}
EMO_LEXICON_EN = {
    "sad": "sad", "down": "sad", "depress": "sad",
    "angry": "angry", "mad": "angry", "furious": "angry",
    "anxious": "anxious", "anxiety": "anxious", "stress": "anxious", "worried": "anxious",
    "happy": "positive", "glad": "positive",
    "ok": "neutral", "fine": "neutral",
}


def classify_emotion(text: str, lang: Lang) -> str:
    if check_safety(text, lang):
        return "crisis"
    n = normalize(text)
    lex = EMO_LEXICON_PL if lang == Lang.PL else EMO_LEXICON_EN
    for frag, label in lex.items():
        if frag in n:
            return label
    return "neutral"


# --------------------------- FSM -------------------------------
class State(str, Enum):
    START = "START"
    IDENTIFY_THOUGHT = "IDENTIFY_THOUGHT"
    EVIDENCE_FOR = "EVIDENCE_FOR"
    EVIDENCE_AGAINST = "EVIDENCE_AGAINST"
    REFRAME = "REFRAME"
    SUMMARY = "SUMMARY"
    END = "END"


# ----------------------- Dane sesji (RAM) ----------------------
@dataclass
class JournalEntry:
    role: str
    text: str
    state: State
    emotion: Optional[str] = None


@dataclass
class SessionMem:
    id: str
    user_name: Optional[str] = None
    state: State = State.START
    lang: Lang = Lang.EN
    slots: Dict[str, Any] = field(default_factory=dict)
    journal: List[JournalEntry] = field(default_factory=list)

    def add(self, role: str, text: str, emotion: Optional[str] = None):
        self.journal.append(JournalEntry(role=role, text=text, state=self.state, emotion=emotion))
        try:
            with DBSession(ENGINE) as db:
                db.add(JournalRow(
                    session_id=self.id,
                    role=role,
                    text=text,
                    state=self.state.value,
                    emotion=emotion,
                ))
                db.commit()
        except Exception:
            pass


SESSIONS: Dict[str, SessionMem] = {}

# -------------------------- MODELE DB (CBT) --------------------
class SessionRow(SQLModel, table=True):
    id: str = SQLField(primary_key=True, index=True)
    user_name: Optional[str] = None
    created_at: datetime = SQLField(default_factory=datetime.utcnow)


class JournalRow(SQLModel, table=True):
    id: Optional[int] = SQLField(default=None, primary_key=True)
    session_id: str = SQLField(index=True)
    role: str
    text: str
    state: str
    emotion: Optional[str] = None
    ts: datetime = SQLField(default_factory=datetime.utcnow)

# ---------------------- MODELE DB (Breathing) ------------------
class BreathingProtocolRow(SQLModel, table=True):
    code: str = SQLField(primary_key=True)
    name_en: str
    name_pl: str
    inhale_s: int
    hold_s: int
    exhale_s: int
    cycles: int


class BreathingSessionRow(SQLModel, table=True):
    id: str = SQLField(primary_key=True, index=True)
    user_name: Optional[str] = None
    protocol_code: str
    cycles_target: int
    lang: str = "en"
    started_at: datetime = SQLField(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None


class BreathingEventRow(SQLModel, table=True):
    id: Optional[int] = SQLField(default=None, primary_key=True)
    breathing_session_id: str = SQLField(index=True)
    step: str
    cycle_index: int
    ts: datetime = SQLField(default_factory=datetime.utcnow)

# ---------------------- MODELE DB (Mood diary) ------------------
class MoodEntryRow(SQLModel, table=True):
    id: Optional[int] = SQLField(default=None, primary_key=True)
    user_name: Optional[str] = SQLField(default=None, index=True)
    rating: int
    emotion: str
    note: Optional[str] = None
    created_at: datetime = SQLField(default_factory=datetime.utcnow)

# ---------------------- MODELE DB (Sleep tracker) ---------------
class SleepEntryRow(SQLModel, table=True):
    id: Optional[int] = SQLField(default=None, primary_key=True)
    user_name: Optional[str] = SQLField(default=None, index=True)
    sleep_start: datetime
    sleep_end: datetime
    rating: int
    note: Optional[str] = None
    created_at: datetime = SQLField(default_factory=datetime.utcnow)

# ---------------------- MODELE DB (Self-care) ------------------
class SelfCareTaskRow(SQLModel, table=True):
    id: Optional[int] = SQLField(default=None, primary_key=True)
    user_name: Optional[str] = SQLField(default=None, index=True)
    text: str
    from_suggestion: bool = SQLField(default=False)
    done: bool = SQLField(default=False)
    created_at: datetime = SQLField(default_factory=datetime.utcnow)
    done_at: Optional[datetime] = None

# ---------------------- Inicjalizacja DB -----------------------
ENGINE = create_engine("sqlite:///./cbt.db", echo=False)


def create_db_and_tables():
    SQLModel.metadata.create_all(ENGINE)


def seed_breathing_protocols():
    defaults = [
        dict(code="box", name_en="Box breathing (4-4-4)", name_pl="Oddech pudełkowy (4-4-4)",
             inhale_s=4, hold_s=4, exhale_s=4, cycles=6),
        dict(code="478", name_en="4-7-8 breathing", name_pl="Oddychanie 4-7-8",
             inhale_s=4, hold_s=7, exhale_s=8, cycles=4),
        dict(code="coherent", name_en="Coherent breathing (6-6)", name_pl="Oddech koherentny (6-6)",
             inhale_s=6, hold_s=0, exhale_s=6, cycles=6),
    ]
    with DBSession(ENGINE) as db:
        existing = {p.code for p in db.exec(select(BreathingProtocolRow)).all()}
        for p in defaults:
            if p["code"] not in existing:
                db.add(BreathingProtocolRow(**p))
        db.commit()


@app.on_event("startup")
def on_startup():
    create_db_and_tables()
    seed_breathing_protocols()


# ------------------------ Schemy API (CBT) ---------------------
class StartSessionRequest(BaseModel):
    user_name: Optional[str] = None
    lang: Optional[Lang] = Lang.EN


class StartSessionResponse(BaseModel):
    session_id: str
    message: str
    state: State


class MessageRequest(BaseModel):
    session_id: str
    text: str


class MessageResponse(BaseModel):
    reply: str
    state: State
    emotion: str
    safety_escalted: bool
    slots: Dict[str, Any]


class JournalEntryDTO(BaseModel):
    role: str
    text: str
    state: State
    emotion: Optional[str] = None


class JournalResponse(BaseModel):
    session_id: str
    journal: List[JournalEntryDTO]


# ------------------------ Schemy API (Mood diary) --------------
class MoodEntryCreate(BaseModel):
    user_name: Optional[str] = None
    rating: int = Field(ge=1, le=5)
    emotion: str
    note: Optional[str] = None


class MoodEntryDTO(BaseDTO):
    id: int
    user_name: Optional[str]
    rating: int
    emotion: str
    note: Optional[str]
    created_at: datetime

    class Config:
        orm_mode = True


# ------------------------ Schemy API (Sleep) -------------------
class SleepEntryCreate(BaseModel):
    user_name: Optional[str] = None
    sleep_start: datetime = Field(description="Czas zaśnięcia (ISO 8601)")
    sleep_end: datetime = Field(description="Czas pobudki (ISO 8601)")
    rating: int = Field(ge=1, le=5, description="Jakość snu 1–5")
    note: Optional[str] = None


class SleepEntryDTO(BaseModel):
    id: int
    user_name: Optional[str]
    sleep_start: datetime
    sleep_end: datetime
    sleep_duration_hours: float
    rating: int
    note: Optional[str]
    created_at: datetime

    class Config:
        orm_mode = True


# ------------------------ Schemy API (Self-care) ----------------
class SelfCareTaskCreate(BaseModel):
    user_name: Optional[str] = None
    text: str = Field(min_length=3, description="Treść zadania self-care")
    from_suggestion: bool = Field(default=False)


class SelfCareTaskDTO(BaseDTO):
    id: int
    user_name: Optional[str]
    text: str
    from_suggestion: bool
    done: bool
    created_at: datetime
    done_at: Optional[datetime] = None

    class Config:
        orm_mode = True


# ------------------ Schema Affirmation -------------------------
class AffirmationResponse(BaseModel):
    date: str
    user_name: Optional[str]
    lang: Lang
    mode: str
    text: str


# -------------------- Prompty / i18n ---------------------
def bot_prompt_for_state(session: SessionMem) -> str:
    pl = {
        State.START: f"Cześć{', ' + session.user_name if session.user_name else ''}! "
                     "Jestem Twoją wirtualną przyjaciółką CBT. Opowiedz mi o myśli, która Cię martwi.",
        State.IDENTIFY_THOUGHT: "Jakie są dowody, które potwierdzają tę myśl?",
        State.EVIDENCE_FOR: "A jakie są dowody PRZECIW tej myśli?",
        State.EVIDENCE_AGAINST: "Spróbujmy stworzyć bardziej zrównoważoną myśl.",
        State.REFRAME: "Jak się czujesz po stworzeniu tej nowej myśli?",
        State.SUMMARY: "Dziękuję! Napisz 'koniec' lub 'jeszcze'.",
        State.END: "Dziękuję za rozmowę 💛 Jestem tu, kiedy mnie potrzebujesz.",
    }
    en = {
        State.START: f"Hi{', ' + session.user_name if session.user_name else ''}! Tell me about a thought that troubles you.",
        State.IDENTIFY_THOUGHT: "What evidence supports this thought?",
        State.EVIDENCE_FOR: "What evidence goes AGAINST this thought?",
        State.EVIDENCE_AGAINST: "Let’s create a more balanced version of this thought.",
        State.REFRAME: "How do you feel after creating this new thought?",
        State.SUMMARY: "Thank you! Type 'end' or 'again'.",
        State.END: "Thanks for today 💛 I’m here when you need me.",
    }
    return (pl if session.lang == Lang.PL else en)[session.state]


# -------------------- Logika FSM (fallback) ---------------------
def fsm_step(session: SessionMem, user_text: str) -> str:
    crisis = check_safety(user_text, session.lang)
    if crisis:
        session.add("bot", crisis)
        return crisis

    emotion = classify_emotion(user_text, session.lang)
    session.add("user", user_text, emotion)

    if session.state == State.START:
        session.slots["initial"] = user_text
        session.state = State.IDENTIFY_THOUGHT

    elif session.state == State.IDENTIFY_THOUGHT:
        session.slots["for"] = user_text
        session.state = State.EVIDENCE_FOR

    elif session.state == State.EVIDENCE_FOR:
        session.slots["against"] = user_text
        session.state = State.EVIDENCE_AGAINST

    elif session.state == State.EVIDENCE_AGAINST:
        session.slots["reframe"] = user_text
        session.state = State.REFRAME

    elif session.state == State.REFRAME:
        session.slots["feeling"] = user_text
        session.state = State.SUMMARY

        summary = (
            f"• Myśl: {session.slots.get('initial')}\n"
            f"• Dowody ZA: {session.slots.get('for')}\n"
            f"• Dowody PRZECIW: {session.slots.get('against')}\n"
            f"• Nowa myśl: {session.slots.get('reframe')}\n"
            f"• Samopoczucie teraz: {session.slots.get('feeling')}"
        )
        session.add("bot", summary)
        next_msg = bot_prompt_for_state(session)
        session.add("bot", next_msg)
        return summary + "\n\n" + next_msg

    elif session.state == State.SUMMARY:
        t = user_text.lower().strip()
        if t in ("koniec", "end"):
            session.state = State.END
        elif t in ("jeszcze", "again"):
            session.slots = {}
            session.state = State.START
        else:
            reply = "Napisz 'koniec' lub 'jeszcze'." if session.lang == Lang.PL else "Type 'end' or 'again'."
            session.add("bot", reply)
            return reply

    reply = bot_prompt_for_state(session)
    session.add("bot", reply)
    return reply

# ---------------------- CBT ENDPOINTY ----------------------
@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/session", response_model=StartSessionResponse)
def start_session(req: StartSessionRequest):
    sid = str(uuid.uuid4())
    session = SessionMem(id=sid, user_name=req.user_name, lang=(req.lang or Lang.EN))
    SESSIONS[sid] = session

    with DBSession(ENGINE) as db:
        db.add(SessionRow(id=sid, user_name=req.user_name))
        db.commit()

    msg = bot_prompt_for_state(session)
    session.add("bot", msg)
    return StartSessionResponse(session_id=sid, message=msg, state=session.state)


@app.post("/message", response_model=MessageResponse)
def post_message(req: MessageRequest):
    s = SESSIONS.get(req.session_id)
    if not s:
        raise HTTPException(404, "Nie znaleziono sesji")

    # --- SAFETY ---
    crisis = check_safety(req.text, s.lang)
    if crisis:
        s.add("bot", crisis)
        return MessageResponse(
            reply=crisis,
            state=s.state,
            emotion="crisis",
            safety_escalted=True,
            slots=s.slots,
        )

    # zapisz usera
    emotion = classify_emotion(req.text, s.lang)
    s.add("user", req.text, emotion)

    # --- system prompt ---
    if s.lang == Lang.PL:
        system_prompt = (
            "Jesteś empatycznym asystentem CBT (terapia poznawczo-behawioralna). "
            "Odpowiadasz naturalnie (bez sztucznych formułek), konkretnie i ciepło. "
            "Zadaj maksymalnie jedno krótkie pytanie doprecyzowujące, jeśli brakuje danych. "
            "Dawaj małe kroki CBT (myśli–emocje–zachowania), bez moralizowania. "
            "Nie diagnozuj. Odpowiadasz po polsku."
        )
    else:
        system_prompt = (
            "You are an empathetic CBT assistant. "
            "Write naturally (no canned phrases), be warm and specific. "
            "Ask at most one short clarifying question if needed. "
            "Offer small CBT steps (thoughts–feelings–behaviors) without lecturing. "
            "Do not diagnose. Reply in English."
        )

    # --- historia rozmowy z sesji ---
    def map_role(role: str) -> str:
        return "assistant" if role == "bot" else "user"

    history_msgs: List[Dict[str, str]] = []
    for j in s.journal[-20:]:
        if j.role in ("user", "bot"):
            history_msgs.append({"role": map_role(j.role), "content": j.text})

    messages = [{"role": "system", "content": system_prompt}] + history_msgs

    # --- wywołanie AI: Ollama/OpenAI, a jak padnie to fallback FSM ---
    try:
        reply = call_ai(messages)
        if not reply:
            raise RuntimeError("AI zwróciło pustą odpowiedź.")
    except Exception as e:
        print("AI ERROR:", repr(e))
        # fallback do FSM, żeby app nie była martwa
        reply = fsm_step(s, req.text)

    # zapisz odpowiedź bota
    s.add("bot", reply)

    return MessageResponse(
        reply=reply,
        state=s.state,
        emotion=emotion,
        safety_escalted=False,
        slots=s.slots,
    )


# ---------------------- JOURNAL / EXPORT ----------------------
def _fetch_journal(session_id: str):
    with DBSession(ENGINE) as db:
        return db.exec(
            select(JournalRow).where(JournalRow.session_id == session_id).order_by(JournalRow.ts)
        ).all()


@app.get("/session/{session_id}", response_model=JournalResponse)
def get_session(session_id: str):
    rows = _fetch_journal(session_id)
    if not rows:
        raise HTTPException(404, "Brak sesji")

    return JournalResponse(
        session_id=session_id,
        journal=[
            JournalEntryDTO(
                role=r.role,
                text=r.text,
                state=State(r.state),
                emotion=r.emotion,
            )
            for r in rows
        ],
    )


@app.get("/export/{session_id}")
def export_session(session_id: str, format: str = "csv"):
    rows = _fetch_journal(session_id)
    if not rows:
        raise HTTPException(404, "Brak sesji")

    if format == "json":
        return JSONResponse([
            {
                "session_id": session_id,
                "role": r.role,
                "text": r.text,
                "state": r.state,
                "emotion": r.emotion,
                "timestamp": r.ts.isoformat(),
            }
            for r in rows
        ])

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["session_id", "role", "text", "state", "emotion", "timestamp"])
    for r in rows:
        writer.writerow([
            session_id,
            r.role,
            (r.text or "").replace("\n", " "),
            r.state,
            r.emotion or "",
            r.ts.isoformat(),
        ])
    buffer.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="session_{session_id}.csv"'}
    return StreamingResponse(buffer, media_type="text/csv", headers=headers)


# ===================== BREATHING =======================
class BreathingProtocolDTO(BaseModel):
    code: str
    name_en: str
    name_pl: str
    inhale_s: int
    hold_s: int
    exhale_s: int
    cycles: int


class BreathingStartRequest(BaseModel):
    user_name: Optional[str] = None
    protocol_code: str
    cycles: Optional[int] = None
    lang: Optional[Lang] = Lang.EN


class BreathingStartResponse(BaseModel):
    breathing_session_id: str
    protocol: BreathingProtocolDTO
    plan: Dict[str, int]


class BreathingMarkRequest(BaseModel):
    breathing_session_id: str
    step: str
    cycle_index: int


class BreathingFinishRequest(BaseModel):
    breathing_session_id: str


def _get_protocol(code: str) -> BreathingProtocolRow:
    with DBSession(ENGINE) as db:
        p = db.get(BreathingProtocolRow, code)
        if not p:
            raise HTTPException(404, "Protocol not found")
        return p


def _breathing_events(session_id: str):
    with DBSession(ENGINE) as db:
        return db.exec(
            select(BreathingEventRow)
            .where(BreathingEventRow.breathing_session_id == session_id)
            .order_by(BreathingEventRow.ts)
        ).all()


@app.get("/breathing/protocols", response_model=List[BreathingProtocolDTO])
def list_breathing_protocols():
    with DBSession(ENGINE) as db:
        rows = db.exec(select(BreathingProtocolRow)).all()
        return [BreathingProtocolDTO(**r.__dict__) for r in rows]


@app.post("/breathing/session/start", response_model=BreathingStartResponse)
def start_breathing(req: BreathingStartRequest):
    p = _get_protocol(req.protocol_code)
    sid = str(uuid.uuid4())
    with DBSession(ENGINE) as db:
        db.add(BreathingSessionRow(
            id=sid,
            user_name=req.user_name,
            protocol_code=p.code,
            cycles_target=req.cycles or p.cycles,
            lang=(req.lang or Lang.EN).value,
        ))
        db.commit()

    return BreathingStartResponse(
        breathing_session_id=sid,
        protocol=BreathingProtocolDTO(**p.__dict__),
        plan={"inhale": p.inhale_s, "hold": p.hold_s, "exhale": p.exhale_s, "cycles": req.cycles or p.cycles},
    )


@app.post("/breathing/session/mark")
def mark_breathing(req: BreathingMarkRequest):
    with DBSession(ENGINE) as db:
        sess = db.get(BreathingSessionRow, req.breathing_session_id)
        if not sess:
            raise HTTPException(404, "Breathing session not found")
        db.add(BreathingEventRow(
            breathing_session_id=req.breathing_session_id,
            step=req.step,
            cycle_index=req.cycle_index,
        ))
        db.commit()
    return {"ok": True}


@app.post("/breathing/session/finish")
def finish_breathing(req: BreathingFinishRequest):
    with DBSession(ENGINE) as db:
        sess = db.get(BreathingSessionRow, req.breathing_session_id)
        if not sess:
            raise HTTPException(404)
        if not sess.finished_at:
            sess.finished_at = datetime.utcnow()
            db.add(sess)
            db.commit()
    return {"ok": True}


@app.get("/breathing/session/{sid}")
def get_breathing_session(sid: str):
    with DBSession(ENGINE) as db:
        sess = db.get(BreathingSessionRow, sid)
        if not sess:
            raise HTTPException(404)
    events = _breathing_events(sid)
    return {
        "session": {
            "id": sess.id,
            "user_name": sess.user_name,
            "protocol_code": sess.protocol_code,
            "cycles_target": sess.cycles_target,
            "lang": sess.lang,
            "started_at": sess.started_at.isoformat(),
            "finished_at": sess.finished_at.isoformat() if sess.finished_at else None,
        },
        "events": [
            {"id": e.id, "step": e.step, "cycle": e.cycle_index, "timestamp": e.ts.isoformat()}
            for e in events
        ],
    }


@app.get("/breathing/export/{sid}")
def export_breathing(sid: str, format: str = "csv"):
    with DBSession(ENGINE) as db:
        sess = db.get(BreathingSessionRow, sid)
        if not sess:
            raise HTTPException(404)
    events = _breathing_events(sid)

    if format == "json":
        return JSONResponse([
            {"id": e.id, "step": e.step, "cycle": e.cycle_index, "timestamp": e.ts.isoformat()}
            for e in events
        ])

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "step", "cycle", "timestamp"])
    for e in events:
        writer.writerow([e.id, e.step, e.cycle_index, e.ts.isoformat()])
    buffer.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="breathing_{sid}.csv"'}
    return StreamingResponse(buffer, media_type="text/csv", headers=headers)


# ===================== MOOD ENDPOINTY =======================
@app.post("/mood", response_model=MoodEntryDTO)
def add_mood(entry: MoodEntryCreate):
    with DBSession(ENGINE) as db:
        row = MoodEntryRow(
            user_name=entry.user_name,
            rating=entry.rating,
            emotion=entry.emotion,
            note=entry.note,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return to_dto(MoodEntryDTO, row)

@app.get("/mood", response_model=List[MoodEntryDTO])
def get_moods(user_name: Optional[str] = None):
    with DBSession(ENGINE) as db:
        q = select(MoodEntryRow)
        if user_name:
            q = q.where(MoodEntryRow.user_name == user_name)
        rows = db.exec(q.order_by(MoodEntryRow.created_at.desc())).all()
        return [to_dto(MoodEntryDTO, r) for r in rows]


    

# ===================== SLEEP TRACKER ENDPOINTY =======================
@app.post("/sleep", response_model=SleepEntryDTO)
def add_sleep(entry: SleepEntryCreate):
    if entry.sleep_end <= entry.sleep_start:
        raise HTTPException(status_code=400, detail="sleep_end musi być później niż sleep_start")

    with DBSession(ENGINE) as db:
        row = SleepEntryRow(
            user_name=entry.user_name,
            sleep_start=entry.sleep_start,
            sleep_end=entry.sleep_end,
            rating=entry.rating,
            note=entry.note,
        )
        db.add(row)
        db.commit()
        db.refresh(row)

        duration_hours = (row.sleep_end - row.sleep_start).total_seconds() / 3600.0

        return SleepEntryDTO(
            id=row.id,
            user_name=row.user_name,
            sleep_start=row.sleep_start,
            sleep_end=row.sleep_end,
            sleep_duration_hours=round(duration_hours, 2),
            rating=row.rating,
            note=row.note,
            created_at=row.created_at,
        )


@app.get("/sleep", response_model=List[SleepEntryDTO])
def list_sleep(user_name: Optional[str] = None):
    with DBSession(ENGINE) as db:
        query = select(SleepEntryRow)
        if user_name:
            query = query.where(SleepEntryRow.user_name == user_name)
        rows = db.exec(query.order_by(SleepEntryRow.created_at.desc())).all()

        result: List[SleepEntryDTO] = []
        for r in rows:
            duration_hours = (r.sleep_end - r.sleep_start).total_seconds() / 3600.0
            result.append(SleepEntryDTO(
                id=r.id,
                user_name=r.user_name,
                sleep_start=r.sleep_start,
                sleep_end=r.sleep_end,
                sleep_duration_hours=round(duration_hours, 2),
                rating=r.rating,
                note=r.note,
                created_at=r.created_at,
            ))
        return result


# ===================== WEEKLY REPORT ENDPOINT =======================
@app.get("/report/weekly")
def get_weekly_report(user_name: Optional[str] = None):
    now = datetime.utcnow()
    start = now - timedelta(days=7)

    with DBSession(ENGINE) as db:
        mood_query = select(MoodEntryRow).where(MoodEntryRow.created_at >= start)
        if user_name:
            mood_query = mood_query.where(MoodEntryRow.user_name == user_name)
        mood_rows = db.exec(mood_query).all()

        mood_count = len(mood_rows)
        rating_sum = 0
        rating_counts = {i: 0 for i in range(1, 6)}
        emotion_counts: Dict[str, int] = {}
        for m in mood_rows:
            rating_sum += m.rating
            if m.rating in rating_counts:
                rating_counts[m.rating] += 1
            emo = (m.emotion or "").strip().lower()
            if emo:
                emotion_counts[emo] = emotion_counts.get(emo, 0) + 1
        avg_mood = rating_sum / mood_count if mood_count > 0 else None
        top_emotions = sorted(
            [{"emotion": k, "count": v} for k, v in emotion_counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )

        sleep_query = select(SleepEntryRow).where(SleepEntryRow.sleep_start >= start)
        if user_name:
            sleep_query = sleep_query.where(SleepEntryRow.user_name == user_name)
        sleep_rows = db.exec(sleep_query).all()
        sleep_count = len(sleep_rows)
        total_sleep_hours = 0.0
        for s in sleep_rows:
            total_sleep_hours += (s.sleep_end - s.sleep_start).total_seconds() / 3600.0
        avg_sleep_hours = total_sleep_hours / sleep_count if sleep_count > 0 else None

        cbt_query = select(SessionRow).where(SessionRow.created_at >= start)
        if user_name:
            cbt_query = cbt_query.where(SessionRow.user_name == user_name)
        cbt_count = len(db.exec(cbt_query).all())

        breathing_query = select(BreathingSessionRow).where(BreathingSessionRow.started_at >= start)
        if user_name:
            breathing_query = breathing_query.where(BreathingSessionRow.user_name == user_name)
        breathing_count = len(db.exec(breathing_query).all())

    return {
        "user_name": user_name,
        "from": start.isoformat(),
        "to": now.isoformat(),
        "mood": {
            "entries": mood_count,
            "average_rating": round(avg_mood, 2) if avg_mood is not None else None,
            "rating_counts": rating_counts,
            "top_emotions": top_emotions,
        },
        "sleep": {
            "entries": sleep_count,
            "average_sleep_hours": round(avg_sleep_hours, 2) if avg_sleep_hours is not None else None,
        },
        "cbt_sessions": cbt_count,
        "breathing_sessions": breathing_count,
    }


# ===================== MOOD STATS ENDPOINT =======================
@app.get("/stats/mood")
def get_mood_stats(user_name: Optional[str] = None, days: int = 30):
    now = datetime.utcnow()
    start = now - timedelta(days=days)

    with DBSession(ENGINE) as db:
        q = select(MoodEntryRow).where(MoodEntryRow.created_at >= start)
        if user_name:
            q = q.where(MoodEntryRow.user_name == user_name)
        rows = db.exec(q).all()

    total = len(rows)
    rating_counts = {i: 0 for i in range(1, 6)}
    emotion_counts: Dict[str, int] = {}
    rating_sum = 0

    for m in rows:
        rating_sum += m.rating
        if m.rating in rating_counts:
            rating_counts[m.rating] += 1
        emo = (m.emotion or "").strip().lower()
        if emo:
            emotion_counts[emo] = emotion_counts.get(emo, 0) + 1

    avg_rating = rating_sum / total if total > 0 else None
    top_emotions = sorted(
        [{"emotion": k, "count": v} for k, v in emotion_counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )

    return {
        "user_name": user_name,
        "days": days,
        "from": start.isoformat(),
        "to": now.isoformat(),
        "total_entries": total,
        "average_rating": round(avg_rating, 2) if avg_rating is not None else None,
        "rating_counts": rating_counts,
        "top_emotions": top_emotions,
    }


# ===================== SELF-CARE SUGGESTIONS =======================
@app.get("/selfcare/suggestions")
def get_selfcare_suggestions(lang: Lang = Lang.PL):
    result = []
    for item in SELFCARE_SUGGESTIONS:
        text = item["text_pl"] if lang == Lang.PL else item["text_en"]
        result.append({"id": item["id"], "text": text})
    return result


# ===================== SELF-CARE TASKS ENDPOINTY =======================
@app.post("/selfcare", response_model=SelfCareTaskDTO)
def add_selfcare_task(body: SelfCareTaskCreate):
    with DBSession(ENGINE) as db:
        row = SelfCareTaskRow(
            user_name=body.user_name,
            text=body.text,
            from_suggestion=body.from_suggestion,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return to_dto(SelfCareTaskDTO, row)


@app.get("/selfcare", response_model=List[SelfCareTaskDTO])
def list_selfcare_tasks(user_name: Optional[str] = None, only_active: bool = True):
    with DBSession(ENGINE) as db:
        q = select(SelfCareTaskRow)
        if user_name:
            q = q.where(SelfCareTaskRow.user_name == user_name)
        if only_active:
            q = q.where(SelfCareTaskRow.done == False)  # noqa: E712
        rows = db.exec(q.order_by(SelfCareTaskRow.created_at.desc())).all()
        return [to_dto(SelfCareTaskDTO, r) for r in rows]


@app.post("/selfcare/{task_id}/done", response_model=SelfCareTaskDTO)
def mark_selfcare_done(task_id: int):
    with DBSession(ENGINE) as db:
        row = db.get(SelfCareTaskRow, task_id)
        if not row:
            raise HTTPException(status_code=404, detail="Self-care task not found")
        if not row.done:
            row.done = True
            row.done_at = datetime.utcnow()
            db.add(row)
            db.commit()
            db.refresh(row)
        return to_dto(SelfCareTaskDTO, row)


@app.delete("/selfcare/{task_id}")
def delete_selfcare_task(task_id: int):
    with DBSession(ENGINE) as db:
        row = db.get(SelfCareTaskRow, task_id)
        if not row:
            raise HTTPException(status_code=404, detail="Self-care task not found")
        db.delete(row)
        db.commit()
    return {"ok": True}


# ===================== AFFIRMATION ENDPOINT ===================
@app.get("/affirmation/today", response_model=AffirmationResponse)
def get_affirmation(
    user_name: Optional[str] = None,
    lang: Lang = Lang.PL,
    mode: str = Query("daily", description="daily = afirmacja dnia, random = losowa przy każdym odświeżeniu"),
):
    today = datetime.utcnow().date().isoformat()
    mode_norm = (mode or "daily").strip().lower()

    if mode_norm == "random":
        text = get_random_affirmation_text(lang)
    else:
        text = get_daily_affirmation_text(user_name, lang)
        mode_norm = "daily"

    return AffirmationResponse(
        date=today,
        user_name=user_name,
        lang=lang,
        mode=mode_norm,
        text=text,
    )
