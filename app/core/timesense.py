"""Noção de tempo do Nym: que horas são e quanto tempo passou desde a última conversa.

Funções puras (sem banco). Tudo é calculado a partir dos horários reais gravados nas mensagens:
o Nym não "sente" o tempo passar; ele recebe esse dado a cada resposta e reage a ele.
O fuso é o do computador (`tz=None`), o mesmo usado pela presença e pelo agendador de sonhos.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo

WEEKDAYS = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
MONTHS = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
          "setembro", "outubro", "novembro", "dezembro")

SAME_CONVERSATION = timedelta(minutes=30)   # abaixo disso, é a mesma conversa
MARK_GAP = timedelta(hours=3)               # a partir disso, o histórico ganha a marca "[... depois]"


def parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _safe_parse(value: str | None) -> datetime | None:
    """Horário ilegível (dado antigo ou corrompido) conta como "sem referência": nunca derruba a conversa."""
    if not value:
        return None
    try:
        return parse_time(value)
    except (TypeError, ValueError):
        return None


def part_of_day(hour: int) -> str:
    if 5 <= hour < 12:
        return "manhã"
    if 12 <= hour < 18:
        return "tarde"
    if 18 <= hour < 24:
        return "noite"
    return "madrugada"


def _with_article(part: str) -> str:
    return "de manhã" if part == "manhã" else f"à {part}" if part in ("tarde", "noite") else "de madrugada"


def describe_now(local: datetime) -> str:
    """Ex.: 'sábado, 17 de outubro de 2026, 21:40 (noite)'."""
    return (f"{WEEKDAYS[local.weekday()]}, {local.day} de {MONTHS[local.month - 1]} de {local.year}, "
            f"{local:%H:%M} ({part_of_day(local.hour)})")


def describe_gap(delta: timedelta) -> str:
    """Duração aproximada, do jeito que se fala: 'uns 40 minutos', 'umas 3 horas', '2 dias', 'umas 3 semanas'."""
    minutes = max(0, int(delta.total_seconds() // 60))
    if minutes < 2:
        return "nem um minuto"
    if minutes < 55:
        return f"uns {minutes} minutos"
    hours = round(minutes / 60)
    if hours < 24:
        return "uma hora" if hours == 1 else f"umas {hours} horas"
    days = round(minutes / 1440)
    if days < 14:
        return "um dia" if days == 1 else f"{days} dias"
    if days < 60:
        weeks = round(days / 7)
        return f"umas {weeks} semanas"
    months = round(days / 30)
    return f"uns {months} meses"


def relative_moment(then_local: datetime, now_local: datetime) -> str:
    """Quando foi, em relação a agora: 'hoje de manhã', 'ontem à noite', 'na quarta à tarde', 'em 3 de outubro'."""
    days = (now_local.date() - then_local.date()).days
    part = _with_article(part_of_day(then_local.hour))
    if days <= 0:
        return f"hoje {part}"
    if days == 1:
        return f"ontem {part}"
    if days < 7:
        day = WEEKDAYS[then_local.weekday()]
        prep = "no" if day in ("sábado", "domingo") else "na"
        return f"{prep} {day} {part}"
    return f"em {then_local.day} de {MONTHS[then_local.month - 1]}"


def time_block(now: datetime, previous_at: str | None, tz: tzinfo | None = None) -> str:
    """Bloco para o prompt: hora atual e tempo desde a mensagem ANTERIOR à fala atual."""
    local = now.astimezone(tz)
    lines = [f"Agora é {describe_now(local)}."]
    then = _safe_parse(previous_at)
    if previous_at is None:
        lines.append("Esta é a primeira conversa de vocês.")
    elif then is not None:
        gap = now - then
        if gap < SAME_CONVERSATION:
            lines.append("Vocês estão no meio de uma conversa.")
        else:
            lines.append(f"Antes desta mensagem, vocês tinham se falado pela última vez "
                         f"{relative_moment(then.astimezone(tz), local)}, há {describe_gap(gap)}.")
            lines.append("Reaja a esse tempo como um amigo reagiria (estranhar o sumiço, retomar o assunto, "
                         "notar que é tarde ou cedo), sem anunciar a hora nem fazer conta.")
    return "Tempo: " + " ".join(lines)


def gap_marker(previous_at: str | None, current_at: str) -> str | None:
    """Marca colocada antes de uma fala do usuário no histórico quando ela veio depois de uma pausa longa."""
    then, now = _safe_parse(previous_at), _safe_parse(current_at)
    if then is None or now is None:
        return None
    gap = now - then
    if gap < MARK_GAP:
        return None
    return f"[{describe_gap(gap)} depois]"
