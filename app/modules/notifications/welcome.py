"""Welcome message for a newly activated account: one in-app notification and one email.

Sent exactly once per activation, from the two places an account becomes usable (email OTP verification and Google
sign-up completion). It is strictly best effort: any failure is logged and swallowed so it can never block sign-up.
The text is localized for the user's language (falls back to English); everything user-supplied is HTML-escaped.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from app.core.config import settings
from app.core.constants import NotificationType
from app.core.logging import get_logger
from app.db.models.user import User
from app.utils.html import esc

logger = get_logger(__name__)

EMAIL_TIMEOUT_SECONDS = 6

# language -> (title, greeting, line 1, line 2, button label). `{name}` is the only placeholder.
WELCOME_COPY: dict[str, tuple[str, str, str, str, str]] = {
    "en": ("Welcome to TourWayva", "Welcome aboard, {name}!", "Your account is ready. TourWayva helps you discover places that fit your budget, plan complete trips with AI and travel with a Companion that knows your plans.", "A good first step: tell us where you want to go and when, and we'll build your first itinerary.", "Start planning"),
    "es": ("Te damos la bienvenida a TourWayva", "¡Bienvenido a bordo, {name}!", "Tu cuenta está lista. TourWayva te ayuda a descubrir lugares que se ajusten a tu presupuesto, planear viajes completos con IA y viajar con un Compañero que conoce tus planes.", "Un buen primer paso: dinos adónde quieres ir y cuándo, y crearemos tu primer itinerario.", "Empezar a planear"),
    "fr": ("Bienvenue sur TourWayva", "Bienvenue à bord, {name} !", "Votre compte est prêt. TourWayva vous aide à découvrir des lieux adaptés à votre budget, à planifier des voyages complets avec l'IA et à voyager avec un Compagnon qui connaît vos plans.", "Un bon premier pas : dites-nous où et quand vous voulez partir, et nous créerons votre premier itinéraire.", "Commencer à planifier"),
    "it": ("Benvenuto su TourWayva", "Benvenuto a bordo, {name}!", "Il tuo account è pronto. TourWayva ti aiuta a scoprire luoghi adatti al tuo budget, pianificare viaggi completi con l'IA e viaggiare con un Compagno che conosce i tuoi piani.", "Un buon primo passo: dicci dove vuoi andare e quando, e creeremo il tuo primo itinerario.", "Inizia a pianificare"),
    "pt": ("Bem-vindo ao TourWayva", "Bem-vindo a bordo, {name}!", "Sua conta está pronta. O TourWayva ajuda você a descobrir lugares que cabem no seu orçamento, planejar viagens completas com IA e viajar com um Companheiro que conhece seus planos.", "Um bom primeiro passo: diga para onde quer ir e quando, e criaremos seu primeiro roteiro.", "Começar a planejar"),
    "de": ("Willkommen bei TourWayva", "Willkommen an Bord, {name}!", "Dein Konto ist bereit. TourWayva hilft dir, Orte zu entdecken, die zu deinem Budget passen, komplette Reisen mit KI zu planen und mit einem Begleiter zu reisen, der deine Pläne kennt.", "Ein guter erster Schritt: Sag uns, wohin und wann du reisen möchtest, und wir erstellen deine erste Route.", "Planung starten"),
    "ru": ("Добро пожаловать в TourWayva", "Добро пожаловать, {name}!", "Ваш аккаунт готов. TourWayva помогает находить места по вашему бюджету, планировать поездки с ИИ и путешествовать с Компаньоном, который знает ваши планы.", "Хороший первый шаг: расскажите, куда и когда вы хотите поехать, и мы составим ваш первый маршрут.", "Начать планирование"),
    "pl": ("Witaj w TourWayva", "Witaj na pokładzie, {name}!", "Twoje konto jest gotowe. TourWayva pomaga odkrywać miejsca dopasowane do budżetu, planować pełne podróże z AI i podróżować z Towarzyszem, który zna Twoje plany.", "Dobry pierwszy krok: powiedz, dokąd i kiedy chcesz jechać, a stworzymy Twój pierwszy plan.", "Zacznij planować"),
    "zh": ("欢迎来到 TourWayva", "欢迎加入，{name}！", "你的账户已就绪。TourWayva 帮你发现符合预算的地方、用 AI 规划完整行程，并由了解你计划的旅行伙伴陪你出行。", "不错的第一步：告诉我们想去哪里、什么时候去，我们会为你生成第一份行程。", "开始规划"),
    "ar": ("مرحبًا بك في TourWayva", "أهلًا بك، {name}!", "حسابك جاهز. يساعدك TourWayva على اكتشاف أماكن تناسب ميزانيتك وتخطيط رحلات كاملة بالذكاء الاصطناعي والسفر برفقة مرافق يعرف خططك.", "خطوة أولى جيدة: أخبرنا إلى أين تريد الذهاب ومتى، وسنبني لك أول برنامج رحلة.", "ابدأ التخطيط"),
}
_RTL = {"ar"}


def _copy(user: User) -> tuple[str, str, str, str, str, str]:
    lang = (user.language or "en").split("-")[0].lower()
    title, greeting, l1, l2, button = WELCOME_COPY.get(lang, WELCOME_COPY["en"])
    name = (user.first_name or "").strip() or "traveler"
    return title, greeting.replace("{name}", name), l1, l2, button, lang


def welcome_text(user: User) -> tuple[str, str]:
    """(title, plain-text body) for the in-app notification."""
    title, greeting, l1, l2, _button, _lang = _copy(user)
    return title, f"{greeting}\n\n{l1}\n\n{l2}"


def welcome_html(user: User, app_url: Optional[str] = None) -> str:
    """Branded, table-free-enough HTML for the email. Every dynamic value is escaped."""
    title, greeting, l1, l2, button, lang = _copy(user)
    url = (app_url or "").strip().rstrip("/")
    safe_url = url if url.startswith(("https://", "http://")) else ""
    direction = "rtl" if lang in _RTL else "ltr"
    cta = (
        f'<p style="margin:28px 0 0"><a href="{esc(safe_url)}/app/home" style="display:inline-block;background:#FE6B35;color:#ffffff;'
        f'text-decoration:none;font-weight:700;padding:13px 26px;border-radius:10px">{esc(button)}</a></p>'
        if safe_url else ""
    )
    return (
        f'<div dir="{direction}" style="font-family:Inter,Segoe UI,Arial,sans-serif;max-width:560px;margin:0 auto;padding:28px 24px;color:#1b1b1f;line-height:1.55">'
        f'<p style="font-size:13px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#FE6B35;margin:0 0 14px">TourWayva</p>'
        f'<h1 style="font-size:24px;line-height:1.25;margin:0 0 14px">{esc(greeting)}</h1>'
        f'<p style="margin:0 0 12px">{esc(l1)}</p><p style="margin:0">{esc(l2)}</p>{cta}'
        f'<p style="margin:32px 0 0;font-size:12px;color:#6b6b73">{esc(title)}</p></div>'
    )


async def send_welcome(db, user: User) -> None:
    """Create the welcome notification and send the welcome email. Never raises."""
    try:
        from app.modules.notifications.service import NotificationService  # local: avoids an import cycle at module load

        title, body = welcome_text(user)
        service = NotificationService(db)
        # In-app row first (the source of truth); the email is sent separately with the branded template.
        await service.notify(user_id=user.id, notification_type=NotificationType.SYSTEM_ANNOUNCEMENT, title=title, body=body, send_email=False)
        try:
            await asyncio.wait_for(
                service.email_provider.send_transactional_email(
                    to_email=user.email, subject=title, html_content=welcome_html(user, settings.FRONTEND_URL), category="welcome",
                ),
                timeout=EMAIL_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - the notification already exists; a slow/failed email must not matter
            logger.warning("welcome_email_failed", user_id=str(user.id), error=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("welcome_notification_failed", user_id=str(user.id), error=str(exc))
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
