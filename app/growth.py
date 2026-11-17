"""Customers (phase 13): leads, the approval queue, the suppression list, limits and switches.

Everything that could annoy or harm a person is decided HERE, in code, before any message can go:
who may be contacted, who never again, how many per day, at which hours, whether a click is needed.
"""

import re
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select

from app.config import get_settings
from app.db import session as db
from app.db.models import (
    GrowthState,
    InboundMessage,
    Lead,
    Metric,
    OutreachItem,
    Suppression,
)

CONSENTS = ("optin", "inbound", "business")
CLASSES = ("interested", "question", "objection", "unsubscribe", "complaint", "bounce", "other")
FOLLOWUPS_MAX = 2
FOLLOWUP_GAP_DAYS = 3
ONE_ADDRESS = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
FREE_MAIL = frozenset(
    {
        "gmail.com", "googlemail.com", "hotmail.com", "hotmail.it", "outlook.com", "outlook.it",
        "live.com", "live.it", "yahoo.com", "yahoo.it", "libero.it", "virgilio.it", "tiscali.it",
        "icloud.com", "me.com", "aol.com", "alice.it", "tin.it", "fastwebnet.it", "proton.me",
        "protonmail.com", "gmx.com", "gmx.net", "pec.it",
    }
)  # fmt: skip
UNSUB = re.compile(
    r"\b(stop|disiscriv\w*|cancellami|cancellatemi|non (?:voglio|desidero) (?:più )?ricev\w*|"
    r"non (?:contattarmi|scrivermi)(?: più)?|rimuov\w+ (?:dalla|dal) (?:lista|elenco)|"
    r"unsubscribe|remove me|do not contact)\b",
    re.I,
)
FOOTER = (
    "\n\n--\n{sender}\nMessaggio scritto con l'aiuto di un assistente AI e inviato da {sender}. "
    "Se non vuoi ricevere altri messaggi rispondi con la parola STOP e non ti scriverò più."
)


class Blocked(Exception):
    """A message may not go out now; the text says why."""

    def __init__(self, reason: str, final: bool = False) -> None:
        super().__init__(reason)
        self.reason, self.final = reason, final  # final: it will never go (suppressed, ...)


def _now() -> datetime:
    return datetime.now(UTC)


def norm(email: str) -> str:
    return email.strip().lower()


def is_personal_address(email: str) -> bool:
    """A free-mail address is treated as a private person: no cold contact."""
    return norm(email).rsplit("@", 1)[-1] in FREE_MAIL


def looks_like_unsubscribe(text: str) -> bool:
    return bool(UNSUB.search(text or ""))


# ---------- switches ----------


async def get_state(key: str, default: str = "") -> str:
    async with db.session_maker()() as s:
        row = await s.get(GrowthState, key)
        return row.value if row else default


async def set_state(key: str, value: str) -> None:
    async with db.session_maker()() as s:
        row = await s.get(GrowthState, key)
        if row is None:
            s.add(GrowthState(key=key, value=value))
        else:
            row.value = value
        await s.commit()


async def is_paused() -> bool:
    return (await get_state("paused", "0")) == "1"


async def set_paused(paused: bool) -> None:
    await set_state("paused", "1" if paused else "0")


async def level(channel: str = "email") -> int:
    """0 = drafts only, 1 = every message needs the user's click (default), 2 = limited autonomy."""
    return int(await get_state(f"level:{channel}", "1"))


async def set_level(value: int, channel: str = "email") -> None:
    if value not in (0, 1, 2):
        raise ValueError("il livello è 0, 1 o 2")
    await set_state(f"level:{channel}", str(value))


# ---------- suppression ----------


async def suppress(email: str, reason: str) -> None:
    async with db.session_maker()() as s:
        key = norm(email)
        if await s.get(Suppression, key) is None:
            s.add(Suppression(email=key, reason=reason[:100]))
        for lead in (
            await s.execute(select(Lead).where(func.lower(Lead.contact) == key))
        ).scalars():
            lead.status = "unsubscribed"
        await s.commit()


