# --- START OF FILE utils.py ---

import os
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

# --- Definição da constante ADMIN_IDS ---
ADMIN_USER_IDS = os.getenv("ADMIN_USER_IDS", "")
ADMIN_IDS = [int(admin_id.strip()) for admin_id in ADMIN_USER_IDS.split(',')] if ADMIN_USER_IDS else []

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


def escape_url(url: str) -> str:
    """
    Escapa caracteres especiais em URLs para Markdown V2.
    No Markdown V2, quando URLs são exibidas como texto puro, caracteres especiais precisam ser escapados.
    """
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        url = url.replace(char, f'\\{char}')
    return url


async def send_access_links(bot: Bot, user_id: int, payment_id: str, access_type: str = 'purchase'):
    """
    Gera e envia links de acesso, com mensagens personalizadas.
    - access_type: 'purchase' (padrão), 'support', ou 'trial'.
    """
    logger.info(f"[JOB][{payment_id}] Iniciando tarefa para enviar links (tipo: {access_type}) ao usuário {user_id}.")

    group_ids = await db.get_all_group_ids()
    if not group_ids:
        logger.error(f"CRÍTICO: Nenhum grupo encontrado no DB para enviar links ao usuário {user_id}.")
        message = escape_markdown("⚠️ Tivemos um problema interno para buscar os grupos. Nossa equipe foi notificada.", version=2)
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
                escaped_title = escape_markdown(chat.title or f"Grupo {chat_id}", version=2)
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
            # CORREÇÃO: Usar formato de link inline do Markdown V2: [texto](url)
            # Isso evita ter que escapar os pontos na URL
            links_to_send_text += f"🔗 *{escaped_title}:* [Clique aqui]({link.invite_link})\n\n"
            new_links_generated += 1
        except Exception as e:
            logger.error(f"[JOB][{payment_id}] Erro ao criar link de convite para o grupo {chat_id}: {e}")
            failed_links += 1

        await asyncio.sleep(0.2)


    # --- CONSTRUÇÃO ROBUSTA DA MENSAGEM ---
    message_parts = []

    if access_type == 'trial':
        header = "🎁 Seu acesso de degustação está liberado!\n\nExplore nossos canais pelos próximos 30 minutos. Aqui estão seus links de acesso:\n\n"
        message_parts.append(escape_markdown(header, version=2))
    elif access_type == 'support':
        header = "Aqui estão o status e os novos links de acesso, se necessário:\n\n"
        message_parts.append(escape_markdown(header, version=2))
    else: # 'purchase'
        header = "🎉 Pagamento confirmado!\n\nSeja bem-vindo(a)! Aqui estão seus links de acesso:\n\n"
        message_parts.append(escape_markdown(header, version=2))

    # Links já escapados corretamente (título escapado, URL pura)
    if links_to_send_text:
        message_parts.append(links_to_send_text)

    if groups_already_in_text:
        message_parts.append(groups_already_in_text)

    if new_links_generated > 0:
        attention_text = escape_markdown("Cada link só pode ser usado ", version=2)
        attention_text_end = escape_markdown(" e expira em breve.", version=2)
        message_parts.append(f"⚠️ *Atenção:* {attention_text}*uma vez*{attention_text_end}\n\n")

        warning_line = escape_markdown("------------------------------------\n", version=2)
        warning_header = "*Aviso importante:*\n"
        warning_body1 = escape_markdown("O Telegram pode bloquear temporariamente novas entradas se você tentar acessar muitos grupos ou canais em pouco tempo — é uma medida automática de segurança contra spam.\n\n", version=2)
        warning_body2_part1 = escape_markdown("👉 Para evitar isso, ", version=2)
        warning_body2_part2 = escape_markdown(", aguarde cerca de 30 minutos e depois continue com os demais.\n\n", version=2)
        warning_body3 = escape_markdown("Se algum link estiver expirado, use o comando /suporte para solicitar novos links.", version=2)

        full_warning = (
            f"{warning_line}"
            f"⚠️ {warning_header}"
            f"{warning_body1}"
            f"{warning_body2_part1}*entre em até 3 canais por vez*{warning_body2_part2}"
            f"{warning_body3}"
        )
        message_parts.append(full_warning)

    if new_links_generated == 0 and access_type == 'support':
        support_footer = "\n\nParece que você já está em todos os nossos grupos! Nenhum link novo foi necessário."
        message_parts.append(escape_markdown(support_footer, version=2))

    if failed_links > 0:
        failed_footer = f"\n\n❌ Não foi possível gerar links para {failed_links} grupo(s). Por favor, contate o suporte se precisar."
        message_parts.append(escape_markdown(failed_footer, version=2))

    final_message = "".join(message_parts)

    try:
        await bot.send_message(chat_id=user_id, text=final_message, parse_mode=ParseMode.MARKDOWN_V2, disable_web_page_preview=True)
        logger.info(f"✅ [JOB][{payment_id}] Mensagem com formatação enviada com sucesso para o usuário {user_id}.")
    except BadRequest as e:
        logger.critical(f"Falha INESPERADA ao enviar msg formatada para {user_id}: {e}. Mensagem: {final_message}")
        plain_text = final_message.replace("*", "").replace("_", "").replace("`", "").replace("\\", "")
        await bot.send_message(chat_id=user_id, text=plain_text, disable_web_page_preview=True)
