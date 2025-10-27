# --- START OF FILE utils.py ---

import logging
import asyncio
from datetime import datetime, timezone, timedelta

from telegram import Bot
from telegram.ext import Application
from telegram.constants import ParseMode

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
        await bot.send_message(chat_id=user_id, text="⚠️ Tivemos um problema interno para buscar os grupos. Nossa equipe foi notificada.")
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
                groups_already_in_text += f"✅ Você já é membro do grupo: *{chat.title}*\n\n"
                continue

            link = await bot.create_chat_invite_link(
                chat_id=chat_id,
                expire_date=expire_date,
                member_limit=1
            )
            chat = await bot.get_chat(chat_id)
            group_title = chat.title or f"Grupo {group_ids.index(chat_id) + 1}"
            links_to_send_text += f"🔗 *{group_title}:* {link.invite_link}\n\n"
            new_links_generated += 1

        except Exception as e:
            if "user not found" in str(e).lower():
                try:
                    link = await bot.create_chat_invite_link(chat_id=chat_id, expire_date=expire_date, member_limit=1)
                    chat = await bot.get_chat(chat_id)
                    group_title = chat.title or f"Grupo {group_ids.index(chat_id) + 1}"
                    links_to_send_text += f"🔗 *{group_title}:* {link.invite_link}\n\n"
                    new_links_generated += 1
                except Exception as inner_e:
                     logger.error(f"[JOB][{payment_id}] Erro interno ao criar link para o grupo {chat_id}: {inner_e}")
                     failed_links += 1
            else:
                logger.error(f"[JOB][{payment_id}] Erro ao verificar membro ou criar link para o grupo {chat_id}: {e}")
                failed_links += 1

        await asyncio.sleep(0.2)

    final_message = ""
    if access_type == 'trial':
        final_message += "🎁 Seu acesso de degustação está liberado!\n\nExplore nossos canais pelos próximos 30 minutos. Aqui estão seus links de acesso:\n\n"
    elif access_type == 'support':
        final_message += "Aqui estão o status e os novos links de acesso, se necessário:\n\n"
    else: # 'purchase' é o padrão
        final_message += "🎉 Pagamento confirmado!\n\nSeja bem-vindo(a)! Aqui estão seus links de acesso:\n\n"

    if links_to_send_text:
        final_message += links_to_send_text

    if groups_already_in_text:
        final_message += groups_already_in_text

    if new_links_generated > 0:
        final_message += "⚠️ **Atenção:** Cada link só pode ser usado **uma vez** e expira em breve.\n\n"

        warning_message = (
            "------------------------------------\n"
            "⚠️ **Aviso importante:**\n"
            "O Telegram pode bloquear temporariamente novas entradas se você tentar acessar "
            "muitos grupos ou canais em pouco tempo — é uma medida automática de segurança contra spam.\n\n"
            "👉 Para evitar isso, **entre em até 3 canais por vez**, aguarde cerca de 30 minutos "
            "e depois continue com os demais.\n\n"
            "Se algum link estiver expirado, use o comando /suporte para solicitar novos links."
        )
        final_message += warning_message

    if new_links_generated == 0 and access_type == 'support':
        final_message += "\nParece que você já está em todos os nossos grupos! Nenhum link novo foi necessário."

    if failed_links > 0:
        final_message += f"\n\n❌ Não foi possível gerar links para {failed_links} grupo(s). Por favor, contate o suporte se precisar."

    await bot.send_message(chat_id=user_id, text=final_message, parse_mode=ParseMode.MARKDOWN)
    logger.info(f"✅ [JOB][{payment_id}] Tarefa de links para o usuário {user_id} concluída. Gerados: {new_links_generated}, Já membro: {len(group_ids) - new_links_generated - failed_links}, Falhas: {failed_links}")