async def is_suppressed(email: str) -> bool:
    async with db.session_maker()() as s:
        return await s.get(Suppression, norm(email)) is not None


# ---------- leads ----------


async def get_lead(lead_id: int) -> Lead:
    async with db.session_maker()() as s:
        lead = await s.get(Lead, lead_id)
    if lead is None:
        raise ValueError(f"Nessun contatto con id {lead_id}")
    return lead


async def add_lead(
    contact: str,
    source_url: str,
    legal_basis: str,
    consent: str,
    name: str = "",
    opportunity_id: int | None = None,
) -> Lead:
    """A contact needs its source and the legal basis: without them it is refused."""
    contact = norm(contact)
    if not ONE_ADDRESS.match(contact):
        raise ValueError("serve un indirizzo email valido")
    web = source_url.lower().startswith(("http://", "https://"))
    if not web and not (consent == "inbound" and source_url.startswith("mailto:")):
        raise ValueError("serve la fonte: l'indirizzo web dove è stato trovato il contatto")
    if len(legal_basis.strip()) < 10:
        raise ValueError("serve la base giuridica del contatto, scritta per esteso")
    if consent not in CONSENTS:
        raise ValueError(f"consenso non valido: {consent} (validi: {', '.join(CONSENTS)})")
    if consent == "business" and is_personal_address(contact):
        raise ValueError("indirizzo personale: senza consenso (optin) non si contattano i privati")
    if await is_suppressed(contact):
        raise ValueError("questo indirizzo è nell'elenco di esclusione: non si contatta")
    if consent == "inbound" and not await _wrote_to_us(contact):
        raise ValueError(
            "«inbound» vale solo per chi ci ha scritto davvero: nessun messaggio ricevuto"
        )
    async with db.session_maker()() as s:
        found = (
            await s.execute(select(Lead).where(func.lower(Lead.contact) == contact))
        ).scalar_one_or_none()
        if found is not None:
            raise ValueError(f"contatto già presente (#{found.id})")
        lead = Lead(
            contact=contact, source_url=source_url, legal_basis=legal_basis, consent=consent,
            name=name, opportunity_id=opportunity_id,
        )  # fmt: skip
        s.add(lead)
        await s.commit()
        lid = lead.id
    return await get_lead(lid)


async def _wrote_to_us(contact: str) -> bool:
    async with db.session_maker()() as s:
        q = (
            select(func.count())
            .select_from(InboundMessage)
            .where(func.lower(InboundMessage.from_addr) == norm(contact))
        )
        return int((await s.execute(q)).scalar() or 0) > 0


async def forget_lead(lead_id: int) -> str:
    """The right to be forgotten: remove the contact and never write to the address again."""
    lead = await get_lead(lead_id)
    async with db.session_maker()() as s:
        await s.execute(delete(OutreachItem).where(OutreachItem.lead_id == lead_id))
        for m in (
            await s.execute(select(InboundMessage).where(InboundMessage.lead_id == lead_id))
        ).scalars():
            m.lead_id, m.from_addr, m.body, m.summary, m.proposed_reply = (
                None,
                "(cancellato)",
                "",
                "",
                "",
            )
        await s.execute(delete(Lead).where(Lead.id == lead_id))
        await s.commit()
    await suppress(lead.contact, "cancellato su richiesta")
    return lead.contact


# ---------- the queue ----------


