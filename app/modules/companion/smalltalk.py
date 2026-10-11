"""
Small talk the Companion answers instantly, with no model call.

"Hi", "thanks", "bye", "how are you" and "who are you" do not need an AI model, a classifier call, tools or the
user's memories. Answering them from a short fixed reply makes the Companion feel immediate and spends nothing.

Matching is deliberately strict: the WHOLE message (ignoring punctuation, emoji and capitals) must be one of these
phrases, optionally followed by a name like "Claude". "Hi, can you plan a trip to Lisbon?" is NOT small talk and goes to
the model as usual. Replies that depend on context ("ok", "yes", "sure") are never matched.

Pure stdlib, no database: unit-testable on its own.
"""
from __future__ import annotations

import random
import re
from typing import Optional

GREETING, HOW_ARE_YOU, THANKS, BYE, ABOUT = "greeting", "how_are_you", "thanks", "bye", "about"

_MAX_WORDS = 7
_ADDRESS = r"(?: (?:there|claude|companion|tourwayva|tour wayva|friend|buddy|everyone|all|team))?"

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (HOW_ARE_YOU, re.compile(
        r"^(?:(?:hi|hello|hey|hola|bonjour|hallo|ciao|olá|ola)(?: there)? )?(?:how are you|how are you doing|how r u|how are u|how do you do"
        r"|how is it going|how s it going|what s up|whats up|wassup|sup|cómo estás|como estas|comment ça va|comment vas tu|wie geht s|come stai|como vai|tudo bem"
        r"|как дела|jak się masz|jak sie masz|你好吗|كيف حالك)" + _ADDRESS + r"$")),
    (GREETING, re.compile(
        r"^(?:hi+|hello+|hey+|heya|hiya|howdy|yo|greetings|good (?:morning|afternoon|evening|day)|morning|evening"
        r"|hola|buenos días|buenos dias|buenas tardes|buenas noches|bonjour|bonsoir|salut|coucou|hallo|guten morgen|guten tag|guten abend"
        r"|ciao|buongiorno|buonasera|olá|ola|oi|bom dia|boa tarde|boa noite|привет|здравствуйте|добрый день|доброе утро"
        r"|cześć|czesc|witaj|dzień dobry|dzien dobry|你好|您好|早上好|مرحبا|مرحباً|اهلا|أهلا|السلام عليكم|sannu|ndewo|bawo|báwo)"
        + _ADDRESS + r"$")),
    (THANKS, re.compile(
        r"^(?:thanks|thank you|thank u|thx|ty|cheers|much appreciated|many thanks|gracias|muchas gracias|merci|merci beaucoup|danke|danke schön|vielen dank"
        r"|grazie|grazie mille|obrigado|obrigada|muito obrigado|muito obrigada|спасибо|большое спасибо|dziękuję|dziekuje|dzięki|dzieki|谢谢|謝謝|شكرا|شكراً|nagode|daalụ|e se)"
        r"(?: (?:a lot|so much|very much|a ton|again|claude|companion|tourwayva|mate|friend))*$")),
    (BYE, re.compile(
        r"^(?:bye+|goodbye|good bye|see you|see ya|cya|later|good night|goodnight|take care|adios|adiós|hasta luego|au revoir|à bientôt|a bientot"
        r"|tschüss|tschuss|auf wiedersehen|arrivederci|tchau|até logo|ate logo|пока|до свидания|do widzenia|na razie|再见|مع السلامة)"
        r"(?: (?:later|soon|tomorrow|there|claude|companion|friend|everyone|all))*$")),
    (ABOUT, re.compile(
        r"^(?:who are you|what are you|what can you do|what do you do|how can you help|how can you help me|what can you help me with|introduce yourself"
        r"|quién eres|qui es tu|qui êtes vous|wer bist du|chi sei|quem é você|кто ты|kim jesteś|你是谁|من أنت)$")),
]

