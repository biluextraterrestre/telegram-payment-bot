# --- START OF FILE utils.py ---

import logging
import asyncio
from datetime import datetime, timezone, timedelta

from telegram import Bot
from telegram.ext import Application
from telegram.constants import ParseMode
from telegram.error import Forbidden, BadRequest
from telegram.helpers import escape_markdown

import db_supabase as db

logger = logging.getLogger(__name__)

# --- Carrega o fuso horário uma vez ---
TIMEZONE_BR = timezone(timedelta(hours=-3))

async def alert_admins(bot: Bot, message: str):
    """Envia uma mensagem de alerta para todos os administradores definidos."""
    if not ADMIN_IDS:
        logger.warning("Nenhum ADMIN_USER_IDS definido. Não é possível enviar alertas.")
        return

    full_message = f"🚨 *ALERTA DO SISTEMA* 🚨\n\n{message}"

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=full_message, parse_mode=ParseMode.MARKDOWN)
            await asyncio.sleep(0.1) # Pequeno delay para evitar rate limit
        except (Forbidden, BadRequest) as e:
            logger.error(f"Falha ao enviar alerta para o admin {admin_id}: {e}")
        except Exception as e:
            logger.error(f"Erro inesperado ao enviar alerta para o admin {admin_id}: {e}", exc_info=True)

def format_date_br(dt: datetime | str | None) -> str:
    """Formata data para o padrão brasileiro."""
    if not dt:
        return "N/A"
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    return dt.astimezone(TIMEZONE_BR).strftime('%d/%m/%Y às %H:%M')


async def send_access_links(bot: Bot, user_id: int, payment_id: str, access_type: str = 'purchase'):
    """
    Gera e envia links de acesso, com mensagens personalizadas.
    - access_type: 'purchase' (padrão), 'support', ou 'trial'.
    """
    logger.info(f"[JOB][{payment_id}] Iniciando tarefa para enviar links (tipo: {access_type}) ao usuário {user_id}.")

    group_ids = await db.get_all_group_ids()
    if not group_ids:
        logger.error(f"CRÍTICO: Nenhum grupo encontrado no DB para enviar links ao usuário {user_id}.")
        message = "⚠️ Tivemos um problema interno para buscar os grupos\\. Nossa equipe foi notificada\\."
        await bot.send_message(chat_id=user_id, text=message, parse_mode=ParseMode.MARKDOWN_V2)
        return

    links_to_send_text = ""
    groups_already_in_text = ""
    failed_links = 0
    new_links_generated = 0
    expire_date = datetime.now(timezone.utc) + timedelta(hours=2)

    for chat_id in group_ids:
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status in ['member', 'administrator', 'creator']:
                chat = await bot.get_chat(chat_id)
                escaped_title = escape_markdown(chat.title, version=2)
                groups_already_in_text += f"✅ Você já é membro do grupo: *{escaped_title}*\n\n"
                continue
        except BadRequest as e:
            if "user not found" not in str(e).lower():
                logger.error(f"[JOB][{payment_id}] Erro ao verificar membro no grupo {chat_id}: {e}")
                failed_links += 1
                continue
        except Exception as e:
            logger.error(f"[JOB][{payment_id}] Erro inesperado ao verificar membro no grupo {chat_id}: {e}")
            failed_links += 1
            continue

        try:
            link = await bot.create_chat_invite_link(
                chat_id=chat_id,
                expire_date=expire_date,
                member_limit=1
            )
            chat = await bot.get_chat(chat_id)
            group_title = chat.title or f"Grupo {group_ids.index(chat_id) + 1}"
            escaped_title = escape_markdown(group_title, version=2)
            links_to_send_text += f"🔗 *{escaped_title}:* {link.invite_link}\n\n"
            new_links_generated += 1
        except Exception as e:
            logger.error(f"[JOB][{payment_id}] Erro ao criar link de convite para o grupo {chat_id}: {e}")
            failed_links += 1

        await asyncio.sleep(0.2)


    # --- CORREÇÃO DEFINITIVA: Montando a mensagem com escape manual e cuidadoso ---
    message_parts = []

    if access_type == 'trial':
        message_parts.append("🎁 Seu acesso de degustação está liberado\\!\n\nExplore nossos canais pelos próximos 30 minutos\\. Aqui estão seus links de acesso:\n\n")
    elif access_type == 'support':
        message_parts.append("Aqui estão o status e os novos links de acesso, se necessário:\n\n")
    else: # 'purchase' é o padrão
        message_parts.append("🎉 Pagamento confirmado\\!\n\nSeja bem\\-vindo\\(a\\)\\! Aqui estão seus links de acesso:\n\n")

    if links_to_send_text:
        message_parts.append(links_to_send_text)

    if groups_already_in_text:
        message_parts.append(groups_already_in_text)

    if new_links_generated > 0:
        message_parts.append(f"⚠️ *Atenção:* Cada link só pode ser usado *uma vez* e expira em breve\\.\n\n")

        # Mensagem de aviso com todos os caracteres especiais escapados manualmente
        warning_message = (
            "\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\n"
            "⚠️ *Aviso importante:*\n"
            "O Telegram pode bloquear temporariamente novas entradas se você tentar acessar "
            "muitos grupos ou canais em pouco tempo \\— é uma medida automática de segurança contra spam\\.\n\n"
            "👉 Para evitar isso, *entre em até 3 canais por vez*, aguarde cerca de 30 minutos "
            "e depois continue com os demais\\.\n\n"
            "Se algum link estiver expirado, use o comando /suporte para solicitar novos links\\."
        )
        message_parts.append(warning_message)

    if new_links_generated == 0 and access_type == 'support':
        message_parts.append("\nParece que você já está em todos os nossos grupos\\! Nenhum link novo foi necessário\\.")

    if failed_links > 0:
        message_parts.append(f"\n\n❌ Não foi possível gerar links para {failed_links} grupo\\(s\\)\\. Por favor, contate o suporte se precisar\\.")

    final_message = "".join(message_parts)

    try:
        await bot.send_message(chat_id=user_id, text=final_message, parse_mode=ParseMode.MARKDOWN_V2, disable_web_page_preview=True)
        logger.info(f"✅ [JOB][{payment_id}] Mensagem com formatação enviada com sucesso para o usuário {user_id}.")
    except BadRequest as e:
        logger.error(f"Falha CRÍTICA ao enviar mensagem final para {user_id} com MARKDOWN_V2: {e}. Enviando como texto plano.")
        # Fallback melhorado: remove formatação E os caracteres de escape
        plain_text = final_message.replace("*", "").replace("_", "").replace("`", "").replace("\\", "")
        await bot.send_message(chat_id=user_id, text=plain_text, disable_web_page_preview=True)