async def queue(
    lead_id: int, subject: str, body: str, kind: str = "cold", thread_id: str | None = None
) -> OutreachItem:
    """Put a message in the queue. Nothing is sent: it waits for the user."""
    if kind not in ("cold", "followup", "reply"):
        raise ValueError("kind: cold, followup o reply")
    if "\n" in subject or "\r" in subject or not subject.strip():
        raise ValueError("l'oggetto è una riga sola")
    if not 20 <= len(body.strip()) <= 2000:
        raise ValueError("il testo deve avere tra 20 e 2000 caratteri")
    lead = await get_lead(lead_id)
    if lead.status == "unsubscribed" or await is_suppressed(lead.contact):
        raise ValueError("contatto escluso: non si scrive mai più a questo indirizzo")
    if kind == "cold":
        if lead.consent == "inbound":
            raise ValueError(
                "ha scritto lui per primo: è una risposta (kind reply), non un contatto a freddo"
            )
        if is_personal_address(lead.contact) and lead.consent != "optin":
            raise ValueError("indirizzo personale: i contatti a freddo ai privati sono rifiutati")
        if lead.status != "new":
            raise ValueError("questo contatto è già stato contattato: al massimo un seguito")
    elif kind == "followup":
        sent = await _sent_count(lead_id)
        if sent == 0:
            raise ValueError("nessun messaggio precedente: un seguito richiede un primo contatto")
        if sent > FOLLOWUPS_MAX:
            raise ValueError(f"al massimo {FOLLOWUPS_MAX} seguiti per contatto")
        last = lead.last_contact_at
        if last is not None:
            last = last if last.tzinfo else last.replace(tzinfo=UTC)
            if _now() - last < timedelta(days=FOLLOWUP_GAP_DAYS):
                raise ValueError(
                    f"un seguito solo dopo {FOLLOWUP_GAP_DAYS} giorni dall'ultimo messaggio"
                )
    async with db.session_maker()() as s:
        item = OutreachItem(
            lead_id=lead_id,
            kind=kind,
            subject=subject.strip(),
            body=body.strip(),
            thread_id=thread_id,
        )
        s.add(item)
        await s.commit()
        iid = item.id
    return await get_item(iid)


async def _sent_count(lead_id: int) -> int:
    async with db.session_maker()() as s:
        q = (
            select(func.count())
            .select_from(OutreachItem)
            .where(OutreachItem.lead_id == lead_id, OutreachItem.status == "sent")
        )
        return int((await s.execute(q)).scalar() or 0)


async def get_item(item_id: int) -> OutreachItem:
    async with db.session_maker()() as s:
        item = await s.get(OutreachItem, item_id)
    if item is None:
        raise ValueError(f"Nessun messaggio in coda con id {item_id}")
    return item


async def pending_items(limit: int = 50) -> list[OutreachItem]:
    async with db.session_maker()() as s:
        q = (
            select(OutreachItem).where(OutreachItem.status == "proposed")
            .order_by(OutreachItem.id).limit(limit)
        )  # fmt: skip
        return list((await s.execute(q)).scalars())


async def update_item(item_id: int, body: str) -> OutreachItem:
    item = await get_item(item_id)
    if item.status != "proposed":
        raise ValueError(f"il messaggio #{item_id} non è più modificabile ({item.status})")
    if not 20 <= len(body.strip()) <= 2000:
        raise ValueError("il testo deve avere tra 20 e 2000 caratteri")
    async with db.session_maker()() as s:
        row = await s.get(OutreachItem, item_id)
        row.body = body.strip()
        await s.commit()
    return await get_item(item_id)


async def skip_item(item_id: int) -> None:
    async with db.session_maker()() as s:
        row = await s.get(OutreachItem, item_id)
        if row is None or row.status != "proposed":
            raise ValueError("messaggio non trovato o già gestito")
        row.status = "skipped"
        await s.commit()


# ---------- the rules before a message goes out ----------


def in_window(now: datetime, hours: str, tz: ZoneInfo) -> bool:
    start, _, end = hours.partition("-")
    return int(start) <= now.astimezone(tz).hour < int(end)