# Short replies, by language then kind. {name} is the person's first name (the "Hi, {name}" form is dropped without one).
_REPLIES: dict[str, dict[str, tuple[str, ...]]] = {
    "en": {
        GREETING: ("Hi{comma_name}! Where would you like to go next?", "Hello{comma_name}! How can I help with your travels today?", "Hey{comma_name}! Ready to plan something? Tell me where you're headed."),
        HOW_ARE_YOU: ("I'm doing great, thanks for asking{comma_name}! Where are we travelling to next?", "All good here{comma_name}! What can I help you plan today?"),
        THANKS: ("You're welcome! Anything else you'd like to plan?", "Happy to help! Let me know if you need anything else for your trip.", "Anytime! I'm here whenever you want to plan more."),
        BYE: ("Goodbye{comma_name}! Safe travels, and come back whenever you want to plan more.", "See you soon{comma_name}! Happy travels."),
        ABOUT: ("I'm your TourWayva travel companion. I can answer travel questions, suggest places and things to do, check weather and currency, "
                "look up hotels and flights, and help you change your itinerary. Just ask.",),
    },
    "es": {
        GREETING: ("¡Hola{comma_name}! ¿A dónde te gustaría ir?", "¡Hola{comma_name}! ¿En qué te ayudo con tu viaje hoy?"),
        HOW_ARE_YOU: ("¡Muy bien, gracias por preguntar{comma_name}! ¿A dónde viajamos?",),
        THANKS: ("¡De nada! ¿Quieres planear algo más?",),
        BYE: ("¡Hasta luego{comma_name}! Buen viaje, y vuelve cuando quieras planear más.",),
        ABOUT: ("Soy tu compañero de viaje de TourWayva. Puedo responder preguntas de viaje, sugerir lugares y actividades, consultar el clima y el cambio de moneda, buscar hoteles y vuelos, y ayudarte a modificar tu itinerario.",),
    },
    "fr": {
        GREETING: ("Bonjour{comma_name} ! Où aimeriez-vous aller ?", "Salut{comma_name} ! Comment puis-je vous aider pour votre voyage ?"),
        HOW_ARE_YOU: ("Très bien, merci{comma_name} ! Où partons-nous ?",),
        THANKS: ("Avec plaisir ! Souhaitez-vous planifier autre chose ?",),
        BYE: ("Au revoir{comma_name} ! Bon voyage, et revenez quand vous voulez.",),
        ABOUT: ("Je suis votre compagnon de voyage TourWayva. Je peux répondre à vos questions, proposer des lieux et des activités, vérifier la météo et les devises, chercher des hôtels et des vols, et modifier votre itinéraire.",),
    },
    "de": {
        GREETING: ("Hallo{comma_name}! Wohin möchtest du reisen?", "Hallo{comma_name}! Wie kann ich dir heute bei deiner Reise helfen?"),
        HOW_ARE_YOU: ("Mir geht es gut, danke{comma_name}! Wohin soll es gehen?",),
        THANKS: ("Gern geschehen! Möchtest du noch etwas planen?",),
        BYE: ("Tschüss{comma_name}! Gute Reise, komm gern wieder.",),
        ABOUT: ("Ich bin dein TourWayva-Reisebegleiter. Ich beantworte Reisefragen, schlage Orte und Aktivitäten vor, prüfe Wetter und Währungen, suche Hotels und Flüge und helfe bei Änderungen an deiner Reiseroute.",),
    },
    "it": {
        GREETING: ("Ciao{comma_name}! Dove ti piacerebbe andare?", "Ciao{comma_name}! Come posso aiutarti con il tuo viaggio?"),
        HOW_ARE_YOU: ("Sto benissimo, grazie{comma_name}! Dove andiamo?",),
        THANKS: ("Prego! Vuoi pianificare qualcos'altro?",),
        BYE: ("Ciao{comma_name}! Buon viaggio, torna quando vuoi.",),
        ABOUT: ("Sono il tuo compagno di viaggio TourWayva. Posso rispondere a domande di viaggio, suggerire luoghi e attività, controllare meteo e valute, cercare hotel e voli e aiutarti a modificare l'itinerario.",),
    },
    "pt": {
        GREETING: ("Olá{comma_name}! Para onde você gostaria de ir?", "Olá{comma_name}! Como posso ajudar na sua viagem hoje?"),
        HOW_ARE_YOU: ("Tudo ótimo, obrigado por perguntar{comma_name}! Para onde vamos?",),
        THANKS: ("De nada! Quer planejar mais alguma coisa?",),
        BYE: ("Tchau{comma_name}! Boa viagem, volte quando quiser.",),
        ABOUT: ("Sou seu companheiro de viagem TourWayva. Posso responder perguntas de viagem, sugerir lugares e atividades, consultar clima e câmbio, buscar hotéis e voos e ajudar a alterar seu roteiro.",),
    },
    "ru": {
        GREETING: ("Привет{comma_name}! Куда бы вы хотели поехать?", "Здравствуйте{comma_name}! Чем помочь с вашим путешествием?"),
        HOW_ARE_YOU: ("Всё отлично, спасибо{comma_name}! Куда отправимся?",),
        THANKS: ("Пожалуйста! Хотите запланировать что-нибудь ещё?",),
        BYE: ("До свидания{comma_name}! Счастливого пути, возвращайтесь в любое время.",),
        ABOUT: ("Я ваш спутник TourWayva. Отвечаю на вопросы о путешествиях, предлагаю места и занятия, проверяю погоду и курсы валют, ищу отели и авиабилеты и помогаю менять маршрут.",),
    },
    "pl": {
        GREETING: ("Cześć{comma_name}! Dokąd chcesz pojechać?", "Cześć{comma_name}! W czym mogę pomóc w Twojej podróży?"),
        HOW_ARE_YOU: ("Świetnie, dziękuję{comma_name}! Dokąd jedziemy?",),
        THANKS: ("Nie ma za co! Chcesz zaplanować coś jeszcze?",),
        BYE: ("Do widzenia{comma_name}! Szerokiej drogi, wróć, kiedy chcesz.",),
        ABOUT: ("Jestem Twoim towarzyszem podróży TourWayva. Odpowiadam na pytania o podróże, podpowiadam miejsca i atrakcje, sprawdzam pogodę i waluty, szukam hoteli i lotów oraz pomagam zmieniać plan.",),
    },
    "zh": {
        GREETING: ("你好{comma_name}！你想去哪里旅行？", "你好{comma_name}！今天需要我帮你做什么旅行计划？"),
        HOW_ARE_YOU: ("我很好，谢谢{comma_name}！我们下一站去哪里？",),
        THANKS: ("不客气！还想规划点什么吗？",),
        BYE: ("再见{comma_name}！旅途愉快，随时欢迎回来。",),
        ABOUT: ("我是你的 TourWayva 旅行伙伴。我可以回答旅行问题、推荐地点和活动、查询天气和汇率、搜索酒店和航班，并帮你修改行程。",),
    },
    "ar": {
        GREETING: ("مرحباً{comma_name}! إلى أين تود أن تسافر؟", "أهلاً{comma_name}! كيف أساعدك في رحلتك اليوم؟"),
        HOW_ARE_YOU: ("أنا بخير، شكراً لسؤالك{comma_name}! إلى أين وجهتنا؟",),
        THANKS: ("على الرحب والسعة! هل تريد التخطيط لشيء آخر؟",),
        BYE: ("مع السلامة{comma_name}! رحلة سعيدة، وعد متى شئت.",),
        ABOUT: ("أنا رفيق سفرك من TourWayva. أجيب عن أسئلة السفر، وأقترح أماكن وأنشطة، وأتحقق من الطقس والعملات، وأبحث عن الفنادق والرحلات، وأساعدك في تعديل خطة رحلتك.",),
    },
}


def _normalize(text: str) -> str:
    lowered = text.lower().replace("’", " ").replace("'", " ")
    cleaned = re.sub(r"[^\w\s]", " ", lowered, flags=re.UNICODE).replace("_", " ")
    return re.sub(r"\s+", " ", cleaned).strip()


def detect_smalltalk(text: Optional[str]) -> Optional[str]:
    """The kind of small talk the whole message is, or None (anything else goes to the model)."""
    if not text or len(text) > 80:
        return None
    norm = _normalize(text)
    if not norm or len(norm.split()) > _MAX_WORDS:
        return None
    for kind, pattern in _PATTERNS:
        if pattern.match(norm):
            return kind
    return None


def smalltalk_reply(kind: str, *, language: Optional[str] = None, first_name: Optional[str] = None) -> str:
    """A short reply in the person's language (English when we have none for it)."""
    lang = (language or "en").split("-")[0].lower()
    table = _REPLIES.get(lang) or _REPLIES["en"]
    template = random.choice(table[kind])
    name = (first_name or "").strip()
    return template.format(comma_name=f", {name}" if name else "")