async def _count_today(kind_filter: Callable[[Any], Any], now: datetime, tz: ZoneInfo) -> int:
    local = now.astimezone(tz)
    start = datetime.combine(local.date(), datetime.min.time(), tzinfo=tz).astimezone(UTC)
    async with db.session_maker()() as s:
        q = (
            select(func.count())
            .select_from(OutreachItem)
            .where(
                OutreachItem.status == "sent",
                OutreachItem.sent_at >= start,
                kind_filter(OutreachItem),
            )
        )
        return int((await s.execute(q)).scalar() or 0)


async def check_can_send(
    item: OutreachItem, lead: Lead, actor: str, now: datetime | None = None
) -> None:
    """Raise Blocked if this message may not go out now. `actor` is 'human' (the user clicked) or
    'auto' (level 2, no click)."""
    now = now or _now()
    s = get_settings()
    tz = ZoneInfo(s.report_timezone)
    if await is_paused():
        raise Blocked("invii fermati con /stop: scrivi /riprendi")
    if not s.outreach_sender_name.strip():
        raise Blocked("manca OUTREACH_SENDER_NAME: i messaggi devono avere il tuo nome")
    if await is_suppressed(lead.contact) or lead.status == "unsubscribed":
        raise Blocked("contatto nell'elenco di esclusione: non si scrive mai più", final=True)
    lvl = await level()
    if lvl == 0:
        raise Blocked("canale al livello 0: solo bozze, nessun invio")
    if item.kind == "cold" and (is_personal_address(lead.contact) and lead.consent != "optin"):
        raise Blocked("contatto a freddo a un privato: rifiutato", final=True)
    if actor == "auto":
        if lvl < 2:
            raise Blocked("livello 1: serve il tuo clic")
        if item.kind == "cold":
            raise Blocked("nessun invio autonomo a freddo", final=True)
        if item.kind == "followup" and lead.consent != "optin":
            raise Blocked(
                "l'invio autonomo di seguiti vale solo per chi ha dato il consenso", final=True
            )
        if len(item.body) > 800:
            raise Blocked("testo troppo lungo per l'invio autonomo (oltre 800 caratteri)")
        if re.search(r"https?://", item.body):
            raise Blocked("nessun link nell'invio autonomo")
        if await _count_today(lambda m: m.kind != "cold", now, tz) >= s.reply_daily_cap:
            raise Blocked(f"tetto giornaliero di risposte autonome raggiunto ({s.reply_daily_cap})")
    if not in_window(now, s.outreach_hours, tz):
        raise Blocked(f"fuori dall'orario di invio ({s.outreach_hours})")
    if (
        item.kind == "cold"
        and await _count_today(lambda m: m.kind == "cold", now, tz) >= s.outreach_daily_cap
    ):
        raise Blocked(f"tetto giornaliero raggiunto ({s.outreach_daily_cap}): domani")


def compose(item: OutreachItem) -> str:
    """The text that really goes out: the body plus who writes, that an AI helped, how to stop."""
    sender = get_settings().outreach_sender_name.strip()
    return item.body.rstrip() + FOOTER.format(sender=sender)


SendFn = Callable[[str, str, str, str | None], Awaitable[dict[str, Any]]]


async def send_item(
    item_id: int, actor: str, send_fn: SendFn, now: datetime | None = None
) -> dict[str, Any]:
    """Send one queued message after every check. A blocked one is reported, and when the block is
    final (suppressed, private address) it is closed for good."""
    item = await get_item(item_id)
    if item.status != "proposed":
        raise ValueError(f"il messaggio #{item_id} è già {item.status}")
    lead = await get_lead(item.lead_id)
    try:
        await check_can_send(item, lead, actor, now)
    except Blocked as b:
        if b.final:
            async with db.session_maker()() as s:
                row = await s.get(OutreachItem, item_id)
                row.status, row.blocked_reason = "blocked", b.reason[:300]
                await s.commit()
        raise
    sent = await send_fn(lead.contact, item.subject, compose(item), item.thread_id)
    moment = now or _now()  # the same clock the checks used (daily caps count from it)
    async with db.session_maker()() as s:
        row = await s.get(OutreachItem, item_id)
        row.status, row.sent_at = "sent", moment
        lrow = await s.get(Lead, lead.id)
        lrow.last_contact_at = moment
        if item.kind == "cold":
            lrow.status = "contacted"
        await s.commit()
    return {"item_id": item_id, "to": lead.contact, "message_id": sent.get("id"), "actor": actor}


# ---------- health of the channel ----------

COMPLAINTS_MAX = 2
COMPLAINT_RATE_MAX = 0.05


async def health(now: datetime | None = None) -> str | None:
    """If complaints or bounces are too many, an autonomous channel goes back to level 1.
    Returns the message to tell the user, or None."""
    since = (now or _now()) - timedelta(days=30)
    async with db.session_maker()() as s:
        sent = int(
            (
                await s.execute(
                    select(func.count())
                    .select_from(OutreachItem)
                    .where(OutreachItem.status == "sent", OutreachItem.sent_at >= since)
                )
            ).scalar()
            or 0
        )
        bad = int(
            (
                await s.execute(
                    select(func.count())
                    .select_from(InboundMessage)
                    .where(
                        InboundMessage.classification.in_(("complaint", "bounce")),
                        InboundMessage.created_at >= since,
                    )
                )
            ).scalar()
            or 0
        )
    too_many = bad >= COMPLAINTS_MAX or (sent >= 10 and bad / sent >= COMPLAINT_RATE_MAX)
    if too_many and await level() == 2:
        await set_level(1)
        return (
            f"⚠️ Troppi reclami o rimbalzi ({bad} su {sent} invii negli ultimi 30 giorni): "
            "l'invio autonomo è tornato al livello 1, serve di nuovo il tuo clic."
        )
    return None


# ---------- metrics ----------


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


async def record_metric(
    name: str, value: float, source: str, opportunity_id: int | None = None, day: date | None = None
) -> Metric:
    """A number with its source: without a source it is not recorded."""
    if not source.strip():
        raise ValueError("serve la fonte del numero (per esempio: modulo, statistiche, a mano)")
    if not name.strip():
        raise ValueError("serve il nome della voce")
    async with db.session_maker()() as s:
        row = Metric(
            name=name.strip()[:60], value=float(value), source=source.strip()[:100],
            opportunity_id=opportunity_id, week=monday_of(day or _now().date()),
        )  # fmt: skip
        s.add(row)
        await s.commit()
        rid = row.id
    async with db.session_maker()() as s:
        return await s.get(Metric, rid)  # type: ignore[return-value]


async def list_metrics(opportunity_id: int | None = None, weeks: int = 4) -> list[Metric]:
    since = monday_of(_now().date()) - timedelta(weeks=weeks - 1)
    async with db.session_maker()() as s:
        q = (
            select(Metric)
            .where(Metric.week >= since)
            .order_by(Metric.week.desc(), Metric.id.desc())
        )
        if opportunity_id is not None:
            q = q.where(Metric.opportunity_id == opportunity_id)
        return list((await s.execute(q)).scalars())


# ---------- inbound ----------


async def store_inbound(
    gmail_id: str, thread_id: str, from_addr: str, subject: str, body: str
) -> tuple[InboundMessage, bool]:
    """Save a received message once. A request to stop, or a mail from a mailer daemon, is
    handled by rules here, before any model sees it."""
    async with db.session_maker()() as s:
        found = (
            await s.execute(select(InboundMessage).where(InboundMessage.gmail_id == gmail_id))
        ).scalar_one_or_none()
        if found is not None:
            return found, False
        addr = norm(re.sub(r".*<([^>]+)>.*", r"\1", from_addr))
        lead = (
            await s.execute(select(Lead).where(func.lower(Lead.contact) == addr))
        ).scalar_one_or_none()
        cls = "new"
        if re.search(r"mailer-daemon|postmaster", addr):
            cls = "bounce"
        elif looks_like_unsubscribe(subject + " " + body[:600]):
            cls = "unsubscribe"
        row = InboundMessage(
            gmail_id=gmail_id, thread_id=thread_id, from_addr=addr[:200], subject=subject[:300],
            body=body[:4000], classification=cls, lead_id=lead.id if lead else None,
            handled=cls in ("unsubscribe", "bounce"),
        )  # fmt: skip
        s.add(row)
        await s.commit()
        rid = row.id
    if cls == "unsubscribe":
        await suppress(addr, "ha chiesto di non essere contattato")
    async with db.session_maker()() as s:
        return (await s.get(InboundMessage, rid)), True  # type: ignore[return-value]


async def set_triage(
    message_id: int, classification: str, summary: str, proposed_reply: str = ""
) -> InboundMessage:
    if classification not in CLASSES:
        raise ValueError(
            f"classificazione non valida: {classification} (valide: {', '.join(CLASSES)})"
        )
    async with db.session_maker()() as s:
        row = await s.get(InboundMessage, message_id)
        if row is None:
            raise ValueError(f"Nessun messaggio ricevuto con id {message_id}")
        row.classification, row.summary = classification, summary[:1000]
        row.proposed_reply = proposed_reply[:2000]
        addr = row.from_addr
        await s.commit()
    if classification in ("unsubscribe", "complaint"):
        await suppress(addr, "disiscrizione" if classification == "unsubscribe" else "reclamo")
    async with db.session_maker()() as s:
        return await s.get(InboundMessage, message_id)  # type: ignore[return-value]


async def inbound_by_id(message_id: int) -> InboundMessage:
    async with db.session_maker()() as s:
        row = await s.get(InboundMessage, message_id)
    if row is None:
        raise ValueError(f"Nessun messaggio ricevuto con id {message_id}")
    return row


async def reply_to_message(
    message_id: int, body: str, send_fn: SendFn, actor: str = "human"
) -> dict:
    """Answer a received message. A stop request or a bounce is never answered. Someone who wrote
    first becomes a lead whose legal basis is exactly that."""
    msg = await inbound_by_id(message_id)
    if msg.classification in ("unsubscribe", "bounce"):
        raise ValueError("a questo messaggio non si risponde (disiscrizione o rimbalzo)")
    text = (body or msg.proposed_reply).strip()
    if len(text) < 20:
        raise ValueError("manca il testo della risposta (almeno 20 caratteri)")
    lead_id = msg.lead_id
    if lead_id is None:
        lead = await add_lead(
            msg.from_addr,
            "mailto:ricevuta",
            "ha scritto per primo alla casella dedicata",
            "inbound",
        )
        lead_id = lead.id
    subject = msg.subject if msg.subject.lower().startswith("re:") else f"Re: {msg.subject}"
    item = await queue(lead_id, subject, text, "reply", msg.thread_id or None)
    sent = await send_item(item.id, actor, send_fn)
    async with db.session_maker()() as s:
        row = await s.get(InboundMessage, message_id)
        row.handled, row.lead_id = True, lead_id
        await s.commit()
    return sent


async def overview() -> str:
    """A few lines about the state of the customers workspace, for the advisor's prompt."""
    async with db.session_maker()() as s:
        leads = int((await s.execute(select(func.count()).select_from(Lead))).scalar() or 0)
        queued = int(
            (
                await s.execute(
                    select(func.count())
                    .select_from(OutreachItem)
                    .where(OutreachItem.status == "proposed")
                )
            ).scalar()
            or 0
        )
        unread = int(
            (
                await s.execute(
                    select(func.count())
                    .select_from(InboundMessage)
                    .where(InboundMessage.handled.is_(False))
                )
            ).scalar()
            or 0
        )
    paused = "FERMI (/stop attivo)" if await is_paused() else "attivi"
    return (
        f"Customers state (data): outbound {paused}; autonomy level {await level()}; "
        f"{leads} contacts; {queued} messages waiting for approval; {unread} received "
        "messages not handled."
    )
